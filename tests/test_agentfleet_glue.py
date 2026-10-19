#!/usr/bin/env python3
"""Tests for agentfleet glue subcommands: report, suggest, and project."""
import sys
sys.dont_write_bytecode = True

import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
AGENTFLEET = REPO / "bin" / "agentfleet"


class TestAgentfleetGlue(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.sandbox = Path(self.temp_dir.name)
        self.home = self.sandbox / "home"
        self.home_claude = self.home / ".claude"
        self.home_claude.mkdir(parents=True)
        self.config_home = self.sandbox / "config"
        self.config_home.mkdir(parents=True)
        self.proj = self.sandbox / "project"
        self.proj.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_agentfleet(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        cmd = [
            sys.executable, "-B", str(AGENTFLEET),
            "--home", str(self.home),
            "--config-home", str(self.config_home),
            *args,
        ]
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(cwd or self.proj),
        )

    # -------------------------------------------------------------------------
    # 1. report command tests
    # -------------------------------------------------------------------------

    def test_jev_key_detected_from_settings_without_printing_it(self) -> None:
        import argparse
        import runpy
        from unittest.mock import patch
        module = runpy.run_path(str(AGENTFLEET), run_name="agentfleet_test")
        key = "sk-" + "1234567890" * 2
        (self.home_claude / "settings.json").write_text(json.dumps({"env": {"TYPESAFE_API_KEY": key}}))
        paths = module["Paths"](argparse.Namespace(home=str(self.home), config_home=str(self.config_home), prefix=None))
        import io
        output = io.StringIO()
        result = subprocess.CompletedProcess([], 0, '[{"id":"fast-jev-compaction@agentfleet","enabled":true}]')
        with patch.object(module["provider_catalog"], "claude_cli", return_value=result), patch.dict(os.environ, {}, clear=True), patch("sys.stdout", output):
            self.assertEqual(module["cmd_jev"](paths, argparse.Namespace(action="status")), 0)
        self.assertIn("TypeSafe API key: set", output.getvalue())
        self.assertNotIn(key, output.getvalue())

    def test_compact_off_removes_global_percent(self) -> None:
        settings_file = self.home_claude / "settings.json"
        settings_file.write_text(json.dumps({"model": "default", "autoCompactWindow": 1000000,
                                           "env": {"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "1000000",
                                                   "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "70"}}))
        result = self.run_agentfleet("config", "compact", "off")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Claude Code's default applies", result.stdout)
        settings = json.loads(settings_file.read_text())
        self.assertNotIn("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", settings["env"])
        self.assertEqual(settings["autoCompactWindow"], 1000000)

    def test_report_empty_case(self) -> None:
        # Text output when no lane runs or main usage exist
        proc = self.run_agentfleet("report")
        self.assertEqual(proc.returncode, 0, f"stderr: {proc.stderr}")
        self.assertIn("No lane runs or usage recorded in the last 7 days.", proc.stdout)

        # JSON output when no reports exist
        proc_json = self.run_agentfleet("report", "--json")
        self.assertEqual(proc_json.returncode, 0, f"stderr: {proc_json.stderr}")
        data = json.loads(proc_json.stdout)
        self.assertIsInstance(data, dict)
        self.assertEqual(data.get("lanes"), {})
        self.assertEqual(data.get("totals", {}).get("runs"), 0)

        # Main-session usage alone is enough to render the report.
        store = self.home_claude / "agentfleet"
        store.mkdir(parents=True, exist_ok=True)
        today = dt.datetime.now(dt.timezone.utc).date().isoformat()
        (store / "main-usage.json").write_text(json.dumps({
            "format": "agentfleet.main-usage.v1",
            "days": {today: {"claude-sonnet": {"inputTokens": 7, "outputTokens": 2,
                "cacheReadTokens": 0, "cacheWriteTokens": 0, "messages": 1}}},
            "transcripts": {},
        }), encoding="utf-8")
        proc_main = self.run_agentfleet("report")
        self.assertEqual(proc_main.returncode, 0, f"stderr: {proc_main.stderr}")
        self.assertIn("Main conversation", proc_main.stdout)
        self.assertIn("claude-sonnet", proc_main.stdout)

    def test_report_crafted_lane_reports(self) -> None:
        watch_store = self.home_claude / "agentfleet"
        watch_store.mkdir(parents=True, exist_ok=True)
        reports_file = watch_store / "lane-reports.jsonl"

        now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry_api_error = {
            "at": now_iso,
            "lane": "implement",
            "model": "claude-sonnet-4",
            "apiError": 500,
            "apiMessage": "Overloaded",
            "files": ["src/app.py"],
            "inputTokens": 150,
            "outputTokens": 60,
        }
        entry_forbidden = {
            "at": now_iso,
            "lane": "review",
            "model": "claude-opus-4",
            "forbidden": [{"rule": "git-history", "command": "git push"}],
            "files": [],
            "inputTokens": 300,
            "outputTokens": 120,
        }
        lines = json.dumps(entry_api_error) + "\n" + json.dumps(entry_forbidden) + "\n"
        reports_file.write_text(lines, encoding="utf-8")

        # Text mode
        proc = self.run_agentfleet("report")
        self.assertEqual(proc.returncode, 0, f"stderr: {proc.stderr}")
        self.assertIn("AgentFleet report, last 7 days", proc.stdout)
        self.assertIn("implement", proc.stdout)
        self.assertIn("review", proc.stdout)

        # JSON mode
        proc_json = self.run_agentfleet("report", "--json")
        self.assertEqual(proc_json.returncode, 0, f"stderr: {proc_json.stderr}")
        data = json.loads(proc_json.stdout)
        self.assertIn("implement", data["lanes"])
        self.assertEqual(data["lanes"]["implement"]["runs"], 1)
        self.assertEqual(data["lanes"]["implement"]["failures"], 1)
        self.assertIn("review", data["lanes"])
        self.assertEqual(data["lanes"]["review"]["runs"], 1)
        self.assertEqual(data["lanes"]["review"]["forbidden"], 1)
        self.assertEqual(data["totals"]["runs"], 2)
        self.assertEqual(data["totals"]["failures"], 1)
        self.assertEqual(data["totals"]["forbidden"], 1)

    # -------------------------------------------------------------------------
    # 2. suggest command tests
    # -------------------------------------------------------------------------

    def test_suggest_json_and_forget(self) -> None:
        (self.proj / "package.json").write_text('{"name": "test-pkg", "dependencies": {"express": "4.18.2"}}')

        sandbox_store = self.home_claude / "agentfleet" / "suggestions.json"
        real_home_store = Path.home() / ".claude" / "agentfleet" / "suggestions.json"
        real_mtime_before = real_home_store.stat().st_mtime if real_home_store.exists() else None

        # Run suggest --json
        proc = self.run_agentfleet("suggest", "--project", str(self.proj), "--json")
        self.assertEqual(proc.returncode, 0, f"stderr: {proc.stderr}")
        data = json.loads(proc.stdout)
        self.assertIn("suggestions", data)
        self.assertEqual(data["project"], str(self.proj.resolve()))

        # Verify sandbox store was written
        self.assertTrue(sandbox_store.is_file(), f"Expected {sandbox_store} to be written")

        # Verify real home was NOT modified
        if real_home_store.exists():
            self.assertEqual(real_home_store.stat().st_mtime, real_mtime_before)

        # Run suggest --forget
        proc_forget = self.run_agentfleet("suggest", "--project", str(self.proj), "--forget")
        self.assertEqual(proc_forget.returncode, 0, f"stderr: {proc_forget.stderr}")
        self.assertFalse(sandbox_store.exists(), f"Expected {sandbox_store} to be removed by --forget")

    # -------------------------------------------------------------------------
    # 3. project pin/show/unpin end-to-end tests
    # -------------------------------------------------------------------------

    def _setup_fleet_and_settings(self) -> None:
        fleet = {
            "version": "delegate-fleet.v1",
            "lanes": {
                "implement": {
                    "implementer": "claude",
                    "model": "claude-sonnet-4",
                },
                "review": {
                    "implementer": "claude",
                    "model": "claude-opus-4",
                },
            },
        }
        (self.home_claude / "fleet.json").write_text(json.dumps(fleet), encoding="utf-8")

        settings = {
            "modelPicker": {
                "options": [
                    {"model": "claude-sonnet-4", "label": "Sonnet"},
                    {"model": "claude-opus-4", "label": "Opus"},
                    {"model": "claude-haiku-4", "label": "Haiku"},
                ]
            }
        }
        (self.home_claude / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

    def test_project_pin_show_unpin_e2e(self) -> None:
        if not shutil.which("node"):
            self.skipTest("node is not available")
        self._setup_fleet_and_settings()

        # Step 1: Initial show
        proc_show_init = self.run_agentfleet("project", "show", "--dir", str(self.proj))
        self.assertEqual(proc_show_init.returncode, 0, f"stderr: {proc_show_init.stderr}")
        self.assertIn("No project lane overrides.", proc_show_init.stdout)

        # Step 2: Pin with 'implement' (without prefix)
        proc_pin = self.run_agentfleet(
            "project", "pin", "implement", "claude-haiku-4",
            "--fallbacks", "claude-sonnet-4,claude-opus-4",
            "--dir", str(self.proj),
        )
        self.assertEqual(proc_pin.returncode, 0, f"stderr: {proc_pin.stderr}")
        self.assertIn("Wrote", proc_pin.stdout)
        self.assertIn("fleet-implement.md", proc_pin.stdout)
        self.assertIn("Restart Claude Code (or start a new session) in this project to use it.", proc_pin.stdout)

        local_fleet_path = self.proj / ".claude" / "fleet.local.json"
        self.assertTrue(local_fleet_path.is_file())
        local_data = json.loads(local_fleet_path.read_text(encoding="utf-8"))
        self.assertEqual(local_data["version"], "delegate-fleet.local.v1")
        self.assertIn("implement", local_data["lanes"])
        self.assertEqual(local_data["lanes"]["implement"]["model"], "claude-haiku-4")
        self.assertEqual(local_data["lanes"]["implement"]["fallbacks"], ["claude-sonnet-4", "claude-opus-4"])

        agent_file = self.proj / ".claude" / "agents" / "fleet-implement.md"
        self.assertTrue(agent_file.is_file())
        agent_content = agent_file.read_text(encoding="utf-8")
        self.assertIn('model: "claude-haiku-4"', agent_content)

        # Step 3: Show after pin
        proc_show = self.run_agentfleet("project", "show", "--dir", str(self.proj))
        self.assertEqual(proc_show.returncode, 0, f"stderr: {proc_show.stderr}")
        self.assertIn("implement -> claude-haiku-4 (fallbacks: claude-sonnet-4, claude-opus-4)", proc_show.stdout)
        self.assertIn("fleet-implement.md", proc_show.stdout)

        # Step 4: Unpin using 'fleet-implement' (with prefix)
        proc_unpin = self.run_agentfleet("project", "unpin", "fleet-implement", "--dir", str(self.proj))
        self.assertEqual(proc_unpin.returncode, 0, f"stderr: {proc_unpin.stderr}")
        self.assertIn("Unpinned", proc_unpin.stdout)

        # Stale agent file pruned
        self.assertFalse(agent_file.exists(), f"Expected {agent_file} to be pruned after unpin")
        # Local fleet file remains with empty lanes
        local_after = json.loads(local_fleet_path.read_text(encoding="utf-8"))
        self.assertEqual(local_after.get("lanes"), {})

        # Step 5: Show after unpin
        proc_show_end = self.run_agentfleet("project", "show", "--dir", str(self.proj))
        self.assertEqual(proc_show_end.returncode, 0, f"stderr: {proc_show_end.stderr}")
        self.assertIn("No project lane overrides.", proc_show_end.stdout)

    # -------------------------------------------------------------------------
    # 4. pin with unknown lane -> error
    # -------------------------------------------------------------------------

    def test_pin_unknown_lane_error(self) -> None:
        self._setup_fleet_and_settings()

        proc = self.run_agentfleet("project", "pin", "unknown-lane", "claude-sonnet-4", "--dir", str(self.proj))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("unknown lane 'unknown-lane'", proc.stderr)
        self.assertIn("implement", proc.stderr)
        self.assertIn("review", proc.stderr)

        local_fleet_path = self.proj / ".claude" / "fleet.local.json"
        self.assertFalse(local_fleet_path.exists())

    # -------------------------------------------------------------------------
    # 5. pin with model not in picker -> generator error and previous file restored
    # -------------------------------------------------------------------------

    def test_pin_model_not_in_picker_restores_previous(self) -> None:
        if not shutil.which("node"):
            self.skipTest("node is not available")
        self._setup_fleet_and_settings()

        local_fleet_path = self.proj / ".claude" / "fleet.local.json"

        # Case A: When no previous fleet.local.json existed, file is removed on error
        proc_fail1 = self.run_agentfleet(
            "project", "pin", "implement", "non-existent-model", "--dir", str(self.proj)
        )
        self.assertEqual(proc_fail1.returncode, 1)
        self.assertIn("claude-fleet-sync", proc_fail1.stderr)
        self.assertFalse(local_fleet_path.exists(), "fleet.local.json should be removed when initial pin fails")

        # Case B: When previous valid fleet.local.json existed, previous content is restored
        proc_ok = self.run_agentfleet("project", "pin", "implement", "claude-sonnet-4", "--dir", str(self.proj))
        self.assertEqual(proc_ok.returncode, 0, f"stderr: {proc_ok.stderr}")
        initial_content = local_fleet_path.read_text(encoding="utf-8")

        proc_fail2 = self.run_agentfleet(
            "project", "pin", "implement", "non-existent-model-2", "--dir", str(self.proj)
        )
        self.assertEqual(proc_fail2.returncode, 1)
        self.assertIn("claude-fleet-sync", proc_fail2.stderr)
        restored_content = local_fleet_path.read_text(encoding="utf-8")
        self.assertEqual(restored_content, initial_content, "fleet.local.json should be restored to prior content")


if __name__ == "__main__":
    unittest.main()
