# Reproduce

Built 2026-10-30 in Claude Code by Claude Opus 5.5 (`claude-opus-5-5`). No model wrote or edited legal text, and no model
runs at build time: the text is the register's Word files, split by their paragraph styles.

## Steps

With the register's responses retrieved 2026-10-28 to 2026-10-30 in place where `build/actcache.py` reads them (each matching its
SHA-256 in `build/DATA-SHA256SUMS`; 7055 responses are read by this build). How they were retrieved is not part of
this record.

```
cd build
python -X utf8 build.py --today 2026-10-30  # reads only the retrieved responses; git fast-import
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
