"""Checks the built repository against every republication it was built from (cache only, no network).

    python -X utf8 check_repo.py --today 2026-10-29 [--repo <build folder>] [--only 1900-40,...] [--no-record]

1. Files: for every republication tag <act>/<label>, the Act's folder at the tag holds exactly the files the parser
   makes from that republication's Word file (same names, same bytes), and the tagged commit's In-force trailer is the
   republication's start day ("effective from" on the register).
2. History: no merges; In-force days never go backwards along main; none is after --today; every commit has In-force
   and Kind trailers; no carriage return in any message; Lexifina is the committer of every commit, and author of the
   documentation commit only; every commit date is noon in Canberra on its In-force day (see build.canberra_offset).
3. Every Act in force whose republications are all cached has a folder, and every republication of it held in Word
   form and in force by --today has a tag.
The independent letters-and-digits check is check_words.py. Writes verification.json unless --no-record.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build  # noqa: E402
import check_words  # noqa: E402


def git(repo: str, *args: str) -> str:
    r = subprocess.run(['git', *args], cwd=repo, capture_output=True)
    if r.returncode:
        raise SystemExit(f'git {" ".join(args[:3])}: {r.stderr.decode(errors="replace")[:300]}')
    return r.stdout.decode('utf-8')


def check_act(args) -> dict:
    repo, aid, page, slug, today, skipped_by_build = args
    bad, n, skipped = [], 0, []
    tags = set(git(repo, 'tag', '--list', f'{slug}/*').split())
    for v in build.act_versions(page, today):
        url = build.doc_url(v)
        tag = f'{slug}/{build.tag_label(v["label"])}'
        r = build.parse_cached(url)
        if v['label'] in skipped_by_build:
            # a file the build could not use and lists in build-stats.json (not Word: a Word 97 .doc behind an RTF
            # link, or zeros; or damaged so that no section can be found); the next republication carries its changes.
            # It must have no tag of its own.
            skipped.append(f'{tag}: {(r or {}).get("error") or "no sections found"}')
            if tag in tags:
                bad.append(f'{tag}: skipped by the build, but tagged')
            continue
        if r is None or 'error' in r:
            bad.append(f'{tag}: source not readable')
            continue
        if tag not in tags:
            bad.append(f'{tag}: no tag')
            continue
        n += 1
        inf = git(repo, 'log', '-1', '--format=%(trailers:key=In-force,valueonly,separator=)', tag).strip()
        if inf != v['from']:
            # a republication that changed no text: its tag is the Act's last commit before it, and the Act's next
            # change (if any) must come after the republication's day
            nxt = git(repo, 'log', '--reverse', '--format=%(trailers:key=In-force,valueonly,separator=)',
                      f'{tag}..HEAD', '--', f'{slug}/').split()
            if not (inf < v['from'] and (not nxt or nxt[0] >= v['from'] and nxt[0] != inf)):
                bad.append(f'{tag}: In-force {inf!r}, republication effective from {v["from"]}; next change {nxt[:1]}')
        got = check_words.files_at(repo, tag, slug)
        want = r['files']
        if got != want:
            miss = sorted(set(want) - set(got))[:3]
            extra = sorted(set(got) - set(want))[:3]
            diff = sorted(k for k in set(want) & set(got) if want[k] != got[k])[:3]
            bad.append(f'{tag}: files differ (missing {miss}, extra {extra}, changed {diff})')
    return {'act': aid, 'republications': n, 'bad': bad, 'skipped': skipped}


def history(repo: str, today: str) -> tuple[list[str], dict]:
    bad = []
    merges = git(repo, 'rev-list', '--merges', '--count', 'HEAD').strip()
    if merges != '0':
        bad.append(f'{merges} merge commits')
    raw = git(repo, 'log', '--reverse', '--format=%H%x1f%an%x1f%cn%x1f%ad%x1f%B%x1e', '--date=iso-strict', 'HEAD')
    rows = [r.strip('\n').split('\x1f') for r in raw.split('\x1e') if r.strip()]
    prev = ''
    kinds: dict[str, int] = {}
    for h, an, cn, ad, body in rows:
        inf = re.search(r'^In-force: (\d{4}-\d{2}-\d{2})$', body, re.M)
        kind = re.search(r'^Kind: (.+)$', body, re.M)
        if not inf or not kind:
            bad.append(f'{h[:9]}: no In-force or Kind trailer')
            continue
        d, k = inf.group(1), kind.group(1)
        kinds[k] = kinds.get(k, 0) + 1
        if '\r' in body:
            bad.append(f'{h[:9]}: carriage return in message')
        if cn != 'Lexifina':
            bad.append(f'{h[:9]}: committer {cn}')
        if (an == 'Lexifina') != (k == 'documentation'):
            bad.append(f'{h[:9]}: author {an} for kind {k}')
        if k != 'documentation':
            if d < prev:
                bad.append(f'{h[:9]}: In-force {d} before the previous commit\'s {prev}')
            prev = d
        if d > today:
            bad.append(f'{h[:9]}: In-force {d} after {today}')
        day = dt.date.fromisoformat(d)
        if day.year >= 1970:
            off = build.canberra_offset(day)
            want = f'{d}T12:00:00+{off:02d}:00'
            if ad != want:
                bad.append(f'{h[:9]}: date {ad}, want {want}')
    return bad, {'commits': len(rows), 'kinds': kinds, 'merges': int(merges)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default=str(HERE.parents[2] / 'australia-australian-capital-territory-law-build'))
    ap.add_argument('--today', required=True)
    ap.add_argument('--only', default='')
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--no-record', action='store_true')
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    only = [x.strip().lstrip('A') for x in a.only.split(',') if x.strip()]
    ids, acts, _ = build.collect(only, a.today)
    # the Acts the build was made from (the fetch may have completed more since)
    stats = json.loads((HERE / 'cache' / 'build-stats.json').read_text(encoding='utf-8'))
    built = stats.get('ids')
    if built is not None and not only:
        ids = [x for x in ids if x in set(built)]
    skip_by_act: dict[str, set] = {}
    for p in stats.get('problems', []):
        skip_by_act.setdefault(p['act'].lstrip('A'), set()).add(p['version'])
    # the folder of each Act, as the build names it: the folder whose tags' commits cite this Act's register page
    folders = {}
    for line in git(a.repo, 'for-each-ref', '--format=%(refname:short)', 'refs/tags').split():
        folders.setdefault(line.split('/', 1)[0], line)
    by_act = {}
    for folder, tag in folders.items():
        m = re.search(r'/a/(\d{4}-\d+)/', git(a.repo, 'log', '-1', '--format=%B', tag))
        if m:
            by_act[m.group(1)] = folder
    bad = [f'A{aid}: no folder in the repository' for aid in ids if aid not in by_act]
    jobs = [(a.repo, aid, acts[aid], by_act[aid], a.today, skip_by_act.get(aid, set())) for aid in ids if aid in by_act]
    n = 0
    skipped = []
    with cf.ProcessPoolExecutor(a.workers) as ex:
        for r in ex.map(check_act, jobs, chunksize=1):
            n += r['republications']
            bad += r['bad']
            skipped += r['skipped']
    hb, hs = history(a.repo, a.today)
    bad += hb
    rec = {'acts': len(jobs), 'republications': n, 'mismatches': len(bad), 'checked_on': a.today,
           'skipped_not_word': skipped,
           'head': git(a.repo, 'rev-parse', 'HEAD').strip(), **hs, 'first_mismatches': bad[:50]}
    print(json.dumps(rec, indent=1, ensure_ascii=False))
    if not a.no_record:
        p = HERE / 'verification.json'
        ver = json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
        ver['check'] = rec
        p.write_text(json.dumps(ver, indent=1, sort_keys=True, ensure_ascii=False) + '\n', encoding='utf-8', newline='\n')
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
