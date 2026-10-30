#!/usr/bin/env python3
"""packaging/measure.py compares two AgentFleet refs on the same tasks; a
difference counts only when the runs do not overlap, and runs with errors or
failed lanes mark the task confounded."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("measure", ROOT / "packaging" / "measure.py")
measure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(measure)

CLAUDE_JSON = {
    "is_error": False, "num_turns": 4, "duration_ms": 12500, "total_cost_usd": 0.25,
    "modelUsage": {
        "main-model": {"inputTokens": 10, "outputTokens": 200, "cacheReadInputTokens": 3000, "cacheCreationInputTokens": 500},
        "lane-model": {"inputTokens": 5, "outputTokens": 100, "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0},
    },
    "subagent_stats": {"spawned": 2, "completed": 1, "failed": 1},
}


def runs(variant: str, tokens: list[int], **extra) -> list[dict]:
    return [{"task": "t", "variant": variant, "error": False, "tokens": value, "cost": 0.1, "turns": 3,
             "seconds": 10.0, "lanesStarted": 1, "lanesFailed": 0, "passed": True, "filesChanged": 2,
             "linesAdded": 10, "linesRemoved": 2, "linesChanged": 12, **extra} for value in tokens]


class MeasureTests(unittest.TestCase):
    def test_parse_run_sums_every_model_and_counts_lanes(self):
        parsed = measure.parse_run(json.dumps(CLAUDE_JSON))
        self.assertEqual(parsed["tokens"], 3815)
        self.assertEqual((parsed["turns"], parsed["seconds"], parsed["cost"]), (4, 12.5, 0.25))
        self.assertEqual((parsed["lanesStarted"], parsed["lanesFailed"]), (2, 1))
        self.assertEqual(parsed["models"], ["lane-model", "main-model"])

    def test_parse_run_keeps_a_bounded_final_text(self):
        parsed = measure.parse_run(json.dumps({**CLAUDE_JSON, "result": "done " * 500}))
        self.assertTrue(parsed["finalText"].startswith("done "))
        self.assertEqual(len(parsed["finalText"]), 1000)

    def test_verdict_needs_three_runs_and_no_overlap(self):
        self.assertEqual(measure.verdict([10, 11], [5, 6]), "inconclusive: fewer than 3 runs each")
        self.assertEqual(measure.verdict([10, 11, 12], [5, 6, 7]), "candidate lower")
        self.assertEqual(measure.verdict([5, 6, 7], [10, 11, 12]), "candidate higher")
        self.assertEqual(measure.verdict([5, 8, 12], [6, 7, 9]), "inconclusive: the runs overlap")

    def test_summary_reports_verdicts_and_marks_troubled_tasks_confounded(self):
        clean = measure.summarize(runs("base", [100, 110, 120]) + runs("candidate", [50, 55, 60]))
        self.assertIn("| tokens | 110 (100–120) | 55 (50–60) | candidate lower |", clean)
        self.assertIn("| check passed | 3/3 | 3/3 | |", clean)
        self.assertIn("| lines changed | 12 (12–12) | 12 (12–12) | inconclusive: the runs overlap |", clean)
        self.assertNotIn("Confounded", clean)
        self.assertNotIn("failed its check", clean)
        failing = measure.summarize(runs("base", [100, 110, 120]) + runs("candidate", [50, 55], run=2, passed=False,
                                                                           checkOutput="Traceback\nAttributeError: no headers"))
        self.assertIn("- candidate run 2 failed its check: `AttributeError: no headers` (full output in runs.jsonl)", failing)
        troubled = measure.summarize(runs("base", [100, 110, 120]) + runs("candidate", [50, 55]) + runs("candidate", [60], lanesFailed=1))
        self.assertIn("Confounded: 1 run(s)", troubled)
        self.assertIn("| tokens | 110 (100–120) | 55 (50–60) | confounded |", troubled)

    def test_load_tasks_resolves_repos_and_rejects_bad_files(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "tasks.json"
            path.write_text(json.dumps({"tasks": [{"name": "a", "repo": "fixture", "prompt": "p", "check": "true"}]}))
            self.assertEqual(measure.load_tasks(path)[0]["repo"], str((Path(raw) / "fixture").resolve()))
            for bad in ({"tasks": []}, {"tasks": [{"name": "a", "repo": "r"}]},
                        {"tasks": [{"name": "a", "repo": "r", "prompt": "p"}, {"name": "a", "repo": "r", "prompt": "p"}]},
                        {"tasks": [{"name": "a", "repo": "r", "prompt": "p", "check": ["true"]}]}):
                path.write_text(json.dumps(bad))
                with self.assertRaises(ValueError):
                    measure.load_tasks(path)

    def run_fake_task(self, raw: str, script: str, check: str, timeout: int = 60, diff_path: Path | None = None) -> tuple[dict, Path, Path]:
        """Run one task in a git repo whose `claude` is a fake shell script."""
        work, repo, bin_dir, home = (Path(raw) / name for name in ("work", "repo", "bin", "home"))
        for folder in (work, bin_dir, home):
            folder.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        (repo / "a.txt").write_text("one\n")
        (repo / ".gitignore").write_text("*.pyc\n")
        git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com"]
        subprocess.run(git + ["add", "a.txt", ".gitignore"], check=True)
        subprocess.run(git + ["commit", "-q", "-m", "one"], check=True)
        fake = bin_dir / "claude"
        fake.write_text(f"#!/bin/sh\n{script}\nprintf '%s' '{json.dumps(CLAUDE_JSON)}'\n")
        fake.chmod(0o755)
        task = {"name": "t", "repo": str(repo), "prompt": "edit", "check": check}
        old_path = os.environ["PATH"]
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{old_path}"
        try:
            return measure.run_task(task, home, work, None, timeout, diff_path), repo, work
        finally:
            os.environ["PATH"] = old_path

    def test_run_task_uses_a_clone_without_its_origin_and_runs_the_check(self):
        with tempfile.TemporaryDirectory() as raw:
            seen = Path(raw) / "remotes.txt"
            result, repo, work = self.run_fake_task(raw, f"git remote > '{seen}'\necho two > a.txt", "grep -q two a.txt")
            self.assertEqual(seen.read_text(), "")
            self.assertTrue(result["passed"])
            self.assertNotIn("checkOutput", result)
            self.assertFalse(result["error"])
            self.assertEqual(result["tokens"], 3815)
            self.assertEqual((repo / "a.txt").read_text(), "one\n")
            self.assertEqual(list(work.iterdir()), [])

    def test_run_task_counts_the_change_including_commits_and_new_files(self):
        with tempfile.TemporaryDirectory() as raw:
            diff = Path(raw) / "out" / "diffs" / "t-base-1.diff"
            # Edits a.txt, commits a new file, writes an ignored one; the check proves the clone's own index is untouched.
            script = ("echo two > a.txt\nprintf 'x\\ny\\n' > b.txt\n"
                      "git add b.txt && git -c user.name=a -c user.email=a@example.com commit -qm b\necho junk > c.pyc")
            result, _, _ = self.run_fake_task(raw, script, "git diff --cached --quiet", diff_path=diff)
            self.assertTrue(result["passed"])
            self.assertEqual((result["filesChanged"], result["linesAdded"], result["linesRemoved"], result["linesChanged"]), (2, 3, 1, 4))
            text = diff.read_text()
            self.assertIn("+two", text)
            self.assertIn("b/b.txt", text)
            self.assertNotIn("c.pyc", text)

    def test_run_task_keeps_the_output_of_a_failed_or_timed_out_check(self):
        with tempfile.TemporaryDirectory() as raw:
            result, _, _ = self.run_fake_task(raw, "true", "echo checking; echo 'AssertionError: boom' >&2; exit 1")
            self.assertFalse(result["passed"])
            self.assertEqual(result["checkOutput"], "checking\nAssertionError: boom")
        with tempfile.TemporaryDirectory() as raw:
            result, _, _ = self.run_fake_task(raw, "true", "sleep 5", timeout=1)
            self.assertFalse(result["passed"])
            self.assertEqual(result["checkOutput"], "check timed out after 1 s")

if __name__ == "__main__":
    unittest.main()
