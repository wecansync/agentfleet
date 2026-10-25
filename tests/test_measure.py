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
             "seconds": 10.0, "lanesStarted": 1, "lanesFailed": 0, "passed": True, **extra} for value in tokens]


class MeasureTests(unittest.TestCase):
    def test_parse_run_sums_every_model_and_counts_lanes(self):
        parsed = measure.parse_run(json.dumps(CLAUDE_JSON))
        self.assertEqual(parsed["tokens"], 3815)
        self.assertEqual((parsed["turns"], parsed["seconds"], parsed["cost"]), (4, 12.5, 0.25))
        self.assertEqual((parsed["lanesStarted"], parsed["lanesFailed"]), (2, 1))
        self.assertEqual(parsed["models"], ["lane-model", "main-model"])

    def test_verdict_needs_three_runs_and_no_overlap(self):
        self.assertEqual(measure.verdict([10, 11], [5, 6]), "inconclusive: fewer than 3 runs each")
        self.assertEqual(measure.verdict([10, 11, 12], [5, 6, 7]), "candidate lower")
        self.assertEqual(measure.verdict([5, 6, 7], [10, 11, 12]), "candidate higher")
        self.assertEqual(measure.verdict([5, 8, 12], [6, 7, 9]), "inconclusive: the runs overlap")

    def test_summary_reports_verdicts_and_marks_troubled_tasks_confounded(self):
        clean = measure.summarize(runs("base", [100, 110, 120]) + runs("candidate", [50, 55, 60]))
        self.assertIn("| tokens | 110 (100–120) | 55 (50–60) | candidate lower |", clean)
        self.assertIn("| check passed | 3/3 | 3/3 | |", clean)
        self.assertNotIn("Confounded", clean)
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

    def test_run_task_uses_a_clone_without_its_origin_and_runs_the_check(self):
        with tempfile.TemporaryDirectory() as raw:
            work, repo, bin_dir, home = (Path(raw) / name for name in ("work", "repo", "bin", "home"))
            for folder in (work, bin_dir, home):
                folder.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            (repo / "a.txt").write_text("one\n")
            git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com"]
            subprocess.run(git + ["add", "a.txt"], check=True)
            subprocess.run(git + ["commit", "-q", "-m", "one"], check=True)
            seen = Path(raw) / "remotes.txt"
            # A fake claude: records the clone's remotes, edits the file, prints the JSON result.
            fake = bin_dir / "claude"
            fake.write_text("#!/bin/sh\n"
                            f"git remote > '{seen}'\n"
                            "echo two > a.txt\n"
                            f"printf '%s' '{json.dumps(CLAUDE_JSON)}'\n")
            fake.chmod(0o755)
            task = {"name": "t", "repo": str(repo), "prompt": "edit", "check": "grep -q two a.txt"}
            old_path = os.environ["PATH"]
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{old_path}"
            try:
                result = measure.run_task(task, home, work, None, 60)
            finally:
                os.environ["PATH"] = old_path
            self.assertEqual(seen.read_text(), "")
            self.assertTrue(result["passed"])
            self.assertFalse(result["error"])
            self.assertEqual(result["tokens"], 3815)
            self.assertEqual((repo / "a.txt").read_text(), "one\n")
            self.assertEqual(list(work.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
