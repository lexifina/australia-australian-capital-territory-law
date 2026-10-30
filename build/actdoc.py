"""Split one ACT republication (DOCX or RTF, PCO Word styles) into provision files, and read its endnotes.

parse(data) -> {
  'files': {name: text},           # intro.md, structure.md, s-005.md, sch-1.md, dictionary.md, ...
  'order': [name, ...],
  'history': [(provision, text)],  # endnote 4 (Amendment history) rows, provision carried down to continuation rows
  'laws': {id: title},             # endnote 3 (Legislation history): 'A2016-5' -> 'Human Rights Amendment Act 2016'
  'repub': 'Republication No 17' line, 'effective': cover 'Effective:' text
}
"""
from __future__ import annotations

import re

import wordlib

PARSER_VERSION = 16

HYPHENS = str.maketrans({'‑': '-', '‐': '-', '­': None})
SPACE_RE = re.compile(r'[       ]+')
CTRL_RE = re.compile(r'[\u0000-\u0008\u000b-\u001f​‌‍⁠﻿]')
LAW_ID = re.compile(r'\b(A|SL|Ord|CN|DI|NI|AR)\s?(\d{4})\s?[-‑‐–]\s?(\d+)')

HEAD_STRUCT = {'A H1 Chapter': 1, 'A H2 Part': 2, 'A H3 Div': 3, 'A H4 SubDiv': 4,
               'AH1 Chapter': 1, 'AH2 Part': 2, 'AH3 Div': 3, 'AH4 SubDiv': 4,
               # republications of 2000-2001 ('A H1 Part', 'A H2 Div') and of the 1990s ('heading 1', 'heading 2')
               'A H1 Part': 2, 'A H2 Div': 3, 'heading 1': 2, 'heading 2': 3}
# section headings: the current template, its 'with symbol' variant (U, M), 2000-2001 ('A H3 sec'), and the odd
# section 1 styled as an amendment-text heading ('I H5 Sec') in 2002 republications
SEC_STYLES = {'A H5 Sec', 'AH5Sec', 'A H5 Sec Symb', 'A H3 sec', 'A H3 Sec', 'I H5 Sec'}
# 1990s republications: the section heading ('heading 3') has no number; the number starts the next paragraph ('578.')
OLD_SEC_STYLE = 'heading 3'
OLD_SEC_NUM = re.compile(r'^\s*(\d+[A-Z]*)\.\s')
# 1990s republications: schedules are body paragraphs 'FIRST SCHEDULE', 'SCHEDULE 5' (capitals)
OLD_SCHED = re.compile(r'^(?:(FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|SEVENTH|EIGHTH|NINTH|TENTH|ELEVENTH|TWELFTH) SCHEDULE'
                       r'|SCHEDULE (\d+[A-Z]*))(?:\s|$)')
OLD_SCHED_STYLES = {'all sections', 'Normal', 'full out', 'heading 1', 'heading 2'}
ORDINALS = {w: i for i, w in enumerate(['FIRST', 'SECOND', 'THIRD', 'FOURTH', 'FIFTH', 'SIXTH', 'SEVENTH', 'EIGHTH',
                                         'NINTH', 'TENTH', 'ELEVENTH', 'TWELFTH'], 1)}
# where the law ends: 'ENDNOTES' (Endnote1), or in the 1990s the 'NOTE(S)' or 'Table of Amendments' heading
END_TEXTS = {'NOTE', 'NOTES', 'ENDNOTES', 'Table of Amendments'}
# page and section markers of the PCO template. '02Text' (the chapter text marker) is not skipped: it is normally empty,
# but the Workers Compensation Act 1951 R15-R50 put a note's text in it ("The following chart is a guide ...").
SKIP_STYLES = {'PageBreak', 'Placeholder', '03Schedule', '04Dictionary', '01Contents', 'BillBasic',
               '00SigningPage', 'N-line3', 'N-line1', 'N-line2', '01aPreamble', 'Endnote20', '05EndNote'}


def clean(t: str) -> str:
    t = t.translate(HYPHENS)
    t = CTRL_RE.sub('', t)
    t = t.replace('\n', ' ').replace('\t', ' ')
    t = SPACE_RE.sub(' ', t)
    # private-use bullets from Symbol font
    t = t.replace('', '•').replace('', '•')
    return t.strip()


def md_escape_start(t: str) -> str:
    if t[:1] in '#>-*+' or re.match(r'\d+\. ', t):
        return '\\' + t
    return t


def indent_of(style: str) -> int:
    s = style.lower()
    if 'subsubpara' in s:
        return 3
    if 'subpara' in s:
        return 2
    if 'para' in s and 'paragraph' not in s:
        return 1
    return 0


def sec_number(text: str) -> str | None:
    t = text.replace(' ', ' ').strip()
    # symbols before the heading: U (uncommenced), M (modified)
    m = re.match(r'^(?:[UM]\s*\t?\s*)?(\d+[A-Z]*(?:\.\d+[A-Z]*)*)\.?\s*(?:\t|  | (?=[A-Z‘“(]))', t)
    return m.group(1) if m else None


def sec_file(num: str) -> str:
    m = re.match(r'(\d+)(.*)', num)
    return f's-{int(m.group(1)):03d}{m.group(2)}.md'


def sched_file(text: str) -> str:
    t = clean(text)
    m = re.match(r'(?:[UM] )?Schedule ?(\d+[A-Z]*(?:\.\d+)?)?', t, re.I)
    if m and m.group(1):
        return f'sch-{m.group(1)}.md'
    return 'sch.md'


def parse(data: bytes) -> dict:
    paras = wordlib.read_word(data)
    styles = [p[0] for p in paras]
    # body: from the 'Billname' after the last table-of-contents line, to 'Endnote1' (Endnotes)
    # contents lines; an empty toc paragraph does not count (Coroners Act 1997 R2 (RI), R2A: an empty 'toc 1' follows
    # the title and long title, which made them look like part of the contents); when no contents line has text (the
    # contents are fields, Territory-owned Corporations Act 1990 R6B), every toc paragraph counts
    tocs = [i for i, s in enumerate(styles) if s.lower().startswith('toc ')]
    tocs = [i for i in tocs if paras[i][1].strip()] or tocs
    last_toc = tocs[-1] if tocs else -1
    if tocs:
        # the contents end before the first heading of the law itself (a body paragraph can carry a toc style, as a
        # schedule table row in the Administration and Probate Act 1929 R5C does)
        heads = SEC_STYLES | {OLD_SEC_STYLE, 'Billname', 'Billname1'} | set(HEAD_STRUCT)
        first_body = next((i for i in range(tocs[0], len(paras)) if styles[i] in heads and paras[i][1].strip()), None)
        if first_body is not None:
            last_toc = max((i for i in tocs if i < first_body), default=-1)
    start = next((i for i in range(last_toc + 1, len(paras)) if styles[i] in ('Billname', 'Billname1')), None)
    if start is None:
        if last_toc >= 0:          # older republications have no Billname: the law starts after the contents
            start = last_toc + 1
        else:
            start = next((i for i in range(len(paras)) if styles[i] in SEC_STYLES), 0)
            # no contents styles (Health Professionals (Special Events Exemptions) Act 2000 R1: the contents are
            # 'Normal' lines): the law's own title block, 'Australian Capital Territory' + name + long title, sits just
            # above the first section; start there
            near = [i for i in range(max(0, start - 8), start) if paras[i][1].strip()]
            top = [i for i in near if clean(paras[i][1]) == 'Australian Capital Territory']
            if top and len([i for i in near if i > top[-1]]) <= 3:
                start = top[-1]
    end = next((i for i in range(start, len(paras)) if styles[i] == 'Endnote1'
                or (not paras[i][2] and clean(paras[i][1]) in END_TEXTS)), len(paras))
    files: dict[str, list[str]] = {}
    order: list[str] = []
    cur = 'intro.md'

    def put(name: str, line: str):
        if name not in files:
            files[name] = []
            order.append(name)
        files[name].append(line)

    row: dict[int, list[str]] = {}
    i = start
    while i < end:
        style, text, cell = paras[i]
        i += 1
        if style == '\\row':
            cells = [' '.join(x for x in row[k] if x) for k in sorted(row)]
            if any(cells):
                put(cur, md_escape_start(' | '.join(cells)))
            row = {}
            continue
        if cell:
            t = clean(text)
            row.setdefault(cell, []).append(t)
            continue
        if style == 'BillBasic' and sec_number(text) and not cur.startswith('sch') and cur != 'dictionary.md':
            # a section heading left in the template's 'BillBasic' style (Evidence (Miscellaneous Provisions) Act
            # 1991 R32 (RI): '1<tab>Name of Act'); read it as the section heading it is
            style = 'A H5 Sec'
        if style == 'PageBreak' and clean(text):
            # text printed in a page-break paragraph (Listening Devices Act 1992 R3-R7: '11' after s 11; Emergencies
            # Act 2004: 'o' before pt 5.3): the reader sees it, so it is kept, as a line of the current file
            style = 'A main'
        if style in SKIP_STYLES:
            continue
        t = clean(text)
        if not t:
            continue
        if style in HEAD_STRUCT and cur not in ('dictionary.md',) and not cur.startswith('sch'):
            put('structure.md', '#' * HEAD_STRUCT[style] + ' ' + t)
            continue
        if style in OLD_SCHED_STYLES and OLD_SCHED.match(t):
            mm = OLD_SCHED.match(t)
            name = base = f'sch-{ORDINALS[mm.group(1)] if mm.group(1) else mm.group(2)}.md'
            k = 2
            while name in files:
                name = base[:-3] + f'-{k}.md'
                k += 1
            cur = name
            put(cur, '# ' + t)
            continue
        if style == OLD_SEC_STYLE and not cur.startswith('sch') and cur != 'dictionary.md':
            # the number is in the heading (1999-2000: '1 \tShort title') or starts the next paragraph ('578.  The')
            own = sec_number(text)
            nxt = next((paras[j][1] for j in range(i, end) if paras[j][1].strip() and not paras[j][2]), '')
            mm = OLD_SEC_NUM.match(nxt)
            num = own or (mm.group(1) if mm else None)
            if num:
                name = base = sec_file(num)
                k = 2
                while name in files:
                    name = base[:-3] + f'-{k}.md'
                    k += 1
                cur = name
                put(cur, '# ' + t)
                continue
        if style in SEC_STYLES and not cur.startswith('sch') and cur != 'dictionary.md':
            n = sec_number(text)
            if n:
                name = sec_file(n)
                k = 2
                while name in files:
                    name = sec_file(n)[:-3] + f'-{k}.md'
                    k += 1
                cur = name
                put(cur, '# ' + t)
                continue
            if t.lower().startswith('preamble'):
                cur = 'intro.md'
                put(cur, '## ' + t)
                continue
        if style.startswith('Sched-heading') or style == 'Sched-heading Symb':
            name = sched_file(text)
            k = 2
            base = name
            while name in files:
                name = base[:-3] + f'-{k}.md'
                k += 1
            cur = name
            put(cur, '# ' + t)
            continue
        if style.startswith('Dict-Heading'):
            cur = 'dictionary.md'
            put(cur, '# ' + t)
            continue
        if style in ('Billname', 'Billname1') and cur == 'intro.md':
            put(cur, '# ' + t)
            continue
        if cur.startswith('sch') and (style.startswith('Sched-Part') or style.startswith('Sched-Form')
                                      or style.startswith('Sch ') or style in HEAD_STRUCT or 'H5' in style):
            put(cur, '## ' + t)
            continue
        put(cur, '  ' * indent_of(style) + md_escape_start(t))
    out_files = {k: '\n'.join(v) + '\n' for k, v in files.items()}

    # endnotes
    history: list[tuple[str, str]] = []
    laws: dict[str, str] = {}
    sect = None
    prov = ''
    for style, text, cell in paras[end:]:
        if style == 'Endnote2':
            sect = clean(text)
            continue
        if sect and sect.startswith('3') and style in ('New Act', 'NewAct', 'NewReg'):
            t = clean(text)
            m = LAW_ID.search(t)
            if m:
                lid = f'{m.group(1)}{m.group(2)}-{m.group(3)}'
                title = t[:t.find(m.group(0))].strip() if t.find(m.group(0)) > 0 else ''
                if title and lid not in laws:
                    laws[lid] = title
        if sect and sect.startswith('4') and style.startswith('AmdtsEntries'):
            raw = text.translate(HYPHENS)
            parts = raw.split('\t')
            if len(parts) >= 2:
                if parts[0].strip():
                    prov = clean(parts[0])
                history.append((prov, clean(' '.join(parts[1:]))))
            else:
                history.append((prov, clean(raw)))
    cover = [clean(t) for s, t, c in paras[:last_toc if last_toc > 0 else 40]]
    repub = next((t for t in cover if t.startswith('Republication No')), '')
    eff = next((t for t in cover if t.startswith('Effective:')), '')
    return {'files': out_files, 'order': order, 'history': history, 'laws': laws, 'repub': repub, 'effective': eff,
            'styles_seen': sorted(set(styles[start:end]))}


# ----------------------------------------------------------------------------- endnote provision -> file
def prov_files(prov: str) -> list[str] | None:
    """Files an endnote 4 provision label refers to; None when it cannot be told (a whole part, etc)."""
    p = prov.strip().lower()
    p = p.replace(' ', ' ')
    m = re.match(r'^(?:s|sec|section)\s+(\d+[a-z]*(?:\.\d+[a-z]*)*)', p)
    if m:
        n = m.group(1)
        mm = re.match(r'(\d+)(.*)', n)
        return [f's-{int(mm.group(1)):03d}{mm.group(2).upper()}.md']
    m = re.match(r'^sch\s+(\d+[a-z]*)', p)
    if m:
        return [f'sch-{m.group(1).upper()}.md']
    if p.startswith('sch'):
        return ['sch.md']
    if p.startswith('dict') or p.startswith('def ') or p.startswith('defs '):
        return ['dictionary.md']
    if p.startswith('preamble') or p.startswith('long title') or p.startswith('title') or p.startswith('name of act'):
        return ['intro.md']
    if re.match(r'^(ch|pt|div|sdiv)\s+\S+\s+hdg', p):
        return ['structure.md']
    return None


def row_laws(text: str) -> list[str]:
    return sorted({f'{m.group(1)}{m.group(2)}-{m.group(3)}' for m in LAW_ID.finditer(text)})


def row_editorial(text: str) -> list[int]:
    return sorted({int(m.group(1)) for m in re.finditer(r'\bR(\d+)\s+LA\b', text)})
