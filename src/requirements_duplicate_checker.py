"""requirements-duplicate-checker-cli: find duplicate packages in requirements.txt, fail CI on a duplicate.

Reads one or more requirements.txt files (or folders of them), finds
every package name that appears twice in one file, and reports each
duplicate with the file, the line numbers, and the versions listed.
Report-only: it never rewrites a file. Exit 0 when every file is
clean, 1 when any duplicate is found, 2 for a usage error.

Package names are matched the way pip matches them (PEP 503): case
does not matter, and runs of '-', '_' and '.' count as one '-'.
So `Requests`, `requests`, `my-package` and `my_package` are the
same package. Extras do not make a new package: `requests[security]`
is `requests`.
"""

import argparse
import fnmatch
import json
import os
import re
import sys
from pathlib import Path

__version__ = "1.0.0"

NAME_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")
SEPARATOR_RE = re.compile(r"[-_.]+")


class Problem:
    """One problem in one file: path, line number, plain reason."""

    def __init__(self, path, line, reason):
        self.path = path
        self.line = line
        self.reason = reason

    def text(self):
        return f"{self.path}:{self.line}: {self.reason}"

    def as_dict(self):
        return {"file": self.path, "line": self.line, "reason": self.reason}


def normalize_name(name):
    """The canonical form of a package name, the way pip compares names.

    Lowercase, and every run of '-', '_' and '.' becomes one '-'.
    """
    return SEPARATOR_RE.sub("-", name).lower()


def strip_comment(line):
    """Remove a trailing comment: a '#' at the start, or after a space."""
    if line.startswith("#"):
        return ""
    for i, ch in enumerate(line):
        if ch == "#" and line[i - 1] in " \t":
            return line[:i]
    return line


def join_continuations(text):
    """Join lines ending in a backslash, keeping the first line number.

    Returns a list of (lineno, logical_line).
    """
    logical = []
    current = None
    start = 0
    for lineno, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.rstrip()
        if current is None:
            current = stripped
            start = lineno
        else:
            current += stripped
        if stripped.endswith("\\") and stripped != "\\":
            current = current[:-1]
            continue
        logical.append((start, current))
        current = None
    if current is not None:
        logical.append((start, current))
    return logical


def parse_requirement_line(line):
    """Parse one logical requirements line.

    Returns (canonical_name, shown_name, version_text) for a package
    entry, or None for a line that is not a package entry (blank,
    comment, option line, or a bare URL/VCS line). The version text
    is the specifier as written, or "" when no version is given.
    """
    stripped = line.strip()
    if not stripped:
        return None
    stripped = strip_comment(stripped).strip()
    if not stripped:
        return None
    if stripped.startswith("-"):
        return None  # option line: -r, -c, -e, --index-url, and the rest
    if stripped.startswith(("git+", "hg+", "svn+", "bzr+", "http://", "https://", "file:")):
        return None  # a bare URL or VCS line has no package name
    match = NAME_RE.match(stripped)
    if not match:
        return None
    shown = match.group(0)
    rest = stripped[match.end():]
    rest = rest.lstrip()
    if rest.startswith("["):
        end = rest.find("]")
        if end == -1:
            return None  # a broken extras list is not a package entry
        rest = rest[end + 1:].lstrip()
    if rest.startswith(";"):
        rest = ""  # only an environment marker: unpinned
    elif rest.startswith("@"):
        rest = "direct reference"
    elif ";" in rest:
        rest = rest.split(";", 1)[0].rstrip()
    version = rest
    return (normalize_name(shown), shown, version)


def scan_text(text, label):
    """Check one requirements document (as text) and return its problems.

    Duplicates are per file: the same package in another file is not
    a duplicate. Every repeat is reported at its own line, naming the
    first line and both versions.
    """
    problems = []
    seen = {}  # canonical name -> (first lineno, first version, first shown name)
    for lineno, logical in join_continuations(text):
        entry = parse_requirement_line(logical)
        if entry is None:
            continue
        canonical, shown, version = entry
        if canonical in seen:
            first_line, first_version, first_shown = seen[canonical]
            first_text = first_version if first_version else "no version given"
            this_text = version if version else "no version given"
            problems.append(Problem(
                label, lineno,
                f"duplicate package '{first_shown}' "
                f"(first on line {first_line}: {first_text}; "
                f"this line: {this_text})"))
        else:
            seen[canonical] = (lineno, version, shown)
    return problems


def check_file(path):
    """Check one file and return its problems."""
    label = str(path)
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        return [Problem(label, 1, f"cannot read file: {exc.strerror or exc}")]
    if b"\x00" in data:
        return [Problem(label, 1, "file contains a null byte, not a requirements file")]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return [Problem(label, 1, "file is not valid UTF-8")]
    return scan_text(text, label)


def is_requirements_name(path):
    """True when a file name looks like a requirements file."""
    return path.name.lower().startswith("requirements") and path.name.lower().endswith(".txt")


def collect(paths, recursive=True, exclude=()):
    """Gather the files to check and their problems.

    A file given directly is always checked, whatever its name. A
    folder is walked for files named requirements*.txt, recursively
    by default. `.git` folders are never walked, and directory links
    are never followed.
    """
    checked = []
    problems = []
    for given in paths:
        path = Path(given)
        if path.is_file():
            checked.append(path)
            problems.extend(check_file(path))
        elif path.is_dir():
            for root, dirs, files in os.walk(path, followlinks=False):
                dirs[:] = [d for d in dirs
                           if d != ".git"
                           and not any(fnmatch.fnmatch(d, pat) for pat in exclude)]
                for name in sorted(files):
                    candidate = Path(root) / name
                    if not is_requirements_name(candidate):
                        continue
                    if any(fnmatch.fnmatch(name, pat) for pat in exclude):
                        continue
                    checked.append(candidate)
                    problems.extend(check_file(candidate))
                if not recursive:
                    break
        else:
            problems.append(Problem(str(given), 1, "no such file or folder"))
    return checked, problems


def main(argv=None):
    """Entry point. Returns the exit code: 0 clean, 1 problems, 2 usage."""
    parser = argparse.ArgumentParser(
        prog="requirements_duplicate_checker",
        description="Find duplicate packages in requirements.txt files.")
    parser.add_argument("paths", nargs="*", metavar="PATH",
                        help="requirements file or folder to check")
    parser.add_argument("--stdin", action="store_true",
                        help="check one requirements file read from standard input")
    parser.add_argument("--json", action="store_true",
                        help="print the result as JSON (human lines go to standard error)")
    parser.add_argument("--no-recursive", action="store_true",
                        help="do not walk into subfolders")
    parser.add_argument("--exclude", action="append", default=[],
                        metavar="PATTERN",
                        help="skip files and folders matching this name pattern")
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    if args.stdin:
        if args.paths:
            parser.error("--stdin takes no paths")
        text = sys.stdin.read()
        checked = ["<stdin>"]
        problems = scan_text(text, "<stdin>")
    else:
        if not args.paths:
            parser.error("give at least one file or folder, or use --stdin")
        checked, problems = collect(args.paths,
                                    recursive=not args.no_recursive,
                                    exclude=args.exclude)

    files_with_problems = len({p.path for p in problems})
    if args.json:
        result = {
            "files_checked": len(checked),
            "problems": [p.as_dict() for p in problems],
            "files_with_problems": files_with_problems,
        }
        print(json.dumps(result, indent=2))
        for problem in problems:
            print(problem.text(), file=sys.stderr)
    else:
        for problem in problems:
            print(problem.text())
        if problems:
            print(f"Checked {len(checked)} file(s), "
                  f"{len(problems)} problem(s) in {files_with_problems} file(s).")
        else:
            print(f"OK — {len(checked)} file(s) checked, "
                  f"all requirements files are clean.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
