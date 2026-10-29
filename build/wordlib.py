"""Read a Word document (DOCX or RTF) into a flat list of paragraphs: (style name, text, cell).

`cell` is 0 outside tables; inside a table it is the 1-based cell number in its row, and a paragraph with
style '\\row' (text '') marks the end of a table row. Field instructions, headers, footers, footnotes, pictures and
hidden text are dropped; field results are kept.
"""
from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree as ET

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


# ------------------------------------------------------------------------------------------------- symbol fonts
# A w:sym character is a glyph of a symbol font, not a letter of the text: 'q' in Monotype Sorts is a box (❑).
# Symbol: the Adobe Symbol encoding (Greek letters and signs); dingbat fonts: the Zapf Dingbats block (U+2700 + code -
# 0x20 for 0x21-0x7E, the order Monotype Sorts and Zapf Dingbats share); any other symbol font: a white square.
_SYMBOL_GREEK = dict(zip(b'ABGDEZHQIKLMNOPRSTUFCYWXJabgdezhqiklmnoprstufcywxjVv',
                         'ΑΒΓΔΕΖΗΘΙΚΛΜΝΟΠΡΣΤΥΦΧΨΩΞϑαβγδεζηθικλμνοπρστυφχψωξφςϖ'))
_SYMBOL_SIGNS = {0xB7: '•', 0xBE: '⎯', 0xB1: '±', 0xB4: '×', 0xB8: '÷', 0xB0: '°', 0xAE: '→', 0xAC: '←', 0xA3: '≤',
                 0xB3: '≥', 0xB9: '≠', 0xBB: '≈', 0xD7: '·', 0xA5: '∞', 0x2D: '−', 0xE2: '®', 0xE3: '©', 0xE4: '™'}


def sym_char(font: str, code: int) -> str:
    f = font.lower()
    if f == 'symbol':
        if code in _SYMBOL_GREEK:
            return _SYMBOL_GREEK[code]
        if code in _SYMBOL_SIGNS:
            return _SYMBOL_SIGNS[code]
        return chr(code) if code < 0x41 or 0x5B <= code < 0x61 else '□'
    if 'sorts' in f or 'dingbat' in f:
        return chr(0x2700 + code - 0x20) if 0x21 <= code <= 0x7E else '□'
    return '□'


# ---------------------------------------------------------------------------------------------------------- DOCX
def _docx_styles(z: zipfile.ZipFile) -> dict[str, str]:
    out = {}
    try:
        root = ET.fromstring(z.read('word/styles.xml'))
    except KeyError:
        return out
    for st in root.iter(W + 'style'):
        sid = st.get(W + 'styleId')
        name = st.find(W + 'name')
        out[sid] = name.get(W + 'val') if name is not None else sid
    return out


def read_docx(data: bytes) -> list[tuple[str, str, int]]:
    z = zipfile.ZipFile(io.BytesIO(data))
    styles = _docx_styles(z)
    root = ET.fromstring(z.read('word/document.xml'))
    body = root.find(W + 'body')
    out: list[tuple[str, str, int]] = []

    def run_text(p) -> str:
        parts = []
        depth = 0          # complex field state: 0 = normal, 1 = in instruction, 2 = in result
        stack = []
        for el in p.iter():
            tag = el.tag
            if tag == W + 'fldChar':
                kind = el.get(W + 'fldCharType')
                if kind == 'begin':
                    stack.append('instr')
                elif kind == 'separate' and stack:
                    stack[-1] = 'result'
                elif kind == 'end' and stack:
                    stack.pop()
                continue
            if stack and stack[-1] == 'instr':
                continue
            if tag == W + 't':
                parts.append(el.text or '')
            elif tag == W + 'tab':
                parts.append('\t')
            elif tag in (W + 'br', W + 'cr'):
                parts.append('\n')
            elif tag == W + 'noBreakHyphen':
                parts.append('\u2011')
            elif tag == W + 'softHyphen':
                pass
            elif tag == W + 'sym':
                ch = el.get(W + 'char') or ''
                try:
                    code = int(ch, 16)
                    if code >= 0xF000:
                        code -= 0xF000
                    parts.append(sym_char(el.get(W + 'font') or '', code))
                except ValueError:
                    pass
        return ''.join(parts)

    def hidden(r) -> bool:
        rpr = r.find(W + 'rPr')
        if rpr is None:
            return False
        v = rpr.find(W + 'vanish')
        return v is not None and v.get(W + 'val') not in ('0', 'false')

    def para(p, cell):
        ppr = p.find(W + 'pPr')
        sname = ''
        if ppr is not None:
            ps = ppr.find(W + 'pStyle')
            if ps is not None:
                sid = ps.get(W + 'val')
                sname = styles.get(sid, sid)
        # drop hidden runs: rebuild a shallow copy without them
        for r in list(p):
            if r.tag == W + 'r' and hidden(r):
                p.remove(r)
        out.append((sname or 'Normal', run_text(p), cell))

    def walk(container, cell):
        for el in container:
            if el.tag == W + 'p':
                para(el, cell)
            elif el.tag == W + 'tbl':
                for tr in el.findall(W + 'tr'):
                    for i, tc in enumerate(tr.findall(W + 'tc'), 1):
                        walk(tc, i)
                    out.append(('\\row', '', 1))
            elif el.tag == W + 'sdt':
                c = el.find(W + 'sdtContent')
                if c is not None:
                    walk(c, cell)

    walk(body, 0)
    return out


# ----------------------------------------------------------------------------------------------------------- RTF
_TOKEN = re.compile(rb"\\([a-zA-Z]+)(-?\d+)? ?|\\'([0-9a-fA-F]{2})|\\(.)|([{}])|(\r\n|\r|\n)|([^\\{}\r\n]+)", re.S)

SKIP_DEST = {
    b'fonttbl', b'colortbl', b'stylesheet', b'info', b'pict', b'header', b'headerl', b'headerr', b'headerf',
    b'footer', b'footerl', b'footerr', b'footerf', b'fldinst', b'footnote', b'listtable', b'listoverridetable',
    b'rsidtbl', b'xmlnstbl', b'mmathPr', b'themedata', b'colorschememapping', b'latentstyles', b'datastore',
    b'generator', b'pgdsctbl', b'object', b'shppict', b'nonshppict', b'userprops', b'revtbl', b'filetbl', b'docvar',
    b'listtext', b'pntext', b'pntxta', b'pntxtb', b'pn', b'shp', b'shpinst', b'sp', b'template', b'xe', b'tc', b'tcn',
    b'bkmkstart', b'bkmkend', b'annotation', b'atnid', b'atnauthor', b'fldtype', b'formfield', b'ftnsep',
    b'ftnsepc', b'aftnsep', b'aftnsepc', b'wgrffmtfilter', b'defchp', b'defpap', b'blipuid', b'factoidname',
    b'oldcprops', b'oldpprops', b'oldsprops', b'oldtprops', b'protusertbl', b'operator', b'author', b'title',
    b'company', b'listpicture', b'panose', b'falt', b'fname', b'mmath', b'grfdocevents', b'ud',
}


def _cp1252(b: int) -> str:
    return bytes([b]).decode('cp1252', errors='replace')


def _stylesheet(data: bytes) -> dict[int, str]:
    i = data.find(b'{\\stylesheet')
    if i < 0:
        return {}
    depth = 0
    j = i
    while j < len(data):
        c = data[j:j + 1]
        if c == b'\\':
            j += 2
            continue
        if c == b'{':
            depth += 1
        elif c == b'}':
            depth -= 1
            if depth == 0:
                break
        j += 1
    # Line breaks in RTF source carry no meaning, and Word writes them anywhere, including between '{' and '\s190'
    # (older PCO files): drop them before reading the entries.
    sheet = data[i + 1:j].replace(b'\r', b'').replace(b'\n', b'')
    out = {}
    # each top-level group of the stylesheet is one style entry
    k, depth, start = 0, 0, None
    entries = []
    while k < len(sheet):
        c = sheet[k:k + 1]
        if c == b'\\':
            k += 2
            continue
        if c == b'{':
            depth += 1
            if depth == 1:
                start = k
        elif c == b'}':
            if depth == 1 and start is not None:
                entries.append(sheet[start + 1:k])
            depth -= 1
        k += 1
    for e in entries:
        m = re.match(rb'\s*(?:\\\*\s*)?\\(s|ds|ts|cs)(\d+)', e)
        if m is None:
            if re.match(rb'\s*\\(?:ql|qj|qc|qr|li|ri|sb|sa|widctlpar|nowidctlpar|f\d|fs|snext|sbasedon)', e) and 0 not in out:
                kind, num = b's', 0          # the Normal style has no \s0
            else:
                continue
        else:
            kind, num = m.group(1), int(m.group(2))
        if kind != b's':
            continue
        body = e
        while True:
            nb = re.sub(rb'\{[^{}]*\}', b'', body)
            if nb == body:
                break
            body = nb
        name = re.sub(rb"\\'([0-9a-fA-F]{2})", lambda x: b'\x00' + x.group(1), body)
        name = re.sub(rb'\\[a-zA-Z]+-?\d* ?', b'', name)
        name = re.sub(rb'\\[^a-zA-Z\x00]', b'', name)
        name = re.sub(rb'\x00([0-9a-fA-F]{2})', lambda x: bytes([int(x.group(1), 16)]), name)
        name = name.strip().rstrip(b';').strip()
        out[num] = name.decode('cp1252', errors='replace').split(',')[0].strip()
    if 0 not in out:
        out[0] = 'Normal'
    return out


def read_rtf(data: bytes) -> list[tuple[str, str, int]]:
    styles = _stylesheet(data)
    out: list[tuple[str, str, int]] = []
    stack = []
    skip = False
    uc = 1
    pending_skip = 0
    style = 0
    hidden = False
    in_table = False
    cell = 1
    buf: list[str] = []
    star = False
    group_start = False

    def flush():
        nonlocal buf
        out.append((styles.get(style, f's{style}'), ''.join(buf), cell if in_table else 0))
        buf = []

    for m in _TOKEN.finditer(data):
        word, num, hexv, sym, brace, nl, text = m.groups()
        if brace == b'{':
            stack.append((skip, uc, hidden, style if False else None))
            group_start = True
            star = False
            continue
        if brace == b'}':
            if stack:
                skip, uc, hidden, _ = stack.pop()
            group_start = False
            continue
        if nl:
            continue
        if skip:
            if word is not None:
                group_start = False
            elif sym is not None and sym == b'*':
                pass
            continue
        if sym is not None:
            group_start_was = group_start
            group_start = False
            if sym == b'*':
                star = True
                group_start = group_start_was
                continue
            if pending_skip:
                pending_skip -= 1
                continue
            if hidden:
                continue
            if sym == b'~':
                buf.append('\u00a0')
            elif sym == b'_':
                buf.append('\u2011')
            elif sym == b'-':
                pass
            elif sym in (b'\\', b'{', b'}'):
                buf.append(sym.decode())
            elif sym in (b'\n', b'\r'):
                flush()
            continue
        if hexv is not None:
            group_start = False
            if pending_skip:
                pending_skip -= 1
                continue
            if not hidden:
                buf.append(_cp1252(int(hexv, 16)))
            continue
        if word is not None:
            was_start = group_start
            group_start = False
            if was_start and (star or word in SKIP_DEST):
                skip = True
                star = False
                continue
            star = False
            n = int(num) if num is not None else None
            if word == b'par':
                flush()
            elif word == b'pard':
                style = 0
                in_table = False
            elif word == b's' and n is not None:
                style = n
            elif word == b'intbl':
                in_table = True
            elif word in (b'cell', b'nestcell'):
                flush()
                cell += 1
            elif word in (b'row', b'nestrow'):
                if buf:
                    flush()
                out.append(('\\row', '', 1))
                cell = 1
            elif word == b'tab':
                if not hidden:
                    buf.append('\t')
            elif word == b'line':
                if not hidden:
                    buf.append('\n')
            elif word == b'uc' and n is not None:
                uc = n
            elif word == b'u' and n is not None:
                if not hidden:
                    buf.append(chr(n + 65536 if n < 0 else n))
                pending_skip = uc
                continue
            elif word == b'v':
                hidden = n != 0
            elif word == b'plain':
                hidden = False
            elif word == b'emdash':
                buf.append('\u2014')
            elif word == b'endash':
                buf.append('\u2013')
            elif word == b'lquote':
                buf.append('\u2018')
            elif word == b'rquote':
                buf.append('\u2019')
            elif word == b'ldblquote':
                buf.append('\u201c')
            elif word == b'rdblquote':
                buf.append('\u201d')
            elif word == b'bullet':
                buf.append('\u2022')
            elif word == b'emspace' or word == b'enspace':
                buf.append(' ')
            elif word == b'sect':
                flush()
            continue
        if text is not None:
            group_start = False
            t = text
            if pending_skip:
                k = min(pending_skip, len(t))
                t = t[k:]
                pending_skip -= k
            if t and not hidden:
                buf.append(t.decode('cp1252', errors='replace'))
    if buf:
        flush()
    return out


def read_word(data: bytes) -> list[tuple[str, str, int]]:
    if data[:2] == b'PK':
        return read_docx(data)
    if data[:5] == b'{\\rtf':
        return read_rtf(data)
    raise ValueError('not DOCX or RTF')
