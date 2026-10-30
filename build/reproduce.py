"""Writes the documentation commit of the ACT repository: README.md, REPRODUCE.md, SOURCES.md, LICENSE, corpus.json,
changes.csv, tools/in_force_on.py and build/ (the builder and the SHA-256 of every retrieved response it reads)."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Every file written here goes through publish_gate (gitlaw_common, private), which refuses anything the private rule
# of 2026-10-29 keeps out of a published record and scrubs this machine's home path.
sys.path.append(str(HERE.parent / 'gitlaw_common'))  # appended, so this folder's own build.py comes first
try:
    from publish_gate import publish_gate
except ImportError:          # an outside reader rebuilding from the published tools: no private rule to apply
    if os.environ.get('GITLAW_PUBLIC_REBUILD') != '1':
        raise SystemExit('publish gate missing: run from lexifina/scripts, or set GITLAW_PUBLIC_REBUILD=1')

    def publish_gate(files):
        return files
NAME = 'Australian Capital Territory'
CORPUS = 'au/australian-capital-territory/acts'
SCRIPTS = ('actcache.py', 'actpage.py', 'wordlib.py', 'actdoc.py', 'build.py', 'reproduce.py',
           'check_repo.py', 'check_words.py', 'test_repo.py')


def corpus_json(ids, acts, commits, today, stats):
    days = sorted(c['day'] for c in commits)
    return {
        'corpus': CORPUS,
        'parent': 'au/federal/acts',
        'name': f'{NAME} law: the Acts in force, republication by republication',
        'label': NAME,
        'language': 'en',
        'acts': [f'{acts[a]["title"]} (A{a})' for a in ids],
        'history_from': days[0] if days else today,
        'record_to': max((d for d in days if d <= today), default=today),
        'record_note': (f'Every Act in force on the ACT Legislation Register whose republications are held in Word '
                        f'form, from its first such republication to the last one in force on or before {today}; data '
                        f'retrieved up to {today}. Each republication is a checked snapshot; changes between two '
                        f'republications are credited by the later one\'s endnote amendment history.'),
        'in_force_trailer': 'In-force',
        'built': today,
        'checked_on': today,
        'history': 'in-force',
        'has_future_branch': False,
        'dates': {'commit': 'in-force', 'other_trailers': ['Published'],
                  'time': '12:00 Canberra (AEST, UTC+10; AEDT, UTC+11, in daylight saving time)'},
        'scope': {
            'trailer': 'Applies',
            'when_absent': 'this corpus records no scope notes (no commit carries Applies:); the absence is not a '
                           'statement that the change reaches every case',
            'default_reach': {
                'rule': 'presumption against retrospective operation; repeal or amendment does not disturb what was '
                        'done or accrued before',
                'meaning': 'an ACT law is presumed not to operate retrospectively on substantive rights unless it '
                           'says so; the repeal or amendment of a law does not affect its previous operation or rights '
                           'and liabilities acquired or incurred under it',
                'authority': 'Legislation Act 2001 (ACT) s 84 (Saving of operation of repealed and amended laws); '
                             'Maxwell v Murphy (1957) 96 CLR 261 (common-law presumption); not checked in this corpus',
                'decided_here': False,
            },
        },
        'tool': 'tools/in_force_on.py',
        'citation': {'file': '([a-z0-9-]+)/s-0*([0-9]+[A-Z]*)\\.md',
                     'cites': '(section|s) {n}([^0-9A-Za-z]|$)'},
        'verification': 'Every republication used is checked against its own Word file on its effective day '
                        '(tools/build/check_repo.py).',
        'stats': {k: v for k, v in stats.items() if k in ('acts', 'commits', 'tags', 'versions_parsed', 'kinds')},
    }


def write_docs(repo: Path, ids, acts, slugs, commits, tags, today, stats, in_force_total=None):
    import build
    files: dict[str, bytes] = {}

    def put(name: str, text: str):
        files[name] = text.encode('utf-8')
    files['tools/in_force_on.py'] = (HERE / 'repo' / 'tools' / 'in_force_on.py').read_bytes()
    for n in SCRIPTS:
        if (HERE / n).exists():
            files['build/' + n] = (HERE / n).read_bytes()
    # data checksums of the retrieved responses this build reads: the tables, and each Act's page and Word files (more
    # may have been retrieved since; listing only these keeps a rebuild from the same Acts identical)
    used = set()
    for aid in ids:
        used.add(f'{build.HOST}/a/{aid}/')
        for v in build.act_versions(acts[aid], today):
            used.add(build.doc_url(v))
    try:   # the retrieval record (private: it is the fetch's own log)
        from retrieval_record import record
        seen = record(used)
    except ImportError:   # an outside reader: the checksums of the retrieved responses themselves
        import actcache
        seen = {u: {'sha256': hashlib.sha256(b).hexdigest(), 'at': os.environ.get('ACT_RETRIEVED_ON', '')}
                for u in used if (b := actcache.cached(u)) is not None}
    put('build/DATA-SHA256SUMS', ''.join(f'{r["sha256"]}  {u}\n' for u, r in sorted(seen.items())))
    got = sorted(r['at'][:10] for r in seen.values() if r.get('at'))
    retrieved = f'{got[0]} to {got[-1]}' if got and got[0] != got[-1] else (got[0] if got else today)
    cj = corpus_json(ids, acts, commits, today, stats)
    put('corpus.json', json.dumps(cj, indent=2, ensure_ascii=False) + '\n')
    rows = []
    for c in commits:
        t = {}
        for line in c['trailers']:
            k, _, v = line.partition(': ')
            t.setdefault(k, v)
        rows.append({'in_force': c['day'], 'act': acts[c['act']]['title'], 'act_id': f'A{c["act"]}',
                     'kind': t.get('Kind', ''), 'law': t.get('Law', ''), 'author': c['author'][0],
                     'republication': t.get('Republication', ''), 'files': len(c['files'])})
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0]), lineterminator='\n')
        w.writeheader()
        w.writerows(rows)
    put('changes.csv', buf.getvalue())
    kinds = stats.get('kinds', {})
    example = slugs.get('2004-5') or (slugs[ids[0]] if ids else 'human-rights-act-2004')
    # a tag and an amending law that exist in this build
    ex_tags = sorted((m, n) for n, m in tags.items() if n.startswith(example + '/'))
    example_tag = ex_tags[0][1] if ex_tags else f'{example}/R1'
    law_count = {}
    for c in commits:
        for t in c['trailers']:
            if t.startswith('Law: ') and 'Kind: amendment' in c['trailers']:
                law_count[t[5:]] = law_count.get(t[5:], 0) + 1
    example_law = max(sorted(law_count), key=lambda k: law_count[k]) if law_count else 'A2023-53'
    # a partial record (built while the Acts in force are still being retrieved): in_force_total is the register's
    # count of Acts in force with a republication; the statement goes when every one of them is built
    partial = ''
    if in_force_total and len(ids) < in_force_total:
        partial = ('\n'
                   f'**This record is partial.** It is built from the {len(ids)} Acts retrieved so far, of the '
                   f'{in_force_total} Acts in force on the register with a republication. Later rebuilds will add the '
                   'rest and re-date the history: each commit is dated the day its change took effect, so an added '
                   'Act inserts commits earlier in time, and commit hashes change between rebuilds.\n')
    readme = f"""# {NAME} law, change by change

The Acts of the {NAME} as a git repository: every republication of every Act in force, from the
[ACT Legislation Register](https://www.legislation.act.gov.au), the Territory's authorised legislation website. Each
change is one commit, credited to the law that made it and dated the day it took effect, at noon in Canberra.

{stats.get('acts', 0)} Acts, {stats.get('commits', 0)} law commits, {stats.get('tags', 0)} tags (one per republication).
{partial}
## The questions, as git commands

| Question | Command |
| --- | --- |
| What did section 5 say on 1 January 2010? | `python tools/in_force_on.py 2010-01-01 {example} 5` |
| The whole Act on a day | `python tools/in_force_on.py 2020-01-01 {example}` |
| Which law wrote each line of a section? | `git blame {example}/s-005.md` |
| Every change to a section, with the law and the day | `git log --follow {example}/s-005.md` |
| Everything one amending law changed | `git log --author="({example_law})" --stat` |
| A republication exactly as published | `git show {example_tag}:{example}/s-005.md` (tags are `<act>/<republication>`) |
| Changes two or more laws could have made (credited to none) | `git log --grep="Kind: two or more laws"` |
| Changes no single law explains | `git log --grep="Kind: no law named"` |
| Editorial changes by the Parliamentary Counsel | `git log --grep="Kind: editorial change"` |
| Every change with its law, day and republication | [`changes.csv`](changes.csv) |

## Dates

ACT laws commence on a day; the register republishes an Act whenever an amendment to it commences, and lists each
republication with the day it took effect ("effective from") and the laws it was made for. **Commits are dated that
day**, at noon in Canberra: AEST (UTC+10), or AEDT (UTC+11) in daylight saving time, which the ACT keeps with New South
Wales. Every commit carries `In-force: YYYY-MM-DD`. A reissued republication for a retrospective amendment ("R16 (RI)")
is dated the day the amendment is taken to have commenced, and its publication day is in `Published:`.

## Authors

The author of a commit is the law that made the change, as `Name (A2023-53) <a2023-53@legislation.act.invalid>` (an
address that cannot exist). The register's republication notes name the laws each republication was made for; each
republication's endnote 4 (Amendment history) names, per provision, the laws that amended it. A changed file is
credited to the one law named both places. Otherwise the change is committed by the ACT Parliamentary Counsel's Office
and labelled: `Kind: two or more laws` (with `Candidate:` trailers), `Kind: editorial change` (the Parliamentary
Counsel's editorial amendments under the Legislation Act 2001, part 11.3), `Kind: expiry` (provisions that expired under
their own terms), or `Kind: no law named`. Where the register names only one law for a republication but the endnote
row for a provision does not, the change is credited to that law with an `Evidence:` trailer. Provisions omitted as
spent under the Legislation Act 2001, s 89 are credited to that Act. An Act's first republication is credited to the
Act itself when the register calls it the republication "for new Act"; otherwise it is a labelled baseline.

Kinds in this build: {', '.join(f'{k} {v}' for k, v in sorted(kinds.items()))}.

## Files

One folder per Act (named from its current title), with one Markdown file per section (`s-005.md`, `s-027A.md`), one
per schedule (`sch-1.md`), `dictionary.md`, `intro.md` (title, long title, preamble, enacting words) and
`structure.md` (chapter, part and division headings). One paragraph per line; notes and examples printed in the
republication are kept. Endnotes are not law text and are left out (they are the source of the attribution).

## What it does not show

- Acts no longer in force, subordinate laws and instruments (not yet built).
- Versions the register holds only as PDF (historical reprints, mostly before 1992): an Act's record starts at its
  first Word-format republication.
- Uncommenced amendments: the register does not republish an Act until an amendment commences.

## Licence

The legislation is © Australian Capital Territory, used under the Creative Commons Attribution 4.0 licence of the
JACS copyright policy that the register points to (see [LICENSE](LICENSE) and [SOURCES.md](SOURCES.md)). This is not
an authorised version; the authorised text is on the ACT Legislation Register.
"""
    put('README.md', readme)
    lic = f"""Legislation text: © Australian Capital Territory. Source: ACT Legislation Register,
https://www.legislation.act.gov.au. Licensed under the Creative Commons Attribution 4.0 International licence
(https://creativecommons.org/licenses/by/4.0/) under the Justice and Community Safety Directorate copyright policy
(https://www.justice.act.gov.au/copyright), which the register's copyright page names. Changes made here: the text is
split into one file per provision, reflowed to one paragraph per line, and page furniture and endnotes are removed.
This is not an authorised republication and is not endorsed by the ACT Government.

The arrangement, commit messages, tools and documentation: © Lexifina, CC BY 4.0.
"""
    put('LICENSE', lic)
    sources = f"""# Sources and legal basis

## ACT Legislation Register (www.legislation.act.gov.au)

The register is the authorised website for ACT legislation under the Legislation Act 2001 (ACT) ("Approved website
under the Legislation Act 2001 (ACT)" on every page).

- Law pages `/a/<year>-<number>/`: title, status, every republication (label, "effective from - to", publication
  day, notes naming the laws it was made for) and the amendments table (amending law, provision, commencement date).
- Republication files `/DownloadFile/a/<id>/<version>/DOCX|RTF/<id>.DOCX|RTF`: the Word file of each republication.
- Tables `/Static/UsefulResources/Tables/acts<YEAR>.DOCX|RTF` (1989-2019) and `Redbook.RTF` (laws of the ACT at
  31 December 1999), used only to find the numbers of laws to look at.

Retrieved {retrieved}. The SHA-256 of every response this build reads is in
[`build/DATA-SHA256SUMS`](build/DATA-SHA256SUMS).

## Copyright and reuse

The register's footer reads "Copyright 2001 ACT Government. All rights reserved."; its Copyright page says "Use of
material on the ACT Legislation Register should be in accordance with the JACS Copyright Policy developed by the
Justice and Community Safety Directorate." The JACS policy (https://www.justice.act.gov.au/copyright): "Unless stated
otherwise, the copyright of content on this website is owned by the Australian Capital Territory (ACT) and licensed
under a Creative Commons Attribution 4.0 licence ... The appropriate credit is © Australian Capital Territory". Each
republication prints "© Australian Capital Territory <year>". The Parliamentary
Counsel's Office's own website (pco.act.gov.au) has a narrower notice for that site's material; it is not the register.
The repository stays private until the reuse basis is confirmed.
"""
    put('SOURCES.md', sources)
    n_req = len(seen)
    reproduce = f"""# Reproduce

Built {today} in Claude Code by Claude Opus 5.5 (`claude-opus-5-5`). No model wrote or edited legal text, and no model
runs at build time: the text is the register's Word files, split by their paragraph styles.

## Steps

With the register's responses retrieved {retrieved} in place where `build/actcache.py` reads them (each matching its
SHA-256 in `build/DATA-SHA256SUMS`; {n_req} responses are read by this build). How they were retrieved is not part of
this record.

```
cd build
python -X utf8 build.py --today {today}  # reads only the retrieved responses; git fast-import
python -X utf8 check_repo.py             # every republication equals its Word file on its day
python -X utf8 check_words.py            # independent letters-and-digits check against the raw files
```

## Sources

| What | URL | Used for |
| --- | --- | --- |
| Law page | `https://www.legislation.act.gov.au/a/<year>-<no>/` | republications, dates, notes, amendments table |
| Republication | `https://www.legislation.act.gov.au/DownloadFile/a/<id>/<version>/DOCX/<id>.DOCX` (RTF before 2017) | the text |
| Acts tables 1989-2019, Red book | `https://www.legislation.act.gov.au/Static/UsefulResources/Tables/` | finding law numbers |

## Decisions

- A republication with only a PDF (historical reprints) is skipped; an Act starts at its first Word republication.
- "As notified" texts are not used: the first republication is the Act as in force.
- Two versions on one day (a republication and its reissue for a retrospective amendment) are both committed, in
  order of publication.
- Non-breaking hyphens are written as "-"; tabs between a number and its text are a space.
"""
    put('REPRODUCE.md', reproduce)
    for name, data in publish_gate(files).items():
        out = repo / name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
    env = {**os.environ}
    import build as b
    when = b.commit_date(today, [0])
    env.update({'GIT_AUTHOR_NAME': b.COMMITTER[0], 'GIT_AUTHOR_EMAIL': b.COMMITTER[1], 'GIT_AUTHOR_DATE': when,
                'GIT_COMMITTER_NAME': b.COMMITTER[0], 'GIT_COMMITTER_EMAIL': b.COMMITTER[1], 'GIT_COMMITTER_DATE': when})
    b.git(repo, 'add', '-A')
    b.git(repo, 'commit', '-q', '--cleanup=verbatim', '-F', '-', env=env,
          input=f'Documentation, tables, tools, corpus.json and REPRODUCE.md\n\nNo law text changes.\n\nIn-force: {today}\nKind: documentation\n'.encode('utf-8'))
