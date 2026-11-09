#!/usr/bin/env python3
"""Tests for `agentfleet ci`: a real git repository and a fake GitHub CLI."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import local_ci  # noqa: E402

PYTHON = sys.executable
AGENTFLEET = ROOT / "bin" / "agentfleet"
# Logs every call; `repo view` answers o/r; a marker file makes the commit unknown.
FAKE_GH = r'''#!/usr/bin/env python3
import json
import sys
from pathlib import Path

home = Path.home()
argv = sys.argv[1:]
with (home / "gh-calls.jsonl").open("a", encoding="utf-8") as log:
    log.write(json.dumps(argv) + "\n")
if argv[:2] == ["repo", "view"]:
    print("o/r")
elif argv[0] == "api" and "/commits/" in argv[1]:
    sys.exit(1 if (home / "unpushed").exists() else 0)
elif argv[0] == "api" and argv[1:3] == ["--method", "POST"]:
    sys.exit(1 if (home / "refuse").exists() else 0)
else:
    sys.exit(2)
'''


@unittest.skipIf(os.name == "nt", "the fake gh is a POSIX script")
class LocalCITests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="agentfleet-ci-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        fake = self.bin / "gh"
        fake.write_text(FAKE_GH, encoding="utf-8")
        fake.chmod(0o755)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.write_test("exit 0")

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.repo), "-c", "user.email=ci@example.com", "-c", "user.name=ci",
                               "-c", "commit.gpgsign=false", *args],
                              check=True, text=True, capture_output=True, env=self.env()).stdout.strip()

    def env(self) -> dict[str, str]:
        return {"PATH": os.pathsep.join([str(self.bin), os.defpath]), "HOME": str(self.home), "LANG": "C.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1", "GIT_CONFIG_NOSYSTEM": "1"}

    def write_test(self, body: str) -> None:
        (self.repo / ".agentfleet-ci").write_text(f"# test command\nsh check.sh\n", encoding="utf-8")
        (self.repo / "check.sh").write_text(body + "\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "test", "--allow-empty")

    def ci(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([PYTHON, str(AGENTFLEET), "--home", str(self.home), "--config-home",
                               str(self.root / "config"), "ci", *args],
                              cwd=self.repo, env=self.env(), text=True, capture_output=True, timeout=60)

    def calls(self) -> list[list[str]]:
        path = self.home / "gh-calls.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def statuses(self) -> list[str]:
        return [next(a.split("=", 1)[1] for a in c if a.startswith("state=")) for c in self.calls()
                if c[:3] == ["api", "--method", "POST"]]

    def test_passing_run_posts_pending_then_success_on_the_pushed_commit(self):
        result = self.ci()
        self.assertEqual(result.returncode, 0, result.stderr)
        sha = self.git("rev-parse", "HEAD")
        self.assertEqual(self.statuses(), ["pending", "success"])
        post = [c for c in self.calls() if c[:3] == ["api", "--method", "POST"]][-1]
        self.assertEqual(post[3], f"repos/o/r/statuses/{sha}")
        self.assertIn("context=agentfleet/tests", post)
        self.assertIn("posted agentfleet/tests = success", result.stdout)

    def test_failing_run_posts_failure_and_returns_the_test_exit_code(self):
        self.write_test("exit 3")
        result = self.ci()
        self.assertEqual(result.returncode, 3)
        self.assertEqual(self.statuses(), ["pending", "failure"])

    def test_uncommitted_changes_are_refused_before_running(self):
        (self.repo / "check.sh").write_text("touch ran\n", encoding="utf-8")
        result = self.ci()
        self.assertEqual(result.returncode, 2)
        self.assertIn("uncommitted changes", result.stderr)
        self.assertFalse((self.repo / "ran").exists())
        self.assertEqual(self.calls(), [])

    def test_an_unpushed_commit_is_refused_before_running(self):
        (self.home / "unpushed").write_text("", encoding="utf-8")
        result = self.ci()
        self.assertEqual(result.returncode, 2)
        self.assertIn("push it first", result.stderr)
        self.assertEqual(self.statuses(), [])

    def test_a_checkout_that_changes_during_the_run_posts_no_result(self):
        self.write_test("echo extra > changed.txt")
        result = self.ci()
        self.assertEqual(result.returncode, 2)
        self.assertIn("checkout changed", result.stderr)
        self.assertEqual(self.statuses(), ["pending", "error"])

    def test_an_ignored_config_cannot_replace_the_committed_tests(self):
        self.write_test("exit 3")
        self.git("rm", "-q", "--cached", ".agentfleet-ci")
        self.git("commit", "-qm", "untrack config")
        (self.repo / ".git" / "info" / "exclude").write_text(".agentfleet-ci\n", encoding="utf-8")
        (self.repo / ".agentfleet-ci").write_text("exit 0\n", encoding="utf-8")
        result = self.ci()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not committed", result.stderr)
        self.assertEqual(self.statuses(), [])

    def test_a_posted_run_uses_the_committed_config(self):
        self.write_test("exit 3")
        (self.repo / ".git" / "info" / "exclude").write_text(".agentfleet-ci\n", encoding="utf-8")
        self.git("update-index", "--assume-unchanged", ".agentfleet-ci")
        (self.repo / ".agentfleet-ci").write_text("exit 0\n", encoding="utf-8")
        result = self.ci()
        self.assertEqual(result.returncode, 3, result.stdout)
        self.assertEqual(self.statuses(), ["pending", "failure"])

    def test_no_status_runs_without_github(self):
        (self.repo / "check.sh").write_text("exit 0\n", encoding="utf-8")  # uncommitted is fine here
        result = self.ci("--no-status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [])

    def test_run_option_overrides_the_configured_command(self):
        result = self.ci("--run", "exit 5", "--no-status")
        self.assertEqual(result.returncode, 5)

    def test_refused_status_is_reported(self):
        (self.home / "refuse").write_text("", encoding="utf-8")
        result = self.ci()
        self.assertEqual(result.returncode, 2)
        self.assertIn("refused the commit status", result.stderr)

    def test_detects_common_test_commands(self):
        cases = (
            ({"package.json": json.dumps({"scripts": {"test": "jest"}})}, "npm test"),
            ({"package.json": json.dumps({"scripts": {"test": "vitest"}}), "pnpm-lock.yaml": ""}, "pnpm test"),
            ({"package.json": json.dumps({"scripts": {"test": "echo \"Error: no test specified\" && exit 1"}})}, None),
            ({"composer.json": json.dumps({"scripts": {"test": "phpunit"}})}, "composer test"),
            ({"artisan": ""}, "php artisan test"),
            ({"Makefile": "build:\n\ttrue\ntest:\n\ttrue\n"}, "make test"),
            ({"go.mod": "module x\n"}, "go test ./..."),
            ({"Cargo.toml": "[package]\n"}, "cargo test"),
        )
        for files, expected in cases:
            with self.subTest(files=sorted(files)), tempfile.TemporaryDirectory() as raw:
                project = Path(raw)
                for name, text in files.items():
                    (project / name).write_text(text, encoding="utf-8")
                self.assertEqual(local_ci.detect_command(project), expected)

    def test_no_command_found_is_explained(self):
        (self.repo / ".agentfleet-ci").unlink()
        (self.repo / "check.sh").unlink()
        self.git("add", "-A")
        self.git("commit", "-qm", "empty")
        result = self.ci()
        self.assertEqual(result.returncode, 2)
        self.assertIn("no test command found", result.stderr)


if __name__ == "__main__":
    unittest.main()
