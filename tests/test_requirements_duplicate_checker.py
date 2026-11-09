"""Tests for requirements-duplicate-checker-cli. Standard library only."""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SRC = ROOT / "src" / "requirements_duplicate_checker.py"

sys.path.insert(0, str(ROOT / "src"))
import requirements_duplicate_checker as rdc
from requirements_duplicate_checker import (
    Problem, check_file, collect, join_continuations,
    normalize_name, parse_requirement_line, scan_text,
)


def problems_as_tuples(text):
    return [(p.line, p.reason) for p in scan_text(text, "test.txt")]


class NormalizeNameTests(unittest.TestCase):
    def test_lowercase(self):
        self.assertEqual(normalize_name("Requests"), "requests")
        self.assertEqual(normalize_name("DJANGO"), "django")

    def test_separators_collapse(self):
        self.assertEqual(normalize_name("my-package"), "my-package")
        self.assertEqual(normalize_name("my_package"), "my-package")
        self.assertEqual(normalize_name("my.package"), "my-package")
        self.assertEqual(normalize_name("my--package"), "my-package")
        self.assertEqual(normalize_name("my_-package"), "my-package")
        self.assertEqual(normalize_name("my_.package"), "my-package")

    def test_digits_kept(self):
        self.assertEqual(normalize_name("boto3"), "boto3")
        self.assertEqual(normalize_name("h11"), "h11")


class ParseLineTests(unittest.TestCase):
    def test_pinned(self):
        self.assertEqual(parse_requirement_line("requests==2.31.0"),
                         ("requests", "requests", "==2.31.0"))

    def test_unpinned(self):
        self.assertEqual(parse_requirement_line("flask"),
                         ("flask", "flask", ""))

    def test_version_operators(self):
        self.assertEqual(parse_requirement_line("numpy>=1.24,<2")[2], ">=1.24,<2")
        self.assertEqual(parse_requirement_line("Django~=4.2")[2], "~=4.2")
        self.assertEqual(parse_requirement_line("celery!=5.3.0")[2], "!=5.3.0")

    def test_extras(self):
        self.assertEqual(parse_requirement_line("requests[security]==2.31.0"),
                         ("requests", "requests", "==2.31.0"))
        self.assertEqual(parse_requirement_line("uvicorn[standard]"),
                         ("uvicorn", "uvicorn", ""))
        self.assertEqual(parse_requirement_line("requests [security] == 2.31.0"),
                         ("requests", "requests", "== 2.31.0"))

    def test_marker_stripped(self):
        self.assertEqual(parse_requirement_line('requests==2.31.0; python_version > "3.8"'),
                         ("requests", "requests", "==2.31.0"))
        self.assertEqual(parse_requirement_line("tomli; python_version < '3.11'"),
                         ("tomli", "tomli", ""))

    def test_direct_reference(self):
        name, shown, version = parse_requirement_line(
            "requests @ https://example.com/requests-2.31.tar.gz")
        self.assertEqual(name, "requests")
        self.assertEqual(version, "direct reference")

    def test_comments_and_blanks(self):
        self.assertIsNone(parse_requirement_line(""))
        self.assertIsNone(parse_requirement_line("   "))
        self.assertIsNone(parse_requirement_line("# requests==1.0"))
        self.assertIsNone(parse_requirement_line("  # a comment"))
        self.assertEqual(parse_requirement_line("requests==2.31.0  # pinned"),
                         ("requests", "requests", "==2.31.0"))

    def test_option_lines_skipped(self):
        self.assertIsNone(parse_requirement_line("-r other.txt"))
        self.assertIsNone(parse_requirement_line("-c constraints.txt"))
        self.assertIsNone(parse_requirement_line("-e ."))
        self.assertIsNone(parse_requirement_line("--index-url https://pypi.org/simple"))
        self.assertIsNone(parse_requirement_line("--extra-index-url https://example.com/simple"))
        self.assertIsNone(parse_requirement_line("--no-index"))

    def test_url_and_vcs_lines_skipped(self):
        self.assertIsNone(parse_requirement_line("git+https://github.com/x/y.git"))
        self.assertIsNone(parse_requirement_line("https://example.com/pkg.tar.gz"))
        self.assertIsNone(parse_requirement_line("file:///tmp/pkg.whl"))

    def test_broken_extras_not_an_entry(self):
        self.assertIsNone(parse_requirement_line("requests[security"))
        self.assertIsNone(parse_requirement_line("@@@"))
        self.assertIsNone(parse_requirement_line("; python_version > '3.8'"))

    def test_single_character_name(self):
        self.assertEqual(parse_requirement_line("x==1.0"), ("x", "x", "==1.0"))


class JoinContinuationsTests(unittest.TestCase):
    def test_plain_lines(self):
        self.assertEqual(join_continuations("a==1\nb==2\n"),
                         [(1, "a==1"), (2, "b==2")])

    def test_continuation_joins_and_keeps_first_line(self):
        self.assertEqual(join_continuations("requests==2.31.0 \\\n  --hash=sha256:abc\nflask\n"),
                         [(1, "requests==2.31.0   --hash=sha256:abc"), (3, "flask")])

    def test_trailing_backslash_at_eof(self):
        self.assertEqual(join_continuations("requests==2.31.0 \\"),
                         [(1, "requests==2.31.0 ")])


class ScanTextTests(unittest.TestCase):
    def test_clean_file(self):
        self.assertEqual(scan_text("requests==2.31.0\nDjango==4.2.7\n", "t"), [])

    def test_empty_file(self):
        self.assertEqual(scan_text("", "t"), [])
        self.assertEqual(scan_text("# only comments\n\n", "t"), [])

    def test_same_package_twice_different_versions(self):
        found = problems_as_tuples("requests==2.28.0\nflask\nrequests==2.31.0\n")
        self.assertEqual(len(found), 1)
        line, reason = found[0]
        self.assertEqual(line, 3)
        self.assertIn("duplicate package 'requests'", reason)
        self.assertIn("first on line 1", reason)
        self.assertIn("==2.28.0", reason)
        self.assertIn("==2.31.0", reason)

    def test_same_package_twice_same_version(self):
        found = problems_as_tuples("celery==5.3.4\nredis==5.0.1\ncelery==5.3.4\n")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][0], 3)

    def test_case_insensitive(self):
        found = problems_as_tuples("requests==2.28.0\nRequests==2.31.0\n")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][0], 2)

    def test_separator_variants_are_one_package(self):
        found = problems_as_tuples("my-package==1.0\nmy_package==2.0\nmy.package==3.0\n")
        self.assertEqual(len(found), 2)
        self.assertEqual([line for line, _ in found], [2, 3])

    def test_extras_count_as_same_package(self):
        found = problems_as_tuples("requests==2.28.0\nrequests[security]==2.31.0\n")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][0], 2)

    def test_unpinned_duplicates(self):
        found = problems_as_tuples("flask\nflask\n")
        self.assertEqual(len(found), 1)
        self.assertIn("no version given", found[0][1])

    def test_three_occurrences_reported_twice(self):
        found = problems_as_tuples("a==1\na==2\na==3\n")
        self.assertEqual(len(found), 2)
        self.assertEqual([line for line, _ in found], [2, 3])

    def test_comments_and_options_do_not_count(self):
        text = ("# requests==1.0\n"
                "-r other.txt\n"
                "requests==2.31.0\n"
                "# requests==9.9\n")
        self.assertEqual(scan_text(text, "t"), [])

    def test_continuation_lines(self):
        text = ("requests==2.28.0 \\\n"
                "  --hash=sha256:abc\n"
                "requests==2.31.0\n")
        found = problems_as_tuples(text)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][0], 3)

    def test_direct_reference_counts(self):
        text = ("requests==2.31.0\n"
                "requests @ https://example.com/r.tar.gz\n")
        found = problems_as_tuples(text)
        self.assertEqual(len(found), 1)
        self.assertIn("direct reference", found[0][1])

    def test_problem_text_format(self):
        problem = scan_text("a==1\na==2\n", "req.txt")[0]
        self.assertEqual(problem.text(),
                         "req.txt:2: duplicate package 'a' "
                         "(first on line 1: ==1; this line: ==2)")
        self.assertEqual(problem.as_dict(), {
            "file": "req.txt", "line": 2,
            "reason": "duplicate package 'a' (first on line 1: ==1; this line: ==2)",
        })


class CheckFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text, encoding="utf-8"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode(encoding))
        return path

    def test_clean_file(self):
        path = self.write("requirements.txt", "a==1\nb==2\n")
        self.assertEqual(check_file(path), [])

    def test_missing_file(self):
        found = check_file(self.root / "nope.txt")
        self.assertEqual(len(found), 1)
        self.assertIn("cannot read file", found[0].reason)

    def test_not_utf8(self):
        path = self.root / "requirements.txt"
        path.write_bytes(b"requests==1.0\n\xff\xfe\n")
        found = check_file(path)
        self.assertEqual(len(found), 1)
        self.assertIn("not valid UTF-8", found[0].reason)

    def test_null_byte(self):
        path = self.root / "requirements.txt"
        path.write_bytes(b"requests==1.0\n\x00\n")
        found = check_file(path)
        self.assertEqual(len(found), 1)
        self.assertIn("null byte", found[0].reason)


class CollectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def test_file_given_directly_is_checked_whatever_its_name(self):
        path = self.write("deps.txt", "a==1\na==2\n")
        checked, problems = collect([str(path)])
        self.assertEqual(len(checked), 1)
        self.assertEqual(len(problems), 1)

    def test_same_package_in_two_files_is_not_a_duplicate(self):
        self.write("requirements.txt", "a==1\n")
        self.write("requirements-dev.txt", "a==2\n")
        checked, problems = collect([str(self.root)])
        self.assertEqual(len(checked), 2)
        self.assertEqual(problems, [])

    def test_folder_walk_only_requirements_names(self):
        self.write("requirements.txt", "a==1\n")
        self.write("requirements-dev.txt", "b==1\n")
        self.write("notes.txt", "a==1\na==2\n")  # not a requirements file
        self.write("README.md", "a==1\na==2\n")
        checked, problems = collect([str(self.root)])
        self.assertEqual(sorted(p.name for p in checked),
                         ["requirements-dev.txt", "requirements.txt"])
        self.assertEqual(problems, [])

    def test_folder_walk_recursive(self):
        self.write("requirements.txt", "a==1\n")
        self.write("configs/requirements.txt", "b==1\nb==2\n")
        checked, problems = collect([str(self.root)])
        self.assertEqual(len(checked), 2)
        self.assertEqual(len(problems), 1)

    def test_no_recursive(self):
        self.write("requirements.txt", "a==1\n")
        self.write("configs/requirements.txt", "b==1\nb==2\n")
        checked, problems = collect([str(self.root)], recursive=False)
        self.assertEqual(len(checked), 1)
        self.assertEqual(problems, [])

    def test_git_folder_skipped(self):
        self.write("requirements.txt", "a==1\n")
        self.write(".git/requirements.txt", "b==1\nb==2\n")
        checked, problems = collect([str(self.root)])
        self.assertEqual(len(checked), 1)
        self.assertEqual(problems, [])

    def test_exclude(self):
        self.write("requirements.txt", "a==1\na==2\n")
        self.write("requirements-dev.txt", "b==1\nb==2\n")
        checked, problems = collect([str(self.root)], exclude=["requirements-dev.txt"])
        self.assertEqual(len(checked), 1)
        self.assertEqual(len(problems), 1)

    def test_missing_path(self):
        checked, problems = collect([str(self.root / "nope.txt")])
        self.assertEqual(checked, [])
        self.assertEqual(len(problems), 1)
        self.assertIn("no such file or folder", problems[0].reason)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args, stdin=None):
        return subprocess.run(
            [sys.executable, str(SRC), *args],
            input=stdin, capture_output=True, text=True, cwd=self.root)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def test_clean_exit_0(self):
        path = self.write("requirements.txt", "a==1\nb==2\n")
        result = self.run_cli(str(path))
        self.assertEqual(result.returncode, 0)
        self.assertIn("OK — 1 file(s) checked", result.stdout)

    def test_duplicate_exit_1(self):
        path = self.write("requirements.txt", "a==1\na==2\n")
        result = self.run_cli(str(path))
        self.assertEqual(result.returncode, 1)
        self.assertIn("duplicate package 'a'", result.stdout)
        self.assertIn("Checked 1 file(s), 1 problem(s) in 1 file(s).", result.stdout)

    def test_no_args_exit_2(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 2)

    def test_missing_path_exit_1(self):
        result = self.run_cli(str(self.root / "nope.txt"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("no such file or folder", result.stdout)

    def test_stdin(self):
        result = self.run_cli("--stdin", stdin="a==1\na==2\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("<stdin>:2: duplicate package 'a'", result.stdout)

    def test_stdin_with_paths_is_usage_error(self):
        result = self.run_cli("--stdin", "requirements.txt")
        self.assertEqual(result.returncode, 2)

    def test_json(self):
        path = self.write("requirements.txt", "a==1\na==2\n")
        result = self.run_cli(str(path), "--json")
        self.assertEqual(result.returncode, 1)
        data = json.loads(result.stdout)
        self.assertEqual(data["files_checked"], 1)
        self.assertEqual(data["files_with_problems"], 1)
        self.assertEqual(len(data["problems"]), 1)
        self.assertEqual(data["problems"][0]["line"], 2)
        self.assertIn("duplicate package 'a'", result.stderr)

    def test_json_clean(self):
        path = self.write("requirements.txt", "a==1\n")
        result = self.run_cli(str(path), "--json")
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data["problems"], [])

    def test_version(self):
        result = self.run_cli("--version")
        self.assertEqual(result.returncode, 0)
        self.assertIn("1.0.0", result.stdout)

    def test_bad_flag_exit_2(self):
        result = self.run_cli("--nope")
        self.assertEqual(result.returncode, 2)

    def test_input_files_unchanged(self):
        path = self.write("requirements.txt", "a==1\na==2\n")
        before = path.read_text()
        self.run_cli(str(path))
        self.assertEqual(path.read_text(), before)

    def test_folder_run(self):
        self.write("requirements.txt", "a==1\n")
        self.write("configs/requirements.txt", "b==1\nb==2\n")
        result = self.run_cli(str(self.root))
        self.assertEqual(result.returncode, 1)
        self.assertIn("Checked 2 file(s), 1 problem(s) in 1 file(s).", result.stdout)


class ExamplesTests(unittest.TestCase):
    def test_run_example_output_matches_sample(self):
        result = subprocess.run(
            ["sh", "examples/run-example.sh"],
            capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 0)
        sample = (ROOT / "examples" / "sample-output.md").read_text()
        expected = "```text\n" + result.stdout + "```\n\n"
        self.assertTrue(sample.startswith(expected),
                        "sample-output.md does not match a fresh run")

    def test_examples_checked(self):
        checked, problems = collect([str(ROOT / "examples")])
        self.assertEqual(len(checked), 4)
        self.assertEqual(len(problems), 5)
        self.assertEqual(len({p.path for p in problems}), 2)

    def test_readme_quoted_outputs_are_real(self):
        readme = (ROOT / "README.md").read_text()
        for block in [
            "OK — 1 file(s) checked, all requirements files are clean.",
            "examples/requirements-duplicates.txt:4: duplicate package 'requests' "
            "(first on line 1: ==2.28.0; this line: ==2.31.0)",
            "examples/requirements-duplicates.txt:8: duplicate package 'Django' "
            "(first on line 2: ==4.2.7; this line: ==4.2.7)",
            "Checked 4 file(s), 5 problem(s) in 2 file(s).",
        ]:
            self.assertIn(block, readme, f"README is missing real output: {block!r}")


class HygieneTests(unittest.TestCase):
    def test_gitignore_first_line_is_env(self):
        lines = (ROOT / ".gitignore").read_text().splitlines()
        self.assertEqual(lines[0], ".env")

    def test_no_banned_words_outside_tests(self):
        banned = ["TODO", "FIXME", "XXX", "lorem", "coming soon"]
        for path in ROOT.rglob("*"):
            if not path.is_file():
                continue
            if ".git" in path.parts or "__pycache__" in path.parts:
                continue
            if "tests" in path.parts:
                continue
            text = path.read_text(errors="replace")
            for word in banned:
                self.assertNotIn(word, text,
                                 f"{word} found in {path.relative_to(ROOT)}")

    def test_no_absolute_paths(self):
        for path in ROOT.rglob("*"):
            if not path.is_file():
                continue
            if ".git" in path.parts or "__pycache__" in path.parts:
                continue
            if "tests" in path.parts:
                continue
            text = path.read_text(errors="replace")
            self.assertNotIn("/home/hatch", text,
                             f"absolute path in {path.relative_to(ROOT)}")
            self.assertNotIn("/Users/", text,
                             f"absolute path in {path.relative_to(ROOT)}")
            self.assertNotIn("/tmp/", text,
                             f"absolute path in {path.relative_to(ROOT)}")

    def test_stdlib_only_imports(self):
        allowed_src = {
            "argparse", "fnmatch", "json", "os", "re", "sys", "pathlib",
        }
        allowed_tests = allowed_src | {
            "io", "subprocess", "tempfile", "unittest", "ast",
            "requirements_duplicate_checker",
        }
        import ast
        for path, allowed in [(SRC, allowed_src),
                              (Path(__file__), allowed_tests)]:
            tree = ast.parse(path.read_text())
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(a.name.split(".")[0] for a in node.names)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        imported.add(node.module.split(".")[0])
            self.assertTrue(imported <= allowed,
                            f"{path.name} imports non-stdlib: "
                            f"{imported - allowed}")
            self.assertNotIn("yaml", imported)

    def test_no_yaml_anywhere_in_source(self):
        text = SRC.read_text()
        self.assertNotIn("yaml", text)

    def test_no_secrets_in_repo(self):
        for path in ROOT.rglob("*"):
            if not path.is_file():
                continue
            if ".git" in path.parts or "__pycache__" in path.parts:
                continue
            if "tests" in path.parts:
                continue
            text = path.read_text(errors="replace").lower()
            for marker in ["api_key", "api-key", "password =", "secret ="]:
                self.assertNotIn(marker, text,
                                 f"possible secret in {path.relative_to(ROOT)}")


if __name__ == "__main__":
    unittest.main()
