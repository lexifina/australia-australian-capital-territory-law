"""Prints an Australian Capital Territory Act, or one provision of it, as it stood in force on a given day, from this
repository's history.

    python tools/in_force_on.py 2010-01-01 human-rights-act-2004 5        # section 5 of the Human Rights Act 2004
    python tools/in_force_on.py 2010-01-01 human-rights-act-2004 27A      # section 27A
    python tools/in_force_on.py 2020-01-01 human-rights-act-2004          # the whole Act
    python tools/in_force_on.py 2020-01-01 human-rights-act-2004 sch-1    # schedule 1 (also: dictionary, intro, structure)

Each commit carries the day its change took effect in an `In-force:` trailer, which is the source of truth (not the
commit date, which is noon in Canberra on that day). The text in force on a day is the last commit on or before it.
Standard library only.
"""
import datetime as dt
import re
import subprocess
import sys


def git(*args: str) -> str:
    return subprocess.run(['git', *args], capture_output=True, text=True, encoding='utf-8', check=True).stdout


def order(path: str) -> tuple:
    """The order of the act's files: the title, the sections by number, the schedules."""
    name = path.split('/', 1)[1][:-3]
    if name == 'intro':
        return (0, 0, ())
    if name == 'structure':
        return (0, 1, ())
    if name == 'dictionary':
        return (2, 10**6, ())
    m = re.fullmatch(r's-(\d+)(.*)', name)
    if m:
        return (1, int(m.group(1)), (m.group(2),))
    if name.startswith('sch'):
        rest = name[len('sch'):].lstrip('-')
        n = re.match(r'\d+', rest)
        return (2, int(n.group(0)) if n else 0, (rest,))
    return (3, 0, (name,))


def file_for(key: str) -> str:
    """'5' -> s-005.md, '27A' -> s-027A.md; anything else is a file name ('sch-1', 'dictionary', 'intro')."""
    m = re.fullmatch(r'(\d+)([A-Z]*(?:\.\d+[A-Z]*)*)', key)
    if m:
        return f's-{int(m.group(1)):03d}{m.group(2)}.md'
    return key if key.endswith('.md') else f'{key}.md'


def commit_on(day: str, act: str) -> str:
    branch = 'future' if day > dt.date.today().isoformat() and git('branch', '--list', 'future').strip() else 'HEAD'
    out = git('log', '--format=%H%x09%(trailers:key=In-force,valueonly,separator=)', branch, '--', act)
    for line in out.splitlines():
        h, _, d = line.partition('\t')
        if d.strip() and d.strip() <= day:
            return h
    return ''


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    day, act = sys.argv[1], sys.argv[2]
    dt.date.fromisoformat(day)
    commit = commit_on(day, act)
    if not commit:
        sys.exit(f'{act} is not in the record on {day} (the record starts with its first point-in-time version).')
    if len(sys.argv) > 3:
        name = file_for(sys.argv[3])
        try:
            sys.stdout.write(git('show', f'{commit}:{act}/{name}'))
        except subprocess.CalledProcessError:
            sys.exit(f'{act}/{name} was not in the text in force on {day}.')
        return
    files = [f for f in git('ls-tree', '-r', '--name-only', commit, f'{act}/').split('\n')
             if f.endswith('.md') and not f.endswith('/README.md')]
    files = sorted(files, key=order)
    data = subprocess.run(['git', 'cat-file', '--batch'], input=''.join(f'{commit}:{f}\n' for f in files).encode('utf-8'),
                          capture_output=True, check=True).stdout
    pos = 0
    for _ in files:
        nl = data.index(b'\n', pos)
        size = int(data[pos:nl].split()[2])
        sys.stdout.write(data[nl + 1:nl + 1 + size].decode('utf-8') + '\n')
        pos = nl + 1 + size + 1


if __name__ == '__main__':
    main()
