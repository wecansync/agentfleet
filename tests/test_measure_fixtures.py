#!/usr/bin/env python3
"""The synthetic benchmark fixtures for packaging/measure.py: 3 to 5 tasks,
valid for measure's own loader, each a clean git repository; the check fails
on the seed for a task that changes code and passes with the reference fix;
and the read-only task's check passes on the seed."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


measure = load("measure", ROOT / "packaging" / "measure.py")
fixtures = load("make_fixtures", ROOT / "packaging" / "measure-fixtures" / "make-fixtures.py")


def check(repo: Path, command: str) -> int:
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(repo.parent), "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(command, shell=True, cwd=repo, env=env, capture_output=True, text=True).returncode


class MeasureFixtureTests(unittest.TestCase):
    def setUp(self):
        self.raw = tempfile.TemporaryDirectory(prefix="af-fixtures-")
        self.addCleanup(self.raw.cleanup)
        self.out = Path(self.raw.name) / "bench"
        self.tasks_file = fixtures.build(self.out)

    def test_three_to_five_tasks_load_with_measures_own_loader(self):
        tasks = measure.load_tasks(self.tasks_file)
        self.assertGreaterEqual(len(tasks), 3)
        self.assertLessEqual(len(tasks), 5)
        self.assertEqual(len({task["name"] for task in tasks}), len(tasks))
        for task in tasks:
            with self.subTest(task=task["name"]):
                self.assertTrue(Path(task["repo"]).is_dir())
                self.assertTrue(task["check"])

    def test_each_repo_is_a_clean_single_commit_repo_without_remote(self):
        for task in measure.load_tasks(self.tasks_file):
            repo = Path(task["repo"])
            with self.subTest(task=task["name"]):
                status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True)
                commits = subprocess.run(["git", "-C", str(repo), "rev-list", "--count", "HEAD"], capture_output=True, text=True)
                remotes = subprocess.run(["git", "-C", str(repo), "remote"], capture_output=True, text=True)
                self.assertEqual(status.stdout, "")
                self.assertEqual(commits.stdout.strip(), "1")
                self.assertEqual(remotes.stdout.strip(), "")

    def test_write_tasks_fail_on_seed_and_pass_with_reference_fix(self):
        checks = {task["name"]: task["check"] for task in measure.load_tasks(self.tasks_file)}
        for task in fixtures.TASKS:
            if "fix" not in task:
                continue
            with self.subTest(task=task["name"]):
                repo = self.out / task["name"]
                self.assertNotEqual(check(repo, checks[task["name"]]), 0, "seed must fail so the task is real")
                fixed = Path(self.raw.name) / f"fixed-{task['name']}"
                shutil.copytree(repo, fixed)
                for relative, text in task["fix"].items():
                    (fixed / relative).write_text(text, encoding="utf-8")
                self.assertEqual(check(fixed, checks[task["name"]]), 0)

    def test_read_only_task_check_passes_on_seed(self):
        checks = {task["name"]: task["check"] for task in measure.load_tasks(self.tasks_file)}
        readonly = [task for task in fixtures.TASKS if "fix" not in task]
        self.assertEqual(len(readonly), 1)
        self.assertEqual(check(self.out / readonly[0]["name"], checks[readonly[0]["name"]]), 0)

    def test_only_builds_one_task_for_a_pilot(self):
        pilot = fixtures.build(Path(self.raw.name) / "pilot", only="fix-off-by-one")
        tasks = measure.load_tasks(pilot)
        self.assertEqual([task["name"] for task in tasks], ["fix-off-by-one"])

    def test_refuses_a_folder_that_is_not_empty(self):
        with self.assertRaises(SystemExit):
            fixtures.build(self.out)

    def test_synthetic_only_fixture_identity(self):
        # Every commit uses the reserved example.invalid domain; no URLs or remotes are added.
        for task in measure.load_tasks(self.tasks_file):
            log = subprocess.run(["git", "-C", task["repo"], "log", "--format=%ae %ce"], capture_output=True, text=True).stdout
            self.assertEqual(log.strip(), "fixtures@example.invalid fixtures@example.invalid")


class CheckerTests(unittest.TestCase):
    """check-task.py rejects edited tests, a stale README key and read-only mutations."""

    @classmethod
    def setUpClass(cls):
        cls.raw = tempfile.TemporaryDirectory(prefix="af-checker-")
        cls.tasks = {task["name"]: task for task in measure.load_tasks(fixtures.build(Path(cls.raw.name) / "bench"))}
        cls.fixes = {task["name"]: task["fix"] for task in fixtures.TASKS if "fix" in task}

    @classmethod
    def tearDownClass(cls):
        cls.raw.cleanup()

    def verdict(self, name, edits=None):
        target = Path(tempfile.mkdtemp(prefix="clone-", dir=self.raw.name)) / "repo"
        subprocess.run(["git", "clone", "-q", "--no-hardlinks", self.tasks[name]["repo"], str(target)], check=True)
        for relative, text in (edits or {}).items():
            (target / relative).write_text(text, encoding="utf-8")
        return check(target, self.tasks[name]["check"])

    def test_reference_fixes_pass(self):
        for name, fix in self.fixes.items():
            with self.subTest(task=name):
                self.assertEqual(self.verdict(name, fix), 0)

    def test_editing_a_test_file_fails(self):
        cheat = {**self.fixes["fix-off-by-one"],
                 "test_calc.py": "import unittest\n\n\nclass LastIndexTests(unittest.TestCase):\n    def test_nothing(self):\n        pass\n"}
        self.assertNotEqual(self.verdict("fix-off-by-one", cheat), 0)

    def test_rename_keeping_the_old_key_in_the_readme_fails(self):
        stale = {**self.fixes["rename-config-key"],
                 "README.md": "# Synthetic client\n\nSet `timeout_seconds` in the settings.\n"}
        self.assertNotEqual(self.verdict("rename-config-key", stale), 0)

    def test_read_only_task_fails_on_any_mutation(self):
        self.assertEqual(self.verdict("explain-parse-line"), 0)
        self.assertNotEqual(self.verdict("explain-parse-line", {"csv_lite.py": "def parse_line(line):\n    return []\n"}), 0)
        self.assertNotEqual(self.verdict("explain-parse-line", {"notes.txt": "added\n"}), 0)

    def test_read_only_task_fails_on_a_committed_new_file(self):
        target = Path(tempfile.mkdtemp(prefix="clone-", dir=self.raw.name)) / "repo"
        subprocess.run(["git", "clone", "-q", "--no-hardlinks", self.tasks["explain-parse-line"]["repo"], str(target)], check=True)
        (target / "notes.txt").write_text("added\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(target), "add", "notes.txt"], check=True)
        subprocess.run(["git", "-C", str(target), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        "-c", "commit.gpgsign=false", "commit", "-q", "-m", "notes"], check=True)
        self.assertNotEqual(check(target, self.tasks["explain-parse-line"]["check"]), 0)

    def test_fixture_git_ignores_the_users_git_config(self):
        config = Path(self.raw.name) / "gitconfig"
        config.write_text("[commit]\n\tgpgsign = true\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(config)}):
            fixtures.build(Path(self.raw.name) / "isolated", only="fix-off-by-one")


if __name__ == "__main__":
    unittest.main()
