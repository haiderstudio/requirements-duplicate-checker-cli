# requirements-duplicate-checker-cli

Find duplicate packages in requirements.txt, fail CI when a package appears twice.

[![CI](https://github.com/haiderstudio/requirements-duplicate-checker-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/haiderstudio/requirements-duplicate-checker-cli/actions/workflows/ci.yml) [![Licence: MIT](https://img.shields.io/badge/Licence-MIT-yellow.svg)](LICENSE)

## What it does

`requirements_duplicate_checker` reads a requirements.txt file
and finds every package that appears twice in it — the same name,
any version — and reports each duplicate with the file, the line
numbers, and the versions listed. It exits 1 when any duplicate
is found, so it can fail a CI run. A clean file exits 0.

- A duplicated entry with two different pins installs
  unpredictably — which version wins depends on the order pip
  reads them — and pip stays silent about it. This tool catches
  the duplicate before the file ships, and points at both lines.
- Every repeat is reported, not just the first: a package listed
  three times is reported twice, at lines 2 and 3.
- Names are matched the way pip matches them (PEP 503): case
  does not matter, and runs of `-`, `_` and `.` count as one
  `-`. So `Requests` and `requests` are the same package, and
  so are `my-package`, `my_package` and `my.package`. Extras
  do not make a new package: `requests[security]` is `requests`.
- A package listed once in each of two files is not a duplicate
  — duplicates are checked within one file.
- Blank lines, comments, and option lines (`-r`, `-c`, `-e`,
  `--index-url`, and the rest) are skipped. An `-r` include is
  not followed — the file you point at is the file that is
  checked. A line ending in a backslash is joined with the
  next line, the way pip reads it, and reported at its first
  line. A direct reference (`name @ https://...`) still counts
  as the package.
- A file given directly on the command line is always checked,
  whatever its name. A folder is walked for files named
  `requirements*.txt` (`requirements.txt`,
  `requirements-dev.txt`, and the like), recursively by
  default (`--no-recursive` stops at the top level). `.git`
  folders are never walked, and directory links are never
  followed. `--exclude` skips files and folders by name pattern.
- `--json` prints the result as JSON (the human-readable
  lines go to standard error), and `--stdin` checks one
  requirements file read from standard input.

## Who it is for

Anyone who keeps a requirements.txt — Python developers, teams
that pin dependencies in version control, and anyone who has
ever wondered why a fresh install picked the wrong version of
a package.

## What it does NOT do

- It does not check whether a version is *pinned* — that is
  Day 28's requirements-pin-checker-cli. A file can be fully
  pinned and still list a package twice; a file can be
  unpinned and still have no duplicates. Pinning and
  duplicates are two different checks on the same file.
- It does not check whether a version *exists* or whether
  packages conflict with each other. That is a resolver's
  job, not a duplicate check.
- It does not convert or rewrite anything. It is report-only,
  and it never touches the network.

## How this differs

- Raknaos/requirements-lint (0 stars) is a single Python
  script that lints requirements.txt for duplicates and
  unpinned packages. It was written in one sitting in
  September 2026 and never touched again — no tests, no
  examples, no line numbers in its reports. This tool is
  duplicates only, one job, with a test suite, per-line
  reporting, a folder walk, and a CI exit code.
- There is no exact-name rival: a GitHub search for
  `"requirements-duplicate-checker-cli"` returns 0 results
  (checked Nov 8, 2026).

## Install

Python 3.10 or newer, nothing else to install.

```sh
git clone https://github.com/haiderstudio/requirements-duplicate-checker-cli.git
cd requirements-duplicate-checker-cli
```

## Usage

Check one clean file:

```sh
python3 src/requirements_duplicate_checker.py examples/requirements.txt
```

```text
OK — 1 file(s) checked, all requirements files are clean.
```

Check a file with duplicates:

```sh
python3 src/requirements_duplicate_checker.py examples/requirements-duplicates.txt
```

```text
examples/requirements-duplicates.txt:4: duplicate package 'requests' (first on line 1: ==2.28.0; this line: ==2.31.0)
examples/requirements-duplicates.txt:6: duplicate package 'my-package' (first on line 5: ==1.0; this line: ==2.0)
examples/requirements-duplicates.txt:7: duplicate package 'requests' (first on line 1: ==2.28.0; this line: ==2.31.0)
examples/requirements-duplicates.txt:8: duplicate package 'Django' (first on line 2: ==4.2.7; this line: ==4.2.7)
Checked 1 file(s), 4 problem(s) in 1 file(s).
```

The exit code is 1, so a CI run fails.

Check a whole folder:

```sh
python3 src/requirements_duplicate_checker.py examples
```

```text
examples/requirements-duplicates.txt:4: duplicate package 'requests' (first on line 1: ==2.28.0; this line: ==2.31.0)
examples/requirements-duplicates.txt:6: duplicate package 'my-package' (first on line 5: ==1.0; this line: ==2.0)
examples/requirements-duplicates.txt:7: duplicate package 'requests' (first on line 1: ==2.28.0; this line: ==2.31.0)
examples/requirements-duplicates.txt:8: duplicate package 'Django' (first on line 2: ==4.2.7; this line: ==4.2.7)
examples/configs/requirements.txt:3: duplicate package 'celery' (first on line 1: ==5.3.4; this line: ==5.3.4)
Checked 4 file(s), 5 problem(s) in 2 file(s).
```

Check one file from standard input:

```sh
printf 'requests==2.28.0\nRequests==2.31.0\n' | python3 src/requirements_duplicate_checker.py --stdin
```

```text
<stdin>:2: duplicate package 'requests' (first on line 1: ==2.28.0; this line: ==2.31.0)
Checked 1 file(s), 1 problem(s) in 1 file(s).
```

Machine-readable output:

```sh
python3 src/requirements_duplicate_checker.py examples/configs/requirements.txt --json
```

```json
{
  "files_checked": 1,
  "problems": [
    {
      "file": "examples/configs/requirements.txt",
      "line": 3,
      "reason": "duplicate package 'celery' (first on line 1: ==5.3.4; this line: ==5.3.4)"
    }
  ],
  "files_with_problems": 1
}
```

## Examples

- `examples/requirements.txt` — a clean requirements file.
  Passes.
- `examples/requirements-duplicates.txt` — four problems in
  one file: the same package twice with different versions,
  a case-only difference (`Requests` vs `requests`), a
  separator-only difference (`my-package` vs `my_package`),
  and an extras variant (`requests[security]`). Fails with
  exit 1.
- `examples/requirements-dev.txt` — a clean dev requirements
  file. Passes.
- `examples/configs/requirements.txt` — one duplicate, in a
  subfolder. Fails with exit 1.
- `sh examples/run-example.sh` runs the checker on the whole
  `examples/` folder. Its output is in
  `examples/sample-output.md`.

## How it is tested

```sh
python3 tests/test_requirements_duplicate_checker.py
```

The tests cover: name matching (case, separators, extras,
markers, direct references, single-character names),
skipped lines (blank, comment, option, URL, VCS, broken
extras), line continuations, every duplicate shape with
exact line numbers and both versions (same version,
different versions, unpinned, three occurrences, extras,
direct references), same package in two files passing,
folder walking (requirements names only, `.git` skipped,
`--no-recursive`, `--exclude`), `--stdin`, `--json`,
exit codes 0/1/2, unreadable and non-UTF-8 files, and
the bundled examples (the sample output is compared
byte-for-byte against a fresh run, and the outputs quoted
in this README are checked against real runs).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Licence

MIT — see [LICENSE](LICENSE).

## Original work

This tool was written from scratch for this repo. Package
name matching follows PEP 503, the rule pip itself uses.
The idea of linting a requirements file is a common one;
the code, the wording, and the tests are original.
