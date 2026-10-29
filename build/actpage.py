"""Parse an ACT Legislation Register law page (/a/<year>-<no>/): title, status, republications, amendment table."""
from __future__ import annotations

import html
import re

_TAG = re.compile(r'<[^>]+>')


def _text(s: str) -> str:
    return re.sub(r'\s+', ' ', html.unescape(_TAG.sub(' ', s))).strip()


MONTHS = {m: i for i, m in enumerate(['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august',
                                      'september', 'october', 'november', 'december'], 1)}


def parse_date(s: str) -> str | None:
    m = re.search(r'(\d{1,2}) ([A-Za-z]+) (\d{4})', s)
    if not m or m.group(2).lower() not in MONTHS:
        return None
    return f'{int(m.group(3)):04d}-{MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}'


def _section(page: str, start_marker: str) -> str:
    i = page.find(start_marker)
    if i < 0:
        return ''
    j = page.find('</table>', i)
    return page[i:j]


def parse(page: str) -> dict:
    out: dict = {}
    m = re.search(r'<h2>(.*?)</h2>', page, re.S)
    out['title'] = _text(m.group(1)) if m else ''
    m = re.search(r'<dt>Number</dt><dd[^>]*>(.*?)</dd>', page, re.S)
    out['number'] = _text(m.group(1)) if m else ''
    m = re.search(r'<dt>Status</dt><dd[^>]*>(.*?)</dd>', page, re.S)
    out['status'] = _text(m.group(1)) if m else ''
    # point-in-time versions: the table after 'point in time versions'
    i = page.find('point in time versions')
    versions = []
    if i >= 0:
        tbody = page[page.find('<tbody>', i):page.find('</tbody>', i)]
        for row in re.findall(r'<tr>(.*?)</tr>', tbody, re.S):
            cells = re.findall(r'<td([^>]*)>(.*?)</td>', row, re.S)
            if len(cells) < 4:
                continue
            eff = _text(cells[0][1])
            links = re.findall(r'href="(/DownloadFile/[^"]+)"', cells[1][1])
            label = _text(cells[2][1])
            published = parse_date(_text(cells[3][1])) if len(cells) > 3 else None
            notes_html = cells[4][1] if len(cells) > 4 else ''
            notes = [_text(p) for p in re.findall(r'<p>(.*?)</p>', notes_html, re.S)] or ([_text(notes_html)] if _text(notes_html) else [])
            parts = eff.split(' - ')
            versions.append({
                'label': label, 'from': parse_date(parts[0]), 'to': parse_date(parts[1]) if len(parts) > 1 else None,
                'published': published, 'notes': notes,
                'acts': sorted(set(re.findall(r'((?:A|Ord|SL|DI|NI|CN|Cwlth)\d{4}-\d+)', ' '.join(notes)))),
                'docx': next((l for l in links if '/DOCX/' in l), None),
                'rtf': next((l for l in links if '/RTF/' in l), None),
                'pdf': next((l for l in links if '/PDF/' in l), None),
                'current': 'data-iscurrent="True"' in cells[0][0],
            })
    out['versions'] = versions
    amend = []
    t = _section(page, 'id="amendment-table"')
    for row in re.findall(r'<tr>(.*?)</tr>', t, re.S):
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)
        if len(cells) < 4:
            continue
        mm = re.search(r'/a/(\d{4}-\d+)/', cells[0])
        amend.append({'id': mm.group(1) if mm else _text(cells[0]), 'title': _text(cells[1]),
                      'provision': _text(cells[2]), 'commencement': _text(cells[3]),
                      'date': parse_date(_text(cells[3]))})
    out['amendments'] = amend
    return out
