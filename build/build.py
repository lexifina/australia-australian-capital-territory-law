"""Build the Australian Capital Territory law repository from the ACT Legislation Register (its retrieved responses,
read by actcache.py).

Every republication (R1, R2, ...) of every Act in force is one snapshot; the changes between two snapshots are
committed on the day the later one took effect, one commit per amending law, credited by the endnote amendment history
of the later republication. Reads only the cache; writes with git fast-import.

    python -X utf8 build.py --today 2026-10-28 [--repo PATH] [--only 2004-5,...] [--no-docs] [--verify-rebuild]
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import hashlib
import io
import json
import os
import pickle
import re
import shutil
import subprocess
import sys
from pathlib import Path

import actdoc
import actcache

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2] / 'australia-australian-capital-territory-law-build'
MANIFEST = HERE / 'cache' / 'manifest.json'
PARSED = HERE / 'cache' / 'parsed'
COMMITTER = ('Lexifina', 'australia-australian-capital-territory-law@lexifina.com')
PUBLISHER = ("ACT Parliamentary Counsel's Office (ACT Legislation Register)", 'register@legislation.act.invalid')
LEGISLATION_ACT = 'A2001-14'
HOST = actcache.HOST


# ---------------------------------------------------------------------------------------------------- time zone
def _sun(y: int, m: int, last: bool = False, on_or_after: int | None = None) -> dt.date:
    if last:
        d = (dt.date(y, m + 1, 1) if m < 12 else dt.date(y + 1, 1, 1)) - dt.timedelta(days=1)
        return d - dt.timedelta(days=(d.weekday() + 1) % 7)
    d = dt.date(y, m, on_or_after or 1)
    return d + dt.timedelta(days=(6 - d.weekday()) % 7)


def canberra_offset(day: dt.date) -> int:
    """Hours ahead of UTC at noon on a day in Canberra (hard-coded; no tz database on this machine).
    AEST is UTC+10. Daylight saving (UTC+11) in the ACT follows New South Wales (tz database Australia/Sydney, 'AN'
    rules) and changes at 2:00 standard time, so noon on a change day already has the new offset. Starts: 1971-1985 last
    Sunday in October; 1986 19 October; 1987-1999 last Sunday in October; 2000 last Sunday in August; 2001-2007 last
    Sunday in October; since 2008 first Sunday in October. Ends: 1972 27 February; 1973-1981 first Sunday in March;
    1982-1983 first Sunday in April; 1984-1985 first Sunday in March; 1986-1989 Sunday on or after 15 March; 1990-1995
    first Sunday in March; 1996-2005 last Sunday in March; 2006 first Sunday in April; 2007 last Sunday in March; since
    2008 first Sunday in April. The test is start <= day, or day < end."""
    y = day.year
    if y < 1971:
        return 10
    if y == 1971:
        end = None
    elif y == 1972:
        end = dt.date(1972, 2, 27)
    elif y <= 1981:
        end = _sun(y, 3)
    elif y <= 1983:
        end = _sun(y, 4)
    elif y <= 1985:
        end = _sun(y, 3)
    elif y <= 1989:
        end = _sun(y, 3, on_or_after=15)
    elif y <= 1995:
        end = _sun(y, 3)
    elif y <= 2005:
        end = _sun(y, 3, last=True)
    elif y == 2006:
        end = _sun(y, 4)
    elif y == 2007:
        end = _sun(y, 3, last=True)
    else:
        end = _sun(y, 4)
    if y == 1986:
        start = dt.date(1986, 10, 19)
    elif y == 2000:
        start = _sun(y, 8, last=True)
    elif y <= 2007:
        start = _sun(y, 10, last=True)
    else:
        start = _sun(y, 10)
    if end is not None and day < end:
        return 11
    return 11 if day >= start else 10


def commit_date(day: str, seq: list[int]) -> str:
    d = dt.date.fromisoformat(day)
    if d.year < 1970:
        seq[0] += 1
        return f'{seq[0]} +0000'
    off = canberra_offset(d)
    stamp = int(dt.datetime(d.year, d.month, d.day, 12, tzinfo=dt.timezone(dt.timedelta(hours=off))).timestamp())
    return f'{stamp} +{off:02d}00'


# ------------------------------------------------------------------------------------------------------ helpers
def slugify(title: str) -> str:
    s = title.lower().replace('&', 'and')
    s = re.sub(r"[\u2019']", '', s)
    s = re.sub(r'[^a-z0-9]+', '-', s).strip('-')
    return s


def law_key(lid: str) -> tuple:
    m = re.match(r'([A-Za-z]+)(\d{4})-(\d+)', lid)
    return (m.group(1), int(m.group(2)), int(m.group(3))) if m else (lid, 0, 0)


def rep_no(label: str) -> int | None:
    m = re.match(r'R(\d+)', label)
    return int(m.group(1)) if m else None


def tag_label(label: str) -> str:
    return re.sub(r'[^A-Za-z0-9]+', '-', label).strip('-')


def doc_url(v: dict) -> str | None:
    u = v.get('docx') or v.get('rtf')
    return HOST + u if u else None


def parse_cached(url: str) -> dict | None:
    body = actcache.cached(url)
    if body is None or body == actcache.NOT_FOUND:
        return None
    h = hashlib.sha256(body).hexdigest()
    p = PARSED / f'{h[:40]}.v{actdoc.PARSER_VERSION}.pkl'
    if p.exists():
        return pickle.loads(p.read_bytes())
    try:
        r = actdoc.parse(body)
    except Exception as exc:     # a damaged file: recorded, the version is skipped
        r = {'error': repr(exc)}
    r['sha256'] = h
    PARSED.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix('.tmp')
    tmp.write_bytes(pickle.dumps(r))
    os.replace(tmp, p)
    return r


# ------------------------------------------------------------------------------------------------- the plan
def act_versions(page: dict, today: str) -> list[dict]:
    vs = []
    for v in page['versions']:
        if v['label'].lower().startswith('as notified') or not v.get('from'):
            continue
        if not doc_url(v):
            continue
        if v['from'] > today:
            continue
        vs.append(v)
    vs.sort(key=lambda v: (v['from'], v.get('published') or '', 'RI' in v['label'], v['label']))
    return vs


def plan_act(aid: str, page: dict, parsed: dict[str, dict], names: dict[str, str], slug: str) -> tuple[list, list, list]:
    """Returns (commits, tags, problems). A commit is a dict with day, group sort key, author, message, files."""
    title = page['title']
    lid = f'A{aid}'
    vs = [v for v in act_versions(page, '9999-12-31') if doc_url(v) in parsed]
    commits, tags, problems = [], [], []
    prev_files: dict[str, str] = {}
    amend = page.get('amendments') or []
    first = True
    prev_from = None
    for vi, v in enumerate(vs):
        r = parsed[doc_url(v)]
        if 'error' in r or not any(k.startswith('s-') for k in r['files']):
            problems.append({'act': lid, 'version': v['label'], 'from': v['from'], 'problem': r.get('error') or 'no sections found'})
            continue
        files = r['files']
        url = doc_url(v)
        rn = rep_no(v['label'])
        common = [f'Republication: {v["label"]} (effective {v["from"]}{" to " + v["to"] if v.get("to") else ""})',
                  f'Source: {url}']
        if v.get('published') and v['published'] != v['from']:
            common.append(f'Published: {v["published"]}')
        if first:
            first = False
            notes = ' '.join(v['notes'])
            changed = sorted(files)
            if 'new act' in notes.lower() or 'new law' in notes.lower():
                author = (f'{title} ({lid})', f'{lid.lower()}@legislation.act.invalid')
                subj = f'{title}: the Act as first republished ({v["label"]})'
                body = (f'The text of republication {v["label"]}, the first republication of the {title} on the ACT '
                        f'legislation register ("{notes}"), in force from {v["from"]}.')
                kind = 'new law'
                extra = [f'Law: {lid}']
            else:
                author = PUBLISHER
                subj = f'{title}: first republication on the register ({v["label"]}, baseline)'
                body = (f'The earliest republication of the {title} held on the ACT legislation register in Word form '
                        f'({v["label"]}, in force from {v["from"]}). It is a starting point: the changes made before it '
                        f'are not separated out, so it is credited to no law.')
                kind = 'baseline'
                extra = []
            commits.append({'day': v['from'], 'act': aid, 'order': (vi, 0), 'author': author,
                            'subject': subj, 'body': body,
                            'trailers': [f'In-force: {v["from"]}', f'Kind: {kind}', *extra, *common],
                            'files': {f'{slug}/{k}': files[k] for k in changed}})
            prev_files = dict(files)
            prev_from = v['from']
            tags.append((f'{slug}/{tag_label(v["label"])}', aid, vi, v['from']))
            continue
        changed = sorted(k for k in set(files) | set(prev_files) if files.get(k) != prev_files.get(k))
        notes = ' '.join(v['notes'])
        retro = re.search(r'retrospective amendments? by (.*)', notes)
        if retro and 'RI' in v['label']:
            cands = sorted({x for x in actdoc.row_laws(retro.group(1))} - {lid}, key=law_key)
            reason = 'retro'
        else:
            cands = set(x for x in v['acts'] if x != lid)
            for a in amend:
                if a.get('date') and prev_from and prev_from < a['date'] <= v['from']:
                    cands.add(a['id'] if a['id'].startswith(('A', 'SL', 'Ord')) else f'A{a["id"]}')
            cands = sorted(cands - {lid}, key=law_key)
            reason = 'normal'
        cands = [c for c in cands if c.startswith(('A', 'SL', 'Ord'))]
        rows: dict[str, list[str]] = {}
        for prov, text in r['history']:
            fs = actdoc.prov_files(prov)
            for f in fs or []:
                rows.setdefault(f, []).append(text)
        groups: dict[tuple, list[str]] = {}
        evid: dict[tuple, str] = {}
        for k in changed:
            texts = rows.get(k, [])
            named = sorted({x for t in texts for x in actdoc.row_laws(t)} & set(cands), key=law_key)
            ed = any(rn is not None and rn in actdoc.row_editorial(t) for t in texts)
            if len(named) == 1:
                g = ('law', named[0])
            elif len(named) > 1:
                g = ('several', tuple(named))
            elif len(cands) == 1:
                g = ('law', cands[0])
                evid[g] = (f'the endnote amendment history of {v["label"]} names no law for some of these provisions; '
                           f'the register names only {cands[0]} for this republication')
            elif ed:
                g = ('editorial', rn)
            elif re.search(r'expir', notes, re.I) and not cands:
                g = ('expiry', None)
            elif re.search(r'commenced provisions?|commencement of', notes, re.I) and not cands:
                g = ('law', lid)
                evid[g] = f'register note "{notes}": provisions of the Act itself commenced'
            elif files.get(k) is None and any('LA s 89' in t for t in texts):
                g = ('law', LEGISLATION_ACT)
                evid[g] = 'endnote amendment history: omitted under the Legislation Act 2001, s 89 (provisions that are spent)'
            else:
                g = ('unexplained', tuple(cands))
            groups.setdefault(g, []).append(k)
        if not changed:
            tags.append((f'{slug}/{tag_label(v["label"])}', aid, vi, v['from']))
            prev_from = v['from']
            continue
        order_rank = {'law': 0, 'several': 1, 'editorial': 2, 'expiry': 3, 'unexplained': 4}
        for g in sorted(groups, key=lambda g: (order_rank[g[0]], law_key(g[1]) if g[0] == 'law' else (str(g[1]),))):
            ks = groups[g]
            aff = [f'Affecting: {title} {label_of(k)}' for k in ks]
            if g[0] == 'law':
                lname = names.get(g[1]) or g[1]
                author = (f'{lname} ({g[1]})', f'{g[1].lower()}@legislation.act.invalid')
                subj = f'{title}: amended by {lname}'
                body = (f'Republication {v["label"]} of the {title} (in force from {v["from"]}) prints these changes; '
                        f'its endnote amendment history credits them to {lname} ({g[1]}).')
                if reason == 'retro':
                    body += (f' {v["label"]} is a reissue for retrospective amendments: the change is dated the day it is '
                             f'taken to have commenced.')
                tr = [f'In-force: {v["from"]}', f'Law: {g[1]}']
                if g in evid:
                    tr.append(f'Evidence: {evid[g]}')
                kind = 'amendment'
            elif g[0] == 'several':
                author = PUBLISHER
                subj = f'{title}: changes two or more laws could have made ({v["label"]})'
                body = (f'Republication {v["label"]} (in force from {v["from"]}) prints these changes, and the endnote '
                        f'amendment history names more than one law that took effect then for these provisions, so they '
                        f'are credited to none of them.')
                tr = [f'In-force: {v["from"]}', *[f'Candidate: {c}' for c in g[1]]]
                kind = 'two or more laws'
            elif g[0] == 'editorial':
                author = PUBLISHER
                subj = f'{title}: editorial changes ({v["label"]})'
                body = (f'Republication {v["label"]} records editorial amendments made by the Parliamentary Counsel under '
                        f'the Legislation Act 2001, part 11.3 (endnote "R{g[1]} LA"). No Act made them.')
                tr = [f'In-force: {v["from"]}']
                kind = 'editorial change'
            elif g[0] == 'expiry':
                author = PUBLISHER
                subj = f'{title}: provisions expired ({v["label"]})'
                body = (f'Republication {v["label"]} ("{notes}") drops provisions that expired under their own terms. '
                        f'No amending law made the change.')
                tr = [f'In-force: {v["from"]}', f'Law: {lid}']
                kind = 'expiry'
            else:
                author = PUBLISHER
                subj = f'{title}: changes no single law explains ({v["label"]})'
                body = (f'Republication {v["label"]} (in force from {v["from"]}; "{notes}") prints these changes, but '
                        f'its endnote amendment history does not tie them to exactly one law that took effect then.')
                tr = [f'In-force: {v["from"]}', *[f'Candidate: {c}' for c in g[1]]]
                kind = 'no law named'
            commits.append({'day': v['from'], 'act': aid, 'order': (vi, 1, order_rank[g[0]], str(g[1])), 'author': author,
                            'subject': subj, 'body': body,
                            'trailers': [*tr, f'Kind: {kind}', *common, *aff],
                            'files': {f'{slug}/{k}': files.get(k) for k in ks}})
        prev_files = dict(files)
        prev_from = v['from']
        tags.append((f'{slug}/{tag_label(v["label"])}', aid, vi, v['from']))
    return commits, tags, problems


SUBJECT_MAX = 72


def fit_subject(c: dict, title: str, names: dict[str, str]) -> str:
    """At most 72 characters (the body keeps the full names): the amending law by its register number instead of its
    name, then the Act's title cut at a word with an ellipsis."""
    s = c['subject']
    if len(s) <= SUBJECT_MAX:
        return s
    law = next((t[5:] for t in c['trailers'] if t.startswith('Law: ')), None)
    if law and names.get(law) and names[law] in s:
        s = s.replace(names[law], law)
    if len(s) <= SUBJECT_MAX:
        return s
    rest = s[len(title):] if s.startswith(title) else ''
    room = SUBJECT_MAX - len(rest) - 1
    if rest and room >= 12:
        cut = title[:room].rsplit(' ', 1)[0].rstrip(' ,(-')
        return cut + '…' + rest
    return s[:SUBJECT_MAX - 1].rsplit(' ', 1)[0] + '…'


def label_of(name: str) -> str:
    n = name[:-3]
    if n.startswith('s-'):
        m = re.match(r's-0*(\d+)(.*)', n)
        return f's {m.group(1)}{m.group(2)}'
    if n.startswith('sch-'):
        return f'sch {n[4:]}'
    return {'intro': 'title and preamble', 'structure': 'headings', 'dictionary': 'dictionary', 'sch': 'schedule'}.get(n, n)


# ---------------------------------------------------------------------------------------------------- git
def git(repo: Path, *args: str, env: dict | None = None, input: bytes | None = None) -> str:
    out = subprocess.run(['git', *args], cwd=repo, capture_output=True, env=env, input=input)
    if out.returncode:
        raise SystemExit(f'git {" ".join(args)} failed: {out.stderr.decode("utf-8", "replace")}')
    return out.stdout.decode('utf-8')


def write_repo(repo: Path, commits: list[dict], tags: list, today: str, branch: str = 'main') -> dict:
    remote = None
    if (repo / '.git').exists():
        out = subprocess.run(['git', 'remote', 'get-url', 'origin'], cwd=repo, capture_output=True)
        remote = out.stdout.decode().strip() or None

    def force(fn, path, _exc):
        os.chmod(path, 0o700)
        fn(path)
    if repo.exists():
        for p in repo.iterdir():
            shutil.rmtree(p, onexc=force) if p.is_dir() else p.unlink()
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, 'init', '-q', '-b', branch)
    git(repo, 'config', 'core.autocrlf', 'false')
    if remote:
        git(repo, 'remote', 'add', 'origin', remote)
    out = io.BytesIO()

    def data(b: bytes):
        out.write(b'data %d\n' % len(b))
        out.write(b)
        out.write(b'\n')
    seq = [0]
    mark = 0
    # the mark of each act's last commit for each of its republications (by sequence: two republications can share a
    # day, e.g. R9 and its later reissue R9 (RI), and each tag must point at its own text)
    act_ver_mark: dict[tuple, int] = {}
    for c in commits:
        when = commit_date(c['day'], seq)
        mark += 1
        out.write(f'commit refs/heads/{branch}\nmark :{mark}\n'.encode())
        out.write(f'author {c["author"][0]} <{c["author"][1]}> {when}\n'.encode('utf-8'))
        out.write(f'committer {COMMITTER[0]} <{COMMITTER[1]}> {when}\n'.encode('utf-8'))
        msg = c['subject'] + '\n\n' + c['body'] + '\n\n' + '\n'.join(c['trailers']) + '\n'
        data(msg.replace('\r', '').encode('utf-8'))
        for path, text in sorted(c['files'].items()):
            if text is None:
                out.write(b'D ' + path.encode('utf-8') + b'\n')
            else:
                out.write(b'M 100644 inline ' + path.encode('utf-8') + b'\n')
                data(text.encode('utf-8'))
        out.write(b'\n')
        act_ver_mark[(c['act'], c['order'][0])] = mark
    per_act: dict[str, list] = {}
    for (a, k), mm in act_ver_mark.items():
        per_act.setdefault(a, []).append((k, mm))
    tag_marks = {}
    for name, aid, vi, day in tags:
        if day > today:
            continue
        # a republication with no text change of its own: the act's last commit at or before it
        ms = [mm for k, mm in per_act.get(aid, ()) if k <= vi]
        if ms:
            tag_marks[name] = max(ms)
    for name, m in sorted(tag_marks.items()):
        out.write(f'reset refs/tags/{name}\nfrom :{m}\n\n'.encode('utf-8'))
    out.write(b'done\n')
    marks_file = repo / '.git' / 'lexifina-marks'
    r = subprocess.run(['git', 'fast-import', '--quiet', '--done', f'--export-marks={marks_file}'], cwd=repo,
                       input=out.getvalue(), capture_output=True)
    if r.returncode:
        raise SystemExit(f'git fast-import failed: {r.stderr.decode(errors="replace")}')
    marks_file.unlink()
    git(repo, 'reset', '-q', '--hard', branch)
    return tag_marks


# ---------------------------------------------------------------------------------------------------- main
def collect(only: list[str], today: str) -> tuple[list[str], dict, dict]:
    man = json.loads(MANIFEST.read_text(encoding='utf-8'))
    acts = man['acts']
    def complete(aid):     # at least one republication in effect by today, and every Word version in the cache
        p = acts.get(aid)    # (an Act with only its 'As notified' text, e.g. an amending Act, has nothing to build)
        vs = act_versions(p, today) if p and p.get('status') == 'In Force' and p.get('versions') else []
        return bool(vs) and all(actcache.cached(doc_url(v)) is not None for v in vs)
    ids = only or sorted((a for a in acts if complete(a)), key=lambda a: law_key('A' + a))
    urls = []
    for aid in ids:
        for v in act_versions(acts[aid], today):
            u = doc_url(v)
            if actcache.cached(u) is not None:
                urls.append(u)
    return ids, acts, urls


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--today', required=True)
    ap.add_argument('--repo', default=str(REPO))
    ap.add_argument('--only', default='')
    ap.add_argument('--branch', default='main')
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--no-docs', action='store_true')
    ap.add_argument('--offline', action='store_true', help='kept for the shared command line; the build never fetches')
    ap.add_argument('--manifest', default=None,
                    help='a copy of cache/manifest.json to build from (the fetch rewrites the live one as it runs)')
    a = ap.parse_args()
    if a.manifest:
        global MANIFEST
        MANIFEST = Path(a.manifest)
    today = a.today
    only = [x.strip().lstrip('A') for x in a.only.split(',') if x.strip()]
    ids, acts, urls = collect(only, today)
    parsed: dict[str, dict] = {}
    with cf.ProcessPoolExecutor(max_workers=a.workers) as ex:
        for u, r in zip(urls, ex.map(parse_cached, urls, chunksize=4)):
            if r is not None:
                parsed[u] = r
    # law names: amendment tables, endnote legislation histories and the pages themselves
    names: dict[str, str] = {LEGISLATION_ACT: 'Legislation Act 2001'}
    for aid, p in acts.items():
        if p and p.get('title'):
            names[f'A{aid}'] = p['title']
    for r in parsed.values():
        for k, t in (r.get('laws') or {}).items():
            names.setdefault(k, t)
    for aid in ids:
        for am in acts[aid].get('amendments') or []:
            k = am['id'] if am['id'][:1].isalpha() else f'A{am["id"]}'
            if am.get('title'):
                names[k] = am['title']
    slugs: dict[str, str] = {}
    used: set[str] = set()
    for aid in ids:
        s = slugify(acts[aid]['title'])
        if s in used:
            s = f'{s}-a{aid}'
        used.add(s)
        slugs[aid] = s
    allc, alltags, problems, done = [], [], [], []
    for aid in ids:
        c, t, pr = plan_act(aid, acts[aid], parsed, names, slugs[aid])
        if c:
            done.append(aid)
        allc += c
        alltags += t
        problems += pr
    allc.sort(key=lambda c: (c['day'], law_key('A' + c['act']), c['order']))
    allc = [c for c in allc if c['day'] <= today]
    for c in allc:
        c['subject'] = fit_subject(c, acts[c['act']]['title'], names)
    tags = write_repo(Path(a.repo), allc, alltags, today, a.branch)
    stats = {'acts': len(done), 'ids': ids, 'commits': len(allc), 'tags': len(tags), 'versions_parsed': len(parsed),
             'problems': problems,
             'kinds': {}}
    for c in allc:
        k = next(t[6:] for t in c['trailers'] if t.startswith('Kind: '))
        stats['kinds'][k] = stats['kinds'].get(k, 0) + 1
    (HERE / 'cache' / 'build-stats.json').write_text(json.dumps(stats, indent=1, sort_keys=True), encoding='utf-8')
    if not a.no_docs:
        import reproduce
        reproduce.write_docs(Path(a.repo), ids=done, acts=acts, slugs=slugs, commits=allc, tags=tags, today=today,
                             stats=stats,
                             in_force_total=json.loads(MANIFEST.read_text(encoding='utf-8')).get('in_force_total'))
    print(json.dumps({k: v for k, v in stats.items() if k != 'problems'}, indent=1), len(problems), 'problems')


if __name__ == '__main__':
    sys.exit(main())
