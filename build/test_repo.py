"""Functional smoke test of the built Australian Capital Territory law repository: the things a reader does with it
work. Standard library only.

    python -X utf8 scripts/australia-australian-capital-territory-law/test_repo.py [--repo ...] [--record]
    python -X utf8 build/test_repo.py                    # from inside the repository

It asserts that:
1. `git blame` on a changed provision names amending Acts as authors of its lines;
2. tools/in_force_on.py gives the text before and after known amendments, and each equals the file at that Act's
   commit (and the commit before it); a day before an Act's record starts is refused;
3. `git log --follow` on a file lists its changes newest first, by amending Acts;
4. every tag resolves to a commit, every commit has an `In-force:` trailer that git parses, and no subject is longer
   than 72 characters;
5. no commit is authored by Lexifina except the documentation commit (the last one), and Lexifina commits them all;
6. every git or tool command in the README's table runs and prints something.
With --record the result is written to verification.json next to this script (for REPRODUCE.md).
"""
import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LAW = re.compile(r'^.+ \((?:A|SL|Ord)\d{4}-\d+\)$')
PUBLISHER = "ACT Parliamentary Counsel's Office (ACT Legislation Register)"
# (file, amending law, day before, day it took effect)
KNOWN = [('crimes-act-1900/s-019.md', 'A2022-13', '2022-08-16', '2022-08-17'),
         ('crimes-act-1900/s-300.md', 'A2024-16', '2024-04-25', '2024-04-26'),
         ('land-titles-act-1925/s-048.md', 'A2020-16', '2020-05-31', '2020-06-01')]


def default_repo() -> Path:
    if (HERE.parent / '.git').exists():
        return HERE.parent
    return HERE.parents[2] / 'australia-australian-capital-territory-law-build'


def ok_author(a: str) -> bool:
    return bool(LAW.match(a) or a == PUBLISHER)


class Smoke:
    def __init__(self, repo: Path):
        self.repo = repo
        self.results: list[tuple[str, bool, str]] = []

    def git(self, *args: str) -> str:
        out = subprocess.run(['git', *args], cwd=self.repo, capture_output=True, text=True, encoding='utf-8')
        if out.returncode:
            raise AssertionError(f'git {" ".join(args)}: {out.stderr.strip()}')
        return out.stdout

    def tool(self, *args: str, ok: bool = True) -> str:
        out = subprocess.run([sys.executable, '-X', 'utf8', 'tools/in_force_on.py', *args], cwd=self.repo,
                             capture_output=True, text=True, encoding='utf-8')
        if ok and out.returncode:
            raise AssertionError(f'in_force_on {" ".join(args)}: {out.stderr.strip()}')
        if not ok and not out.returncode:
            raise AssertionError(f'in_force_on {" ".join(args)} should have failed')
        return out.stdout

    def check(self, name: str, fn):
        try:
            detail = fn() or ''
            self.results.append((name, True, detail))
            print(f'PASS {name}: {detail}')
        except AssertionError as exc:
            self.results.append((name, False, str(exc)))
            print(f'FAIL {name}: {exc}')

    # 1
    def blame(self):
        path = KNOWN[0][0]
        out = self.git('blame', '--line-porcelain', 'HEAD', '--', path)
        authors = {l[7:] for l in out.splitlines() if l.startswith('author ')}
        assert authors, 'no blame output'
        assert all(ok_author(a) for a in authors), f'unexpected authors {authors}'
        laws = [a for a in authors if LAW.match(a)]
        assert any('(A2026-4)' in a for a in laws), f'A2026-4 not among the authors {authors}'
        assert len(laws) >= 2, f'only {authors}'
        return f'{path} lines by {len(authors)} authors, {len(laws)} Acts'

    # 2
    def on_date(self):
        done = []
        for path, law, before, after in KNOWN:
            slug, name = path.split('/')
            prov = re.match(r's-0*(\w+)\.md', name).group(1)
            sha = self.git('log', '--format=%H', f'--author=({law})', 'HEAD', '--', path).split()
            assert sha, f'{path}: no commit by {law}'
            sha = sha[-1]
            inf = self.git('log', '-1', '--format=%(trailers:key=In-force,valueonly,separator=)', sha).strip()
            assert inf == after, f'{path}: {law} commit In-force {inf}, expected {after}'
            a, b = self.tool(before, slug, prov), self.tool(after, slug, prov)
            assert a and b and a != b, f'{path}: no change between {before} and {after}'
            assert b == self.git('show', f'{sha}:{path}'), f'{path} on {after} is not the file at {law}'
            assert a == self.git('show', f'{sha}~1:{path}'), f'{path} on {before} is not the file before {law}'
            done.append(f'{path} {before} != {after} ({law})')
        whole = self.tool('2020-01-01', 'crimes-act-1900')
        assert 'An Act to consolidate the statutes relating to criminal law' in whole and '# 19 ' in whole, \
            'whole Crimes Act on 2020-01-01 incomplete'
        first = self.git('log', '--reverse', '--format=%(trailers:key=In-force,valueonly,separator=)', 'HEAD', '--',
                         'multiculturalism-act-2023').split()[0]
        assert first == '2023-09-01', f'multiculturalism-act-2023 starts {first}'
        self.tool('2023-08-31', 'multiculturalism-act-2023', ok=False)
        return '; '.join(done) + '; a day before the record is refused'

    # 3
    def follow(self):
        checked = []
        for path, _, _, _ in KNOWN:
            out = self.git('log', '--follow', '--format=%an%x09%(trailers:key=In-force,valueonly,separator=)', 'HEAD',
                           '--', path)
            rows = [l.split('\t') for l in out.splitlines() if l]
            rows = [r for r in rows if r[0] != 'Lexifina']
            assert len(rows) >= 3, f'{path}: {len(rows)} commits'
            days = [d.strip() for _, d in rows]
            assert days == sorted(days, reverse=True), f'{path}: not in date order {days}'
            assert all(ok_author(a) for a, _ in rows), f'{path}: authors {[a for a, _ in rows]}'
            assert sum(1 for a, _ in rows if LAW.match(a)) >= 2, f'{path}: fewer than two Acts'
            checked.append(f'{path} {len(rows)} commits')
        return '; '.join(checked)

    # 4
    def tags_and_trailers(self):
        tags = self.git('tag').split()
        assert tags, 'no tags'
        kinds = self.git('for-each-ref', '--format=%(objecttype)', 'refs/tags').split()
        assert len(kinds) == len(tags) and set(kinds) == {'commit'}, 'a tag does not point at a commit'
        rows = self.git('log', '--format=%H%x09%an%x09%(trailers:key=In-force,valueonly,separator=)%x09%s',
                        'HEAD').splitlines()
        missing = [r for r in rows if not r.split('\t')[2].strip()]
        assert not missing, f'{len(missing)} commits without In-force, e.g. {missing[:2]}'
        bad = [r for r in rows if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', r.split('\t')[2].strip())]
        assert not bad, f'malformed In-force {bad[:2]}'
        long = [r for r in rows if len(r.split('\t', 3)[3]) > 72]
        assert not long, f'{len(long)} subjects over 72 characters, e.g. {long[:1]}'
        return f'{len(tags)} tags resolve; {len(rows)} commits carry In-force and a subject of at most 72 characters'

    # 5
    def lexifina(self):
        rows = self.git('log', '--format=%H%x09%an%x09%ae%x09%cn', 'HEAD').splitlines()
        by_lex = [r for r in rows if r.split('\t')[1] == 'Lexifina']
        assert by_lex and by_lex[0] == rows[0], f'{len(by_lex)} commits by Lexifina'
        assert all(r.split('\t')[3] == 'Lexifina' for r in rows), 'a committer other than Lexifina'
        law = [r for r in rows if r.split('\t')[1] != 'Lexifina']
        authors = {r.split('\t')[1] for r in law}
        assert all(ok_author(a) for a in authors), f'unexpected authors {[a for a in authors if not ok_author(a)][:5]}'
        assert all(r.split('\t')[2].endswith('.invalid') for r in law), 'a law author address not ending .invalid'
        first_doc = rows.index(by_lex[-1])
        assert all(r.split('\t')[1] == 'Lexifina' for r in rows[:first_doc + 1]), 'a law commit after documentation'
        return f'only the documentation commits ({len(by_lex)}) are by Lexifina; {len(authors)} distinct authors'

    # 6
    def readme(self):
        text = (self.repo / 'README.md').read_text(encoding='utf-8')
        table = text[text.index('## The questions'):text.index('## Dates')]
        cmds = [c for c in re.findall(r'`([^`]+)`', table) if c.startswith(('git ', 'python '))]
        assert len(cmds) >= 8, f'only {len(cmds)} commands found'
        for c in cmds:
            args = shlex.split(c)
            if args[0] == 'python':
                args[0] = sys.executable
                args.insert(1, '-X')
                args.insert(2, 'utf8')
            out = subprocess.run(args, cwd=self.repo, capture_output=True, text=True, encoding='utf-8')
            assert out.returncode == 0, f'{c}: exit {out.returncode} {out.stderr.strip()[:200]}'
            assert out.stdout.strip(), f'{c}: printed nothing'
        return f'{len(cmds)} README commands ran and printed output'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default=None)
    ap.add_argument('--record', action='store_true')
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    s = Smoke(Path(args.repo) if args.repo else default_repo())
    s.check('blame names an Act', s.blame)
    s.check('text before and after an amendment', s.on_date)
    s.check('log --follow in date order', s.follow)
    s.check('tags resolve, In-force on every commit', s.tags_and_trailers)
    s.check('only the documentation commit is by Lexifina', s.lexifina)
    s.check('README commands run', s.readme)
    ok = all(r[1] for r in s.results)
    print(f'{sum(r[1] for r in s.results)}/{len(s.results)} passed')
    if args.record:
        p = HERE / 'verification.json'
        rec = json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
        rec['smoke'] = {'passed': sum(r[1] for r in s.results), 'total': len(s.results),
                        'results': [{'test': n, 'ok': o, 'detail': d} for n, o, d in s.results]}
        p.write_text(json.dumps(rec, indent=1, sort_keys=True, ensure_ascii=False) + '\n', encoding='utf-8',
                     newline='\n')
    raise SystemExit(0 if ok else 1)


if __name__ == '__main__':
    main()
