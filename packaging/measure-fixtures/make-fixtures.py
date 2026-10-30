#!/usr/bin/env python3
"""Build the synthetic benchmark repositories and tasks.json for packaging/measure.py.

Every repository is invented for this benchmark: no client code, data, names or
URLs. Each one is a git repository with one commit and no remote, because
measure.py clones it for every run. Nothing here is a measured result.

    python3 packaging/measure-fixtures/make-fixtures.py /tmp/af-bench
    python3 packaging/measure-fixtures/make-fixtures.py /tmp/af-bench --only fix-off-by-one

The output directory must not exist or must be empty. Run the generator again
rather than editing its repositories, so every run starts from the same seed.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

GITIGNORE = "__pycache__/\n*.pyc\n"
AUTHOR = {"GIT_AUTHOR_NAME": "synthetic fixture", "GIT_AUTHOR_EMAIL": "fixtures@example.invalid",
          "GIT_COMMITTER_NAME": "synthetic fixture", "GIT_COMMITTER_EMAIL": "fixtures@example.invalid"}
CHECK = "python3 -m unittest"

TASKS = [
    {
        "name": "fix-off-by-one",
        "prompt": "In calc.py, last_index returns one more than the index of the last item. "
                  "Fix it so the tests pass. Do not change the tests.",
        "check": CHECK,
        "files": {
            "calc.py": r'''def last_index(items):
    """Return the index of the last item, or -1 for an empty list."""
    return len(items)
''',
            "test_calc.py": r'''import unittest

from calc import last_index


class LastIndexTests(unittest.TestCase):
    def test_returns_index_of_last_item(self):
        self.assertEqual(last_index([10, 20, 30]), 2)

    def test_empty_list_gives_minus_one(self):
        self.assertEqual(last_index([]), -1)
''',
        },
        "fix": {
            "calc.py": r'''def last_index(items):
    """Return the index of the last item, or -1 for an empty list."""
    return len(items) - 1
''',
        },
    },
    {
        "name": "add-verbose-flag",
        "prompt": "Add a --verbose option to greet.py. With it, print a second line "
                  "'name length: N' after the greeting, where N is the length of the name. "
                  "Make the tests pass.",
        "check": CHECK,
        "files": {
            "greet.py": r'''import argparse


def build_message(name):
    return f"Hello, {name}!"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Print a greeting.")
    parser.add_argument("name")
    args = parser.parse_args(argv)
    print(build_message(args.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
''',
            "test_greet.py": r'''import io
import unittest
from contextlib import redirect_stdout

from greet import main


class GreetTests(unittest.TestCase):
    def test_prints_greeting(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(["Ada"]), 0)
        self.assertEqual(out.getvalue(), "Hello, Ada!\n")

    def test_verbose_adds_name_length(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(["--verbose", "Ada"]), 0)
        self.assertEqual(out.getvalue(), "Hello, Ada!\nname length: 3\n")
''',
        },
        "fix": {
            "greet.py": r'''import argparse


def build_message(name):
    return f"Hello, {name}!"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Print a greeting.")
    parser.add_argument("name")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    print(build_message(args.name))
    if args.verbose:
        print(f"name length: {len(args.name)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
''',
        },
    },
    {
        "name": "rename-config-key",
        "prompt": "Rename the settings key timeout_seconds to timeout_s everywhere in this "
                  "repository, including the README. Keep its default value and behavior. "
                  "Make the tests pass.",
        "check": CHECK,
        "files": {
            "settings.py": r'''DEFAULTS = {
    "timeout_seconds": 30,
    "retries": 2,
}


def load(overrides=None):
    settings = dict(DEFAULTS)
    settings.update(overrides or {})
    return settings
''',
            "client.py": r'''from settings import load


def request_budget(overrides=None):
    """Seconds one request may take before it is abandoned."""
    return load(overrides)["timeout_seconds"]
''',
            "README.md": "# Synthetic client\n\nSet `timeout_seconds` in the settings to change how long a request may take.\n",
            "test_client.py": r'''import unittest

from client import request_budget
from settings import load


class TimeoutKeyTests(unittest.TestCase):
    def test_default_uses_the_new_key(self):
        self.assertEqual(load()["timeout_s"], 30)
        self.assertNotIn("timeout_seconds", load())

    def test_override_sets_the_budget(self):
        self.assertEqual(request_budget({"timeout_s": 5}), 5)

    def test_retries_unchanged(self):
        self.assertEqual(load()["retries"], 2)
''',
        },
        "fix": {
            "settings.py": r'''DEFAULTS = {
    "timeout_s": 30,
    "retries": 2,
}


def load(overrides=None):
    settings = dict(DEFAULTS)
    settings.update(overrides or {})
    return settings
''',
            "client.py": r'''from settings import load


def request_budget(overrides=None):
    """Seconds one request may take before it is abandoned."""
    return load(overrides)["timeout_s"]
''',
            "README.md": "# Synthetic client\n\nSet `timeout_s` in the settings to change how long a request may take.\n",
        },
    },
    {
        # Read-only: the check only confirms nothing broke, so "passed" says little here.
        "name": "explain-parse-line",
        "prompt": "Do not change any file. In your reply, explain how parse_line in csv_lite.py "
                  "handles a quoted field that contains a comma and an escaped double quote. "
                  "Give one example input and its output, then stop.",
        "check": CHECK,
        "files": {
            "csv_lite.py": r'''def parse_line(line):
    """Split one CSV line. A double quote starts a quoted field; commas inside it are kept."""
    fields, current, quoted = [], [], False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == '"':
            if quoted and i + 1 < len(line) and line[i + 1] == '"':
                current.append('"')
                i += 1
            else:
                quoted = not quoted
        elif ch == "," and not quoted:
            fields.append("".join(current))
            current = []
        else:
            current.append(ch)
        i += 1
    fields.append("".join(current))
    return fields
''',
            "test_csv_lite.py": r'''import unittest

from csv_lite import parse_line


class ParseLineTests(unittest.TestCase):
    def test_plain_fields(self):
        self.assertEqual(parse_line("a,b,c"), ["a", "b", "c"])

    def test_quoted_comma_is_kept(self):
        self.assertEqual(parse_line('a,"b,c",d'), ["a", "b,c", "d"])

    def test_escaped_quote(self):
        self.assertEqual(parse_line('"say ""hi""",x'), ['say "hi"', "x"])
''',
        },
    },
]


def write_repo(root: Path, task: dict) -> Path:
    """Create one task's repository with its seed files and a single commit."""
    repo = root / task["name"]
    repo.mkdir()
    for relative, text in {".gitignore": GITIGNORE, **task["files"]}.items():
        (repo / relative).write_text(text, encoding="utf-8")
    env = {**os.environ, **AUTHOR}
    git = ["git", "-C", str(repo)]
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
    subprocess.run([*git, "add", "-A"], check=True, env=env)
    subprocess.run([*git, "commit", "-q", "-m", f"Seed {task['name']}"], check=True, env=env)
    return repo


def build(out: Path, only: str | None = None) -> Path:
    """Write the repositories and tasks.json into ``out`` and return the tasks file."""
    selected = [task for task in TASKS if only is None or task["name"] == only]
    if not selected:
        raise SystemExit(f"make-fixtures: no task named {only!r}")
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"make-fixtures: {out} is not empty; choose a new folder")
    out.mkdir(parents=True, exist_ok=True)
    entries = []
    for task in selected:
        write_repo(out, task)
        entries.append({"name": task["name"], "repo": task["name"], "prompt": task["prompt"], "check": task["check"]})
    tasks_file = out / "tasks.json"
    tasks_file.write_text(json.dumps({"tasks": entries}, indent=2) + "\n", encoding="utf-8")
    return tasks_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("out", type=Path, help="new or empty folder for the repositories and tasks.json")
    parser.add_argument("--only", help="build only this task, for a pilot run")
    args = parser.parse_args(argv)
    tasks_file = build(args.out, args.only)
    print(f"wrote {tasks_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
