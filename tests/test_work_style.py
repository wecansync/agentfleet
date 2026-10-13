#!/usr/bin/env python3
"""Focused tests for work-style routing, preferences, and managed policy rendering."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ROUTE = ROOT / "scripts" / "route-to-fleet.py"
AGENTFLEET = ROOT / "bin" / "agentfleet"
sys.path.insert(0, str(ROOT / "scripts"))
import provider_catalog


class WorkStyleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="agentfleet-work-style-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.claude = self.home / ".claude"
        self.claude.mkdir(parents=True)
        self.project = self.root / "project"
        self.project.mkdir()
        # A fake claude first on PATH and no inherited provider credentials.
        fake_bin = self.root / "fake-bin"
        fake_bin.mkdir()
        claude = fake_bin / ("claude.cmd" if os.name == "nt" else "claude")
        if os.name == "nt":
            claude.write_text("@echo []\n", encoding="utf-8")
        else:
            claude.write_text("#!/bin/sh\necho '[]'\n", encoding="utf-8")
            claude.chmod(0o755)
        keep = ("SYSTEMROOT", "COMSPEC", "PATHEXT", "TEMP", "TMP", "LANG")
        self.env = {name: os.environ[name] for name in keep if name in os.environ}
        self.env.update({"PATH": str(fake_bin) + os.pathsep + os.defpath, "HOME": str(self.home),
                         "USERPROFILE": str(self.home), "CLAUDE_FLEET_HOME": str(self.home),
                         "PYTHONDONTWRITEBYTECODE": "1"})

    def set_style(self, style: str):
        provider_catalog.write_preferences(self.home, {"workStyle": style})

    def route(self, prompt: str, style: str | None = "balanced") -> str:
        if style is None:
            provider_catalog.preferences_path(self.home).unlink(missing_ok=True)
        else:
            self.set_style(style)
        result = subprocess.run([sys.executable, "-B", str(ROUTE)],
                                input=json.dumps({"cwd": str(self.project), "prompt": prompt}),
                                text=True, capture_output=True, env=self.env, cwd=ROOT, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        if not result.stdout.strip():
            return ""
        data = json.loads(result.stdout)
        return data.get("hookSpecificOutput", {}).get("additionalContext", "")

    def cli(self, *args: str, env: dict[str, str] | None = None):
        return subprocess.run([sys.executable, "-B", str(AGENTFLEET), "--home", str(self.home), *args],
                              text=True, capture_output=True, env=env or self.env, cwd=ROOT, timeout=20)

    def test_balanced_small_and_large_list_routing(self):
        small = self.route("fix the typo in the README title")
        self.assertNotIn("Routing:", small)
        large = self.route("Implement these independent changes:\n1. Add the first endpoint.\n2. Add the second endpoint.\n3. Add the third endpoint.")
        self.assertIn("Routing:", large)
        self.assertIn("consider dispatching", large)
        self.assertIn("otherwise do it yourself", large)

    def test_balanced_security_review_is_large_but_unmatched_large_text_is_silent(self):
        review = self.route("security review of this branch")
        self.assertIn("Routing:", review)
        unmatched = self.route("Please summarize the current status for me. " + ("There are several details to consider. " * 12))
        self.assertNotIn("Routing:", unmatched)

    def test_direct_never_routes_and_fleet_preserves_existing_routing(self):
        direct = self.route("fix the typo in the README title", "direct")
        self.assertNotIn("Routing:", direct)
        fleet = self.route("fix the typo in the README title", "fleet")
        self.assertIn("Dispatch `fleet-implement`", fleet)

    def test_missing_preference_uses_balanced_default(self):
        context = self.route("fix the typo in the README title", None)
        self.assertNotIn("Routing:", context)

    def test_balanced_outage_uses_alternative_lane(self):
        self.set_style("balanced")
        (self.claude / "fleet.json").write_text(json.dumps({"lanes": {
            "implement": {"model": "sonnet"},
            "implement-alt": {"model": "opus", "altOf": "implement"},
        }}), encoding="utf-8")
        store = self.claude / "agentfleet"
        store.mkdir(parents=True, exist_ok=True)
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        (store / "outages.json").write_text(json.dumps({"format": "agentfleet.outages.v1", "lanes": {
            "implement": {"model": "sonnet", "status": 503,
                          "at": now.isoformat().replace("+00:00", "Z"),
                          "until": (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")},
        }}), encoding="utf-8")
        context = self.route("Implement this large update across all files: " + ("Refactor each module carefully. " * 14))
        self.assertIn("Routing:", context)
        self.assertIn("consider dispatching `fleet-implement-alt`", context)
        self.assertIn("`fleet-implement` lane is failing", context)

    def test_balanced_small_task_is_logged_with_small_task_reason(self):
        self.set_style("balanced")
        context = self.route("fix the typo in the README title")
        self.assertNotIn("Routing:", context)
        entries = [json.loads(line) for line in (self.claude / "routing-log.jsonl").read_text().splitlines()]
        self.assertTrue(any(item["reason"] == "small-task" for item in entries))

    def test_config_work_style_renders_only_managed_policy(self):
        store = self.claude / "agentfleet"
        store.mkdir(parents=True)
        template = (ROOT / "config" / "agentfleet.md").read_text(encoding="utf-8")
        (store / "policy-template.md").write_text(template, encoding="utf-8")
        result = self.cli("config", "work-style", "direct")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Work style set to direct. Restart Claude Code to apply.", result.stdout)
        rendered_path = self.claude / "rules" / "agentfleet.md"
        rendered = rendered_path.read_text(encoding="utf-8")
        self.assertIn("Do all work yourself.", rendered)
        self.assertNotIn("work-style:", rendered)
        if os.name == "posix":
            self.assertEqual(rendered_path.stat().st_mode & 0o777, 0o644)

    def test_config_work_style_preserves_unmarked_policy_and_rejects_invalid_style(self):
        store = self.claude / "agentfleet"
        store.mkdir(parents=True)
        (store / "policy-template.md").write_text((ROOT / "config" / "agentfleet.md").read_text(encoding="utf-8"), encoding="utf-8")
        rules = self.claude / "rules"
        rules.mkdir()
        target = rules / "agentfleet.md"
        original = b"my own instruction rules\n"
        target.write_bytes(original)
        result = self.cli("config", "work-style", "balanced")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(target.read_bytes(), original)
        invalid = self.cli("config", "work-style", "bogus")
        self.assertEqual(invalid.returncode, 2)

    def test_config_show_defaults_and_configure_preset_round_trip(self):
        shown = self.cli("config")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn("work-style: balanced (default)", shown.stdout)
        self.assertIn("agent-brain: auto (default)", shown.stdout)
        preset = self.root / "preset.json"
        exported = self.cli("configure", "--export", str(preset))
        self.assertEqual(exported.returncode, 0, exported.stderr)
        data = json.loads(preset.read_text(encoding="utf-8"))
        self.assertEqual(data["workStyle"], "balanced")
        self.assertIn("agentBrain", data)
        self.assertNotIn("instructions", data)
        data["workStyle"] = "direct"
        preset.write_text(json.dumps(data), encoding="utf-8")
        store = self.claude / "agentfleet"
        store.mkdir(parents=True, exist_ok=True)
        (store / "policy-template.md").write_text((ROOT / "config" / "agentfleet.md").read_text(encoding="utf-8"), encoding="utf-8")
        applied = self.cli("configure", "--preset", str(preset))
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual(provider_catalog.load_preferences(self.home)["workStyle"], "direct")
        self.assertIn("Do all work yourself.", (self.claude / "rules" / "agentfleet.md").read_text(encoding="utf-8"))
        data["workStyle"] = "bogus"
        preset.write_text(json.dumps(data), encoding="utf-8")
        invalid = self.cli("configure", "--preset", str(preset))
        self.assertEqual(invalid.returncode, 2)

    def test_preset_agent_brain_choice_is_saved_without_agent_brain_installed(self):
        preset = self.root / "preset.json"
        preset.write_text(json.dumps({"format": "agentfleet.preset.v1", "agentBrain": False}), encoding="utf-8")
        applied = self.cli("configure", "--preset", str(preset))
        self.assertEqual(applied.returncode, 0, applied.stderr)
        # Saved explicitly, so installing agent-brain later keeps it off.
        self.assertIs(provider_catalog.load_preferences(self.home)["agentBrain"], False)

    def test_config_agent_brain_passes_the_install_prefix_to_the_installer(self):
        releases = self.root / "releases"
        release = releases / "releases" / "9.9.9" / "bin"
        release.mkdir(parents=True)
        (releases / "current").write_text("9.9.9\n", encoding="utf-8")
        argv_file = self.root / "installer-argv.json"
        (release / "install.py").write_text(
            "import json, sys\n"
            f"open({str(argv_file)!r}, 'w', encoding='utf-8').write(json.dumps(sys.argv[1:]))\n",
            encoding="utf-8")
        prefix = self.root / "custom prefix"
        prefix.mkdir()
        env = dict(self.env, AGENTFLEET_HOME=str(releases))
        result = self.cli("--prefix", str(prefix), "config", "agent-brain", "off", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        argv = json.loads(argv_file.read_text(encoding="utf-8"))
        self.assertIn("--prefix", argv)
        self.assertEqual(Path(argv[argv.index("--prefix") + 1]), prefix.resolve())

    def test_wizard_offers_instruction_check_and_yes_skips_it(self):
        sys.path.insert(0, str(ROOT / "bin"))
        import importlib.machinery
        import importlib.util
        from unittest import mock
        loader = importlib.machinery.SourceFileLoader("agentfleet_cli_ws", str(AGENTFLEET))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        api = importlib.util.module_from_spec(spec)
        loader.exec_module(api)
        import fleet_configure
        import argparse
        paths = api.Paths(argparse.Namespace(home=str(self.home), config_home=str(self.home / ".config"), prefix=None))
        state = paths.store / "wizard.json"
        calls = []
        fake_module = mock.Mock(run=lambda *args, **kwargs: calls.append(kwargs) or 0)
        everything_but_check = [f.key for f in fleet_configure.FEATURES if f.key != "instructions"]
        paths.store.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"format": "agentfleet.wizard.v1", "asked": everything_but_check}), encoding="utf-8")
        args = mock.Mock(after_install=False, yes=True, preset=None, export=None)
        with mock.patch.dict(os.environ, self.env, clear=True), \
                mock.patch.object(api, "instructions_module", return_value=fake_module):
            self.assertEqual(fleet_configure.run(api, paths, args), 0)
            self.assertEqual(calls, [])
            self.assertNotIn("instructions", json.loads(state.read_text())["asked"])
            args = mock.Mock(after_install=True, yes=False, preset=None, export=None)
            with mock.patch("sys.stdin.isatty", return_value=True), \
                    mock.patch("sys.stdout.isatty", return_value=True), \
                    mock.patch("builtins.input", return_value="y"):
                self.assertEqual(fleet_configure.run(api, paths, args), 0)
        self.assertEqual(calls, [{"project": None, "interactive": True}])
        self.assertIn("instructions", json.loads(state.read_text())["asked"])


if __name__ == "__main__":
    unittest.main()
