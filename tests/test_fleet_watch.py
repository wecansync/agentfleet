#!/usr/bin/env python3
"""Focused tests for fleet_watch and its UserPromptSubmit integration."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "fleet_watch.py"
ROUTE = ROOT / "scripts" / "route-to-fleet.py"
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("fleet_watch", SCRIPT)
fleet_watch = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = fleet_watch
spec.loader.exec_module(fleet_watch)


# Built at runtime so the bundle's own secret scan never sees a key-shaped literal.
FAKE_KEY = "sk-" + "1234567890" * 2


def rec(kind, message, timestamp="2026-10-11T09:00:00Z"):
    return {"type": kind, "timestamp": timestamp, "message": message}


def assistant(message_id="m1", content=None, model="claude-sonnet-4.6", usage=None):
    return rec("assistant", {
        "id": message_id, "role": "assistant", "model": model,
        "content": content or [], "stop_reason": "end_turn",
        "usage": usage or {"input_tokens": 10, "output_tokens": 3},
    })


def tool(name, inputs):
    return {"type": "tool_use", "id": "toolu-1", "name": name, "input": inputs}


class FleetWatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        (self.home / ".claude").mkdir(parents=True)
        self.project = self.root / "project"
        self.project.mkdir()
        self.session = "session-123"
        self.transcript = self.project / f"{self.session}.jsonl"
        self.agent_dir = self.project / self.session / "subagents"
        self.agent_dir.mkdir(parents=True)
        self.agent = self.agent_dir / "agent-ac27a4.jsonl"
        self.meta = self.agent_dir / "agent-ac27a4.meta.json"
        self.meta.write_text(json.dumps({"agentType": "fleet-tests", "description": "Write tests"}), encoding="utf-8")
        self._fleet()

    def tearDown(self):
        self.temp.cleanup()

    def _fleet(self, lanes=None):
        lanes = lanes or {
            "docs": {"model": "sonnet"},
            "ui": {"model": "opus", "altOf": "docs"},
            "implement": {"model": "haiku"},
            "tests": {"model": "sonnet"},
        }
        (self.home / ".claude" / "fleet.json").write_text(json.dumps({"lanes": lanes}), encoding="utf-8")

    def payload(self, prompt="hello, this should not route because it is plain", session_id=None, transcript=None):
        return {
            "session_id": session_id or self.session,
            "transcript_path": str(transcript or self.transcript),
            "cwd": str(self.project),
            "prompt": prompt,
        }

    @staticmethod
    def write_records(path, records, append=False, partial=""):
        mode = "a" if append else "w"
        with path.open(mode, encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.write(partial)

    def test_lane_report_emitted_once_with_files_and_forbidden_command(self):
        self.write_records(self.agent, [
            assistant("same", [tool("Edit", {"file_path": str(self.project / "tests" / "test_a.py")})]),
            assistant("done", [tool("Bash", {"command": "git restore -- site/index.html"})]),
            assistant("final", []),
        ])
        # Reported as soon as the transcript is fully read and the last message ended its turn.
        lines = fleet_watch.on_prompt(self.payload(), self.home)
        self.assertEqual(len(lines), 1)
        self.assertIn("fleet-tests", lines[0])
        self.assertIn("changed 1 files: tests/test_a.py", lines[0])
        self.assertIn("[git-history]", lines[0])
        report = self.home / ".claude" / "agentfleet" / "lane-reports.jsonl"
        self.assertEqual(len(report.read_text(encoding="utf-8").splitlines()), 1)
        self.assertEqual(fleet_watch.on_prompt(self.payload(), self.home), [])
        self.assertEqual(len(report.read_text(encoding="utf-8").splitlines()), 1)

    def test_usage_counts_each_message_once_at_its_final_value(self):
        self.write_records(self.agent, [
            assistant("same", [], usage={"input_tokens": 5, "output_tokens": 2}),
            assistant("same", [], usage={"input_tokens": 500, "output_tokens": 200}),
            assistant("final", []),
        ])
        fleet_watch.on_prompt(self.payload(), self.home)
        rows = (self.home / ".claude" / "agentfleet" / "lane-reports.jsonl").read_text().splitlines()
        report = json.loads(rows[0])
        # The later record of a message carries its final counts: 500 + 10 in, 200 + 3 out.
        self.assertEqual((report["inputTokens"], report["outputTokens"]), (510, 203))

    def test_outage_alternative_selection_clearing_expiry_and_alt_of(self):
        self._fleet({
            "docs": {"model": "same"},
            "docs-alt": {"model": "other", "altOf": "docs"},
            "ui": {"model": "other"},
            "implement": {"model": "last"},
        })
        self.assertEqual(fleet_watch.alternative_lane(self.home, "docs"), "docs-alt")
        # Relative to now: fixed timestamps would expire and make this test depend on the clock.
        now = datetime.now(timezone.utc)
        at = now.isoformat().replace("+00:00", "Z")
        until = (now + timedelta(minutes=15)).isoformat().replace("+00:00", "Z")
        outages = {"format": "agentfleet.outages.v1", "lanes": {
            "docs-alt": {"model": "other", "status": 503, "at": at, "until": until},
            "ui": {"model": "same", "status": 503, "at": at, "until": until},
        }}
        store = self.home / ".claude" / "agentfleet"
        store.mkdir(parents=True, exist_ok=True)
        (store / "outages.json").write_text(json.dumps(outages), encoding="utf-8")
        self.assertEqual(fleet_watch.alternative_lane(self.home, "docs"), "implement")
        expired = {"format": "agentfleet.outages.v1", "lanes": {
            "docs": {"model": "same", "status": 503, "at": "2020-01-01T00:00:00Z", "until": "2020-01-01T00:15:00Z"},
        }}
        (store / "outages.json").write_text(json.dumps(expired), encoding="utf-8")
        self.assertFalse(fleet_watch.current_outages(self.home)["lanes"])

        self.meta.write_text(json.dumps({"agentType": "fleet-docs", "description": "Docs"}), encoding="utf-8")
        just_now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        failure = rec("assistant", {"id": "api", "role": "assistant", "model": "<synthetic>",
            "content": [{"type": "text", "text": "API Error: 503 Service temporarily unavailable"}], "usage": {}}, timestamp=just_now)
        # The synthetic API error is the last record a failed subagent writes.
        self.write_records(self.agent, [failure])
        lines = fleet_watch.on_prompt(self.payload(), self.home)
        self.assertTrue(any("AgentFleet outage" in line and "dispatch fleet-docs-alt" in line for line in lines), lines)
        self.assertEqual(fleet_watch.current_outages(self.home)["lanes"]["docs"]["status"], 503)
        # A later run of the same lane that succeeds clears the outage.
        retry = self.agent_dir / "agent-bd41f2.jsonl"
        (self.agent_dir / "agent-bd41f2.meta.json").write_text(json.dumps({"agentType": "fleet-docs", "description": "Docs retry"}), encoding="utf-8")
        older = assistant("older-success", [])
        later = {**assistant("success", []), "timestamp": (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat().replace("+00:00", "Z")}
        # A run that ended before the failure proves nothing.
        self.write_records(retry, [older])
        fleet_watch.on_prompt(self.payload(), self.home)
        self.assertIn("docs", fleet_watch.current_outages(self.home)["lanes"])
        # A run that ends after it clears the outage.
        second = self.agent_dir / "agent-ce52a3.jsonl"
        (self.agent_dir / "agent-ce52a3.meta.json").write_text(json.dumps({"agentType": "fleet-docs", "description": "Docs again"}), encoding="utf-8")
        self.write_records(second, [later])
        fleet_watch.on_prompt(self.payload(), self.home)
        self.assertNotIn("docs", fleet_watch.current_outages(self.home)["lanes"])

    def test_old_failures_never_open_outages_and_two_failing_lanes_skip_each_other(self):
        self._fleet({"review": {"model": "a"}, "review-alt": {"model": "b"}, "review-deep": {"model": "c"}})
        now = datetime.now(timezone.utc)
        def failure(when):
            return rec("assistant", {"id": "api", "role": "assistant", "model": "<synthetic>",
                "content": [{"type": "text", "text": "API Error: 529 Overloaded"}], "usage": {}},
                timestamp=when.isoformat().replace("+00:00", "Z"))
        # Three hours ago: reported, but the outage would already have expired.
        self.meta.write_text(json.dumps({"agentType": "fleet-review", "description": "old"}), encoding="utf-8")
        self.write_records(self.agent, [failure(now - timedelta(hours=3))])
        fleet_watch.on_prompt(self.payload(), self.home)
        self.assertEqual(fleet_watch.current_outages(self.home)["lanes"], {})
        # review and review-alt fail together: neither recommends the other.
        for agent_id, lane in (("aa11", "review"), ("bb22", "review-alt")):
            (self.agent_dir / f"agent-{agent_id}.meta.json").write_text(json.dumps({"agentType": f"fleet-{lane}", "description": lane}), encoding="utf-8")
            self.write_records(self.agent_dir / f"agent-{agent_id}.jsonl", [failure(now)])
        lines = fleet_watch.on_prompt(self.payload(), self.home)
        outages = fleet_watch.current_outages(self.home)["lanes"]
        # review-alt is failing too, so review falls back to review-deep.
        self.assertEqual(outages["review"]["alternative"], "review-deep")
        self.assertNotEqual(outages["review-alt"].get("alternative"), "review")
        self.assertFalse(any("fleet-review-alt (" in line and "dispatch fleet-review instead" in line for line in lines), lines)

    def test_incremental_offsets_partial_trailing_line_and_system_envelope(self):
        complete = json.dumps(assistant("finished", [tool("Edit", {"file_path": str(self.project / "a.py")})])) + "\n"
        self.agent.write_text(complete + '{"type":"assistant","mess', encoding="utf-8", newline="\n")
        self.assertEqual(fleet_watch.on_prompt(self.payload(), self.home), [])
        state = json.loads((self.home / ".claude" / "agentfleet" / "watch-state.json").read_text())
        path = str(self.agent.resolve())
        self.assertEqual(state["sessions"][self.session]["agents"][path]["offset"], len(complete.encode()))
        with self.agent.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write('age":{"id":"last","model":"sonnet","content":[],"stop_reason":"end_turn"}}\n')
        lines = fleet_watch.on_prompt(self.payload(prompt="<task notification>"), self.home)
        self.assertTrue(lines)
        self.assertIn("fleet-tests", lines[0])

    def test_system_envelope_route_hook_emits_watcher_line(self):
        self.write_records(self.agent, [
            assistant("one", [tool("Write", {"file_path": str(self.project / "README.md")})]),
            assistant("finished", []),
        ])
        env = {**os.environ, "CLAUDE_FLEET_HOME": str(self.home), "PYTHONDONTWRITEBYTECODE": "1"}
        # The task notification is the first prompt after a background subagent finishes.
        second = subprocess.run([sys.executable, str(ROUTE)], input=json.dumps(self.payload("<task notification>")), text=True, capture_output=True, env=env)
        self.assertEqual(second.returncode, 0, second.stderr)
        response = json.loads(second.stdout)
        self.assertIn("lane report", response["hookSpecificOutput"]["additionalContext"])

    def test_route_substitutes_outage_with_alternative(self):
        self._fleet({"docs": {"model": "sonnet"}, "ui": {"model": "opus"}, "implement": {"model": "haiku"}})
        config = self.home / ".claude" / "agentfleet" / "config.json"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(json.dumps({"format": "agentfleet.config.v1", "workStyle": "fleet"}), encoding="utf-8")
        store = self.home / ".claude" / "agentfleet"
        store.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        (store / "outages.json").write_text(json.dumps({"format": "agentfleet.outages.v1", "lanes": {
            "docs": {"model": "sonnet", "status": 503, "message": "down", "at": now.isoformat().replace("+00:00", "Z"), "until": (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")},
        }}), encoding="utf-8")
        (store / "config.json").write_text(json.dumps({"format": "agentfleet.config.v1", "workStyle": "fleet"}), encoding="utf-8")
        env = {**os.environ, "CLAUDE_FLEET_HOME": str(self.home), "PYTHONDONTWRITEBYTECODE": "1"}
        result = subprocess.run([sys.executable, str(ROUTE)], input=json.dumps(self.payload("Update the README for this feature")), text=True, capture_output=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("failing (503", context)
        self.assertIn("Dispatch `fleet-ui`", context)

    def test_learning_opt_in_heads_tools_domains_commands_redaction_and_off(self):
        main = [
            assistant("main-1", [tool("Bash", {"command": "npm run build && python3 -m pytest"}), tool("WebFetch", {"url": "https://Example.COM/path"})]),
            rec("user", {"role": "user", "content": "/fleet-report --json"}),
            assistant("secret", [tool("Bash", {"command": "echo token=" + FAKE_KEY})]),
        ]
        self.write_records(self.transcript, main)
        self.assertEqual(fleet_watch.on_prompt(self.payload(), self.home), [])
        usage_dir = self.home / ".claude" / "agentfleet" / "usage"
        self.assertTrue(usage_dir.exists())  # learning is enabled when config is absent
        config = self.home / ".claude" / "agentfleet" / "config.json"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text('{"format":"agentfleet.config.v1","learn":true}', encoding="utf-8")
        fleet_watch.on_prompt(self.payload(), self.home)
        paths = list(usage_dir.glob("*.json"))
        self.assertEqual(len(paths), 1)
        data = json.loads(paths[0].read_text(encoding="utf-8"))
        self.assertEqual(data["bash"]["npm run build"]["count"], 1)
        self.assertEqual(data["bash"]["python3 -m pytest"]["count"], 1)
        self.assertIn("webfetch", {key.lower() for key in data["tools"]})
        self.assertEqual(data["domains"]["example.com"]["count"], 1)
        self.assertIn("/fleet-report", data["commands"])
        self.assertNotIn(FAKE_KEY, paths[0].read_text(encoding="utf-8"))

    def test_usage_retention_cap_and_per_session_counts(self):
        now = datetime.now(timezone.utc)
        data = {category: {} for category in ("bash", "tools", "domains", "commands")}
        for index in range(205):
            data["tools"][f"tool-{index}"] = {"last": (now - timedelta(minutes=index)).isoformat(), "count": 1, "sessions": 1}
        data["tools"]["old"] = {"last": (now - timedelta(days=31)).isoformat(), "count": 1}
        fleet_watch._prune_usage(data, now)
        self.assertEqual(len(data["tools"]), 200)
        self.assertNotIn("old", data["tools"])
        fleet_watch._usage_increment(data, "bash", "npm build", "s1", fleet_watch._iso(now))
        fleet_watch._usage_increment(data, "bash", "npm build", "s1", fleet_watch._iso(now))
        fleet_watch._usage_increment(data, "bash", "npm build", "s2", fleet_watch._iso(now))
        self.assertEqual(data["bash"]["npm build"]["sessions"], 2)

    def test_forbidden_reason_table(self):
        positives = {
            "git commit -am msg": "git-history", "git -C repo -c user.name=x reset --hard": "git-history",
            "git branch -D old": "git-history", "git worktree remove old": "git-history", "git tag v1": "git-history",
            "gh pr merge 2": "github-write", "gh release create v1": "github-write",
            "gh api /repos/o/r -X PATCH": "github-write", "npm publish": "publish-deploy",
            "twine upload dist/*": "publish-deploy", "kubectl apply -f x": "publish-deploy",
            "vercel --prod": "publish-deploy", "./scripts/deploy.sh": "publish-deploy",
            "scp file host:/tmp": "publish-deploy", "php artisan migrate:fresh": "migration",
            "php artisan db:wipe": "migration", "prisma migrate deploy": "migration",
            "manage.py flush": "migration", "dropdb test": "migration", "redis-cli flushall": "migration",
            "mysql -e 'DROP TABLE users'": "db-write", "psql -c 'DELETE FROM users'": "db-write",
            "rm -rf /": "rm-dangerous", "rm -rf ../other": "rm-dangerous", "rm -r .git": "rm-dangerous",
            "sudo env A=1 git push": "git-history", "echo ok; git push": "git-history",
            "echo $(git reset --hard)": "git-history", "bash -c 'gh issue close 1'": "github-write",
        }
        for command, expected in positives.items():
            with self.subTest(command=command):
                self.assertEqual(fleet_watch.forbidden_reason(command), expected)
        negatives = [
            "git status", "git diff", "git log", "git show", "git blame", "git grep x", "git ls-files",
            "git rev-parse HEAD", "git fetch", "git branch", "git stash list", "git tag --list",
            "python -m unittest", "pytest", "ruff check .", "npm run build", "psql -c 'SELECT 1'",
            "mysql -e 'DELETE FROM t WHERE id = 1'", "php artisan migrate:status", "php artisan migrate --pretend",
            "rm -rf build/ dist/ node_modules/ ./tmp", "rsync -a src/ dst/", "grep -rn 'DROP TABLE' .",
            'echo "git push"', "scp a C:\\tmp\\b",
        ]
        for command in negatives:
            with self.subTest(command=command):
                self.assertIsNone(fleet_watch.forbidden_reason(command))

    def test_corrupted_state_recovers_modes_and_report(self):
        store = self.home / ".claude" / "agentfleet"
        store.mkdir(parents=True)
        (store / "watch-state.json").write_text("{not json", encoding="utf-8")
        self.write_records(self.agent, [
            assistant("ok", [tool("Write", {"file_path": str(self.project / "x.txt")})]),
            assistant("finished", []),
        ])
        fleet_watch.on_prompt(self.payload(), self.home)
        fleet_watch.on_prompt(self.payload(), self.home)
        state = json.loads((store / "watch-state.json").read_text())
        self.assertEqual(state["format"], "agentfleet.watch-state.v1")
        report_data = fleet_watch.report(self.home)
        self.assertEqual(report_data["lanes"]["tests"]["runs"], 1)
        self.assertIn("Lane", fleet_watch.format_report(report_data))
        if os.name != "nt":
            for path in store.rglob("*"):
                if path.is_file():
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600, path)
            self.assertEqual(store.stat().st_mode & 0o777, 0o700)

    def test_byte_cap_and_missing_meta(self):
        huge = b"x" * (fleet_watch.SCAN_MAX_BYTES + 1024)
        self.agent.write_bytes(huge)
        started = __import__("time").monotonic()
        fleet_watch.on_prompt(self.payload(), self.home)
        self.assertLessEqual(__import__("time").monotonic() - started, 2.5)


if __name__ == "__main__":
    unittest.main()


class ForbiddenParityTests(unittest.TestCase):
    """Cases where the Python rules once disagreed with the lane-guard mod."""

    def test_manage_py_through_python_and_windows_drives(self):
        self.assertEqual(fleet_watch.forbidden_reason("python3 manage.py migrate"), "migration")
        self.assertIsNone(fleet_watch.forbidden_reason("python3 manage.py test"))
        self.assertIsNone(fleet_watch.forbidden_reason("scp C:\\a.txt D:\\b.txt"))
        self.assertEqual(fleet_watch.forbidden_reason("rsync -a dist/ root@host:/var/www"), "publish-deploy")


class LaneGuardHardeningParityTests(unittest.TestCase):
    """Enforces shared rule parity across the Python and TypeScript implementations."""

    def test_shared_cases_json_parity(self):
        cases_file = ROOT / "mods" / "lane-guard" / "tests" / "cases.json"
        self.assertTrue(cases_file.exists(), "cases.json must exist")
        cases = json.loads(cases_file.read_text(encoding="utf-8"))
        for tc in cases:
            command = tc["command"]
            expected = tc["rule"]
            with self.subTest(command=command):
                actual = fleet_watch.forbidden_reason(command)
                self.assertEqual(
                    actual,
                    expected,
                    f"Mismatch for command {command!r}: expected {expected!r}, got {actual!r}",
                )

    def test_secrets_redaction(self):
        cases = [
            ("mysql -pHunter2pass", "mysql -p[redacted]"),
            ("mysqladmin -pHunter2pass status", "mysqladmin -p[redacted] status"),
            ("--password=secret123", "--password=[redacted]"),
            ("--password secret123", "--password [redacted]"),
            ("MYSQL_PWD=hunter2", "MYSQL_PWD=[redacted]"),
            ("PGPASSWORD=hunter2", "PGPASSWORD=[redacted]"),
            ("https://user:pass123@github.com", "https://user:[redacted]@github.com"),
            ("postgresql://alice:secret123@db:5432/app", "postgresql://alice:[redacted]@db:5432/app"),
            ("sk_live_1234567890abcdef", "[redacted]"),
            ("sk_test_1234567890abcdef", "[redacted]"),
            ("rk_live_1234567890abcdef", "[redacted]"),
            ("github_pat_1234567890abcdef", "[redacted]"),
            ("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "AWS_SECRET_ACCESS_KEY=[redacted]"),
            ("Bearer my_token_12345", "Bearer [redacted]"),
            # Joined at runtime: the bundle's secret scan rejects key-shaped literals.
            ("sk-" + "1234567890abcdef", "[redacted]"),
            ("ghp_" + "1234567890abcdef", "[redacted]"),
            ("xoxb-" + "1234567890abcdef", "[redacted]"),
            ("user@example.com", "[redacted]"),
        ]
        for original, expected in cases:
            with self.subTest(original=original):
                redacted = fleet_watch.SECRETS.sub("[redacted]", original)
                self.assertEqual(redacted, expected)
                self.assertTrue(fleet_watch.SECRETS.search(original))


class SharedCasesMirrorTests(unittest.TestCase):
    def test_cases_ts_mirrors_cases_json(self):
        root = Path(__file__).resolve().parent.parent / "mods" / "lane-guard" / "tests"
        source = (root / "cases.ts").read_text(encoding="utf-8")
        mirrored = json.loads(source[source.index("= [") + 2:source.rindex("]") + 1])
        self.assertEqual(mirrored, json.loads((root / "cases.json").read_text(encoding="utf-8")),
                         "regenerate mods/lane-guard/tests/cases.ts from cases.json")

