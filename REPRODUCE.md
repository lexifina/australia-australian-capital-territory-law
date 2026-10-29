# Reproduce

Built 2026-10-29 in Claude Code by Claude Opus 5.5 (`claude-opus-5-5`). No model wrote or edited legal text, and no model
runs at build time: the text is the register's Word files, split by their paragraph styles.

## Steps

```
cd build
python -X utf8 fetch_all.py              # the one fetch process; resumable; cache in cache/http
python -X utf8 build.py --today 2026-10-29  # reads only the cache; git fast-import
python -X utf8 check_repo.py             # every republication equals its Word file on its day
python -X utf8 check_words.py            # independent letters-and-digits check against the raw files
```

## Fetch rate

One request at a time, at least 10.5 seconds apart (robots.txt `Crawl-delay: 10`), with the user agent
`Mozilla/5.0 (compatible; Lexifina law history; +https://lexifina.com/watch)`. 996 cached responses are read by this build;
`build/DATA-SHA256SUMS` lists the SHA-256 of each.

## Sources

| What | URL | Used for |
| --- | --- | --- |
| Law page | `https://www.legislation.act.gov.au/a/<year>-<no>/` | republications, dates, notes, amendments table |
| Republication | `https://www.legislation.act.gov.au/DownloadFile/a/<id>/<version>/DOCX/<id>.DOCX` (RTF before 2017) | the text |
| Acts tables 1989-2019, Red book | `https://www.legislation.act.gov.au/Static/UsefulResources/Tables/` | finding law numbers |

Law numbers after 2019 were found by reading `/a/<year>-<n>/` for n = 1, 2, ... until 8 numbers in a row did not exist.

## Decisions

- A republication with only a PDF (historical reprints) is skipped; an Act starts at its first Word republication.
- "As notified" texts are not used: the first republication is the Act as in force.
- Two versions on one day (a republication and its reissue for a retrospective amendment) are both committed, in
  order of publication.
- Non-breaking hyphens are written as "-"; tabs between a number and its text are a space.
