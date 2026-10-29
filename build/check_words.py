"""Independent letters-and-digits check: the repository's files against the raw republication files.

This reader shares no code with the build (no wordlib, no actdoc, no styles, no paragraphs, no tables). It reads the
characters a reader sees in the Word file (DOCX: the w:t runs of word/document.xml; RTF: the text outside destination
groups), drops field instructions and hidden text, and keeps only letters and digits, case-folded.

The build puts an Act's headings (chapter, part, division) in structure.md and its sections in s-NNN.md files, so the
files are not one contiguous piece of the source: they are an interleaving. The check walks the source's letters from
the title (above the long title) to the ENDNOTES heading, and at each point the next line of exactly one of the files
must continue it. It passes when every line of every file is used, in each file's own order, no source letter in that
stretch is left over, and the next source letters are the ENDNOTES heading. A letter the build dropped, invented or
moved fails the check.

    python -X utf8 check_words.py --repo <build folder> [--only 1900-40,...] [--workers 8] [--no-record]
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import io
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / 'cache' / 'http'
HOST = 'https://www.legislation.act.gov.au'
_KEEP = re.compile(r'[^\W_]+')


def letters(s: str) -> str:
    return ''.join(_KEEP.findall(s.casefold()))


# -------------------------------------------------------------------------------------------- DOCX: the shown text
_RUN_OR_FIELD = re.compile(
    r'<w:r(?:\s[^>]*)?>(.*?)</w:r>|<w:r(?:\s[^>]*)?/>', re.S)
_PIECE = re.compile(r'<w:(t|instrText|delText|fldChar|tab|br|cr)\b([^>]*?)(?:/>|>(.*?)</w:\1>)', re.S)
_ENT = {'&lt;': '<', '&gt;': '>', '&amp;': '&', '&quot;': '"', '&apos;': "'"}


def _unescape(s: str) -> str:
    s = re.sub(r'&#x([0-9a-fA-F]+);', lambda m: chr(int(m.group(1), 16)), s)
    s = re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), s)
    return re.sub(r'&(lt|gt|amp|quot|apos);', lambda m: _ENT[m.group(0)], s)


def docx_text(raw: bytes) -> str:
    xml = zipfile.ZipFile(io.BytesIO(raw)).read('word/document.xml').decode('utf-8')
    body = xml[xml.find('<w:body'):]
    out = []
    depth = []          # one entry per open complex field: True while in its instruction part
    for m in _RUN_OR_FIELD.finditer(body):
        run = m.group(1) or ''
        props = re.search(r'<w:rPr>(.*?)</w:rPr>', run, re.S)
        hidden = bool(props and re.search(r'<w:vanish(?:\s+w:val="(?:1|true|on)")?\s*/>', props.group(1)))
        for p in _PIECE.finditer(run):
            kind, attrs, inner = p.group(1), p.group(2), p.group(3)
            if kind == 'fldChar':
                t = re.search(r'w:fldCharType="(\w+)"', attrs).group(1)
                if t == 'begin':
                    depth.append(True)
                elif t == 'separate' and depth:
                    depth[-1] = False
                elif t == 'end' and depth:
                    depth.pop()
                continue
            if hidden or (depth and depth[-1]) or kind in ('instrText', 'delText'):
                continue
            if kind == 't' and inner:
                out.append(_unescape(inner))
            else:
                out.append(' ')
    return ''.join(out)


# ---------------------------------------------------------------------------------------- RTF: text outside groups
_RTF = re.compile(rb"\\([a-zA-Z]{1,32})(-?\d{1,10})? ?|\\'([0-9a-fA-F]{2})|\\([^a-zA-Z'])|([{}])|([^\\{}\r\n]+)|[\r\n]+")
# groups whose content is not the document's main text
_DEST = {b'fonttbl', b'colortbl', b'stylesheet', b'info', b'pict', b'header', b'headerl', b'headerr', b'headerf',
         b'footer', b'footerl', b'footerr', b'footerf', b'fldinst', b'footnote', b'listtable', b'listoverridetable',
         b'rsidtbl', b'xmlnstbl', b'themedata', b'colorschememapping', b'latentstyles', b'datastore', b'generator',
         b'object', b'nonshppict', b'shp', b'shpinst', b'sp', b'listtext', b'pntext', b'pntxta', b'pntxtb', b'pn',
         b'bkmkstart', b'bkmkend', b'annotation', b'atnid', b'atnauthor', b'xe', b'tc', b'tcn', b'ftnsep', b'ftnsepc',
         b'aftnsep', b'aftnsepc', b'filetbl', b'revtbl', b'userprops', b'docvar', b'template', b'mmathPr', b'shppict'}


def rtf_text(raw: bytes) -> str:
    out = []
    groups = [[False, False, 1]]        # per group: skipping, hidden, \uc
    fresh = False                        # just after '{'
    starred = False
    pending = 0                          # characters still to skip after \uN
    for m in _RTF.finditer(raw):
        word, num, hx, sym, brace, text = m.groups()
        g = groups[-1]
        if brace == b'{':
            groups.append(list(g))
            fresh, starred = True, False
            continue
        if brace == b'}':
            if len(groups) > 1:
                groups.pop()
            fresh = starred = False
            continue
        if word is None and hx is None and sym is None and text is None:
            continue                     # a line break in the source
        if sym == b'*' and fresh:
            starred = True
            continue
        if word is not None and fresh:
            fresh = False
            if starred or word in _DEST:
                g[0] = True
                continue
        fresh = False
        if g[0]:
            continue
        if word is not None:
            n = int(num) if num is not None else None
            if word == b'v':
                g[1] = n != 0
            elif word == b'plain':
                g[1] = False
            elif word == b'uc':
                g[2] = n or 0
            elif word == b'u' and n is not None:
                if not g[1]:
                    out.append(chr(n % 65536))
                pending = g[2]
            elif word in (b'par', b'line', b'tab', b'cell', b'row', b'sect', b'page'):
                out.append(' ')
            continue
        if pending:
            if hx is not None or sym is not None:
                pending -= 1
                continue
            k = min(pending, len(text))
            text, pending = text[k:], pending - k
        if g[1]:
            continue
        if hx is not None:
            out.append(bytes([int(hx, 16)]).decode('cp1252', 'replace'))
        elif sym is not None:
            out.append(' ')
        elif text:
            out.append(text.decode('cp1252', 'replace'))
    return ''.join(out)


def source_letters(raw: bytes) -> str:
    if raw[:2] == b'PK':
        return letters(docx_text(raw))
    if raw[:5] == b'{\\rtf':
        return letters(rtf_text(raw))
    raise ValueError('neither DOCX nor RTF')


# ----------------------------------------------------------------------------------------------- the interleave
def interleave(src: str, files: dict[str, str]) -> tuple[bool, str, int]:
    """(ok, detail, letters). Tries each place the title appears, latest first (the body's title follows the cover and
    the contents)."""
    lines = {}
    for name, text in files.items():
        ls = [letters(l) for l in text.split('\n')]
        lines[name] = [l for l in ls if l]
    lines = {k: v for k, v in lines.items() if v}
    if 'intro.md' not in lines:
        return False, 'no intro.md (title)', 0
    title = lines['intro.md'][0]
    total = sum(len(l) for v in lines.values() for l in v)
    starts = [m.start() for m in re.finditer(re.escape(title), src)][::-1]
    best = (-1, 'title not found in the source')
    for start in starts:
        idx = {k: 0 for k in lines}
        pos = start
        used = 0
        fail = None
        while used < total:
            hits = [(len(v[idx[k]]), k) for k, v in lines.items() if idx[k] < len(v) and src.startswith(v[idx[k]], pos)]
            if not hits:
                nxt = {k: v[idx[k]][:40] for k, v in lines.items() if idx[k] < len(v)}
                fail = (f'source letter {pos - start} not in any file: {src[pos:pos + 60]!r}; '
                        f'next lines: {dict(list(nxt.items())[:4])}')
                break
            if len(hits) > 1:
                # two files' next lines both fit (two sections headed 'Application'): take the file whose following
                # lines go on fitting the source furthest, then the longer line, then the earlier file
                def run(k):
                    v, j, q = lines[k], idx[k], pos
                    while j < len(v) and src.startswith(v[j], q):
                        q += len(v[j])
                        j += 1
                    return q
                hits = [(run(k), n, k) for n, k in hits]
                _, n, k = max(hits, key=lambda h: (h[0], h[1], [-ord(c) for c in h[2]]))
            else:
                n, k = hits[0]
            pos += n
            used += n
            idx[k] += 1
        if fail is None:
            tail = src[pos:pos + 8]
            # the ENDNOTES heading; in the 1990s reprints 'NOTE(S)' followed by note 1, or 'Table of Amendments'
            if pos == len(src) or tail.startswith('endnotes') or re.match(r'notes?1|tableofamendments', tail + src[pos + 8:pos + 20]):
                return True, f'{total} letters', total
            fail = f'after the last line the source goes on: {src[pos:pos + 60]!r}'
        if used > best[0]:
            best = (used, fail)
    return False, best[1], 0


# ------------------------------------------------------------------------------------------- against the repo
def git(repo: str, *args: str, input: bytes | None = None) -> bytes:
    r = subprocess.run(['git', *args], cwd=repo, capture_output=True, input=input)
    if r.returncode:
        raise RuntimeError(f'git {" ".join(args[:3])}: {r.stderr.decode(errors="replace")[:200]}')
    return r.stdout


def files_at(repo: str, ref: str, folder: str) -> dict[str, str]:
    rows = [l.split('\t', 1) for l in git(repo, 'ls-tree', '-r', ref, '--', folder + '/').decode('utf-8').splitlines()]
    blobs = [(f[len(folder) + 1:], info.split()[2]) for info, f in rows if f.endswith('.md')]
    data = git(repo, 'cat-file', '--batch', input=''.join(f'{b}\n' for _, b in blobs).encode())
    out, pos = {}, 0
    for name, _ in blobs:
        nl = data.index(b'\n', pos)
        size = int(data[pos:nl].split()[2])
        out[name] = data[nl + 1:nl + 1 + size].decode('utf-8')
        pos = nl + 2 + size
    return out


def republications(repo: str) -> list[tuple[str, str, str]]:
    """(tag, act folder, source URL) for every republication tag, read from the tagged commit's Source trailer."""
    out = git(repo, 'for-each-ref', '--format=%(refname:short)%09%(*objectname)%(objectname)', 'refs/tags').decode()
    res = []
    tags = [l.split('\t') for l in out.splitlines() if l]
    shas = sorted({s for _, s in tags})
    msgs = {}
    if shas:
        data = git(repo, 'log', '--no-walk', '--format=%H%x1f%B%x1e', *shas).decode('utf-8')
        for rec in data.split('\x1e'):
            if rec.strip():
                h, b = rec.strip('\n').split('\x1f', 1)
                msgs[h] = b
    for tag, sha in tags:
        folder, label = tag.split('/', 1)
        res.append((tag, folder, label, msgs.get(sha, '')))
    return res


def check_one(args) -> tuple[str, bool, str, int]:
    repo, tag, folder, url = args
    p = CACHE / hashlib.sha256(url.encode()).hexdigest()[:40]
    if not p.exists():
        return tag, False, f'source not cached: {url}', 0
    try:
        src = source_letters(p.read_bytes())
        ok, detail, n = interleave(src, files_at(repo, tag, folder))
    except Exception as exc:          # reported, not hidden
        return tag, False, f'error: {exc!r}', 0
    return tag, ok, detail, n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default=str(HERE.parents[2] / 'australia-australian-capital-territory-law-build'))
    ap.add_argument('--only', default='')
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--no-record', action='store_true')
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    jobs = []
    tag_info = republications(a.repo)
    # the tag names the republication; the Source trailer of the commit it points at may belong to a later
    # republication of the same day, so the URL comes from the manifest by label instead
    man = json.loads((HERE / 'cache' / 'manifest.json').read_text(encoding='utf-8'))
    by_folder = {}
    for tag, folder, label, msg in tag_info:
        by_folder.setdefault(folder, []).append((tag, label, msg))
    only = {x.strip().lstrip('A') for x in a.only.split(',') if x.strip()}
    for folder, rows in by_folder.items():
        for tag, label, msg in rows:
            aid = re.search(r'/a/(\d{4}-\d+)/', msg)
            aid = aid.group(1) if aid else None
            if aid is None or (only and aid not in only):
                continue
            url = next((HOST + (v.get('docx') or v.get('rtf')) for v in man['acts'][aid]['versions']
                        if re.sub(r'[^A-Za-z0-9]+', '-', v['label']).strip('-') == label and (v.get('docx') or v.get('rtf'))), None)
            if url:
                jobs.append((a.repo, tag, folder, url))
    bad, n_letters = [], 0
    with cf.ProcessPoolExecutor(a.workers) as ex:
        for tag, ok, detail, n in ex.map(check_one, jobs, chunksize=2):
            n_letters += n
            if not ok:
                bad.append(f'{tag}: {detail}')
    rec = {'republications': len(jobs), 'failed': len(bad), 'letters': n_letters, 'first_failures': bad[:40]}
    print(json.dumps(rec, indent=1, ensure_ascii=False))
    if not a.no_record:
        p = HERE / 'verification.json'
        ver = json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
        ver['words'] = rec
        p.write_text(json.dumps(ver, indent=1, sort_keys=True, ensure_ascii=False) + '\n', encoding='utf-8', newline='\n')
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
