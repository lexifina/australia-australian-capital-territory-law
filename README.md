# Australian Capital Territory law, change by change

The Acts of the Australian Capital Territory as a git repository: every republication of every Act in force, from the
[ACT Legislation Register](https://www.legislation.act.gov.au), the Territory's authorised legislation website. Each
change is one commit, credited to the law that made it and dated the day it took effect, at noon in Canberra.

50 Acts, 958 law commits, 908 tags (one per republication).

## The questions, as git commands

| Question | Command |
| --- | --- |
| What did section 5 say on 1 January 2010? | `python tools/in_force_on.py 2010-01-01 crimes-act-1900 5` |
| The whole Act on a day | `python tools/in_force_on.py 2020-01-01 crimes-act-1900` |
| Which law wrote each line of a section? | `git blame crimes-act-1900/s-005.md` |
| Every change to a section, with the law and the day | `git log --follow crimes-act-1900/s-005.md` |
| Everything one amending law changed | `git log --author="(A2001-44)" --stat` |
| A republication exactly as published | `git show crimes-act-1900/R0C-RI:crimes-act-1900/s-005.md` (tags are `<act>/<republication>`) |
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

Kinds in this build: amendment 741, baseline 42, editorial change 14, expiry 62, new law 8, no law named 76, two or more laws 15.

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
