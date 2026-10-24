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
            (fake_bin / "claude.py").write_text("print('[]')\n", encoding="utf-8")
            claude.write_text(f'@"{sys.executable}" "%~dp0claude.py" %*\n', encoding="utf-8")
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

    def test_balanced_never_routes_whatever_the_wording(self):
        # The wording says nothing reliable about the size of the job, so
        # balanced mode leaves the decision to Claude after it reads the code.
        prompts = [
            "fix the typo in the README title",
            "Implement these independent changes:\n1. Add the first endpoint.\n2. Add the second endpoint.\n3. Add the third endpoint.",
            "security review of this branch",
            "Implement this large update across all files: " + ("Refactor each module carefully. " * 14),
            "apply the fixes, build the binary and create the docs site",
        ]
        for prompt in prompts:
            with self.subTest(prompt=prompt[:40]):
                self.assertNotIn("Routing:", self.route(prompt))

    def test_direct_never_routes_and_fleet_preserves_existing_routing(self):
        direct = self.route("fix the typo in the README title", "direct")
        self.assertNotIn("Routing:", direct)
        fleet = self.route("fix the typo in the README title", "fleet")
        self.assertIn("Dispatch `fleet-implement`", fleet)

    def test_missing_preference_uses_balanced_default(self):
        context = self.route("fix the typo in the README title", None)
        self.assertNotIn("Routing:", context)

    def test_balanced_stored_outage_adds_no_routing_line(self):
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
        # The outage notice went out when the failure was seen; balanced mode
        # does not repeat it as a routing line on later prompts.
        context = self.route("Implement this large update across all files: " + ("Refactor each module carefully. " * 14))
        self.assertNotIn("Routing:", context)

    def test_balanced_prompt_is_logged_without_its_text(self):
        self.set_style("balanced")
        self.route("fix the typo in the README title")
        entries = [json.loads(line) for line in (self.claude / "routing-log.jsonl").read_text().splitlines()]
        self.assertTrue(entries)
        self.assertTrue(all(item["reason"] == "balanced" for item in entries))
        self.assertTrue(all("prompt" not in item for item in entries))

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

    def test_config_keeps_a_managed_policy_with_user_edits(self):
        store = self.claude / "agentfleet"
        store.mkdir(parents=True)
        template = (ROOT / "config" / "agentfleet.md").read_text(encoding="utf-8")
        (store / "policy-template.md").write_text(template, encoding="utf-8")
        self.assertEqual(self.cli("config", "work-style", "balanced").returncode, 0)
        target = self.claude / "rules" / "agentfleet.md"
        edited = target.read_text(encoding="utf-8") + "\nMY OWN RULE\n"
        target.write_text(edited, encoding="utf-8")
        for args in (("ask-first", "off"), ("work-style", "direct")):
            result = self.cli("config", *args)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("has your own edits", result.stderr)
            self.assertNotIn("Restart Claude Code", result.stdout)
            self.assertEqual(target.read_text(encoding="utf-8"), edited)
        # Removing the edit lets the next run re-render with the saved choices.
        target.write_text(provider_catalog.render_policy(template, "balanced", True), encoding="utf-8")
        self.assertEqual(self.cli("config", "work-style", "direct").returncode, 0)
        self.assertEqual(target.read_text(encoding="utf-8"), provider_catalog.render_policy(template, "direct", False))

    def test_policy_renders_ask_first_section_per_preference(self):
        template = (ROOT / "config" / "agentfleet.md").read_text(encoding="utf-8")
        for style in provider_catalog.WORK_STYLES:
            with self.subTest(style=style):
                on = provider_catalog.render_policy(template, style, True)
                off = provider_catalog.render_policy(template, style, False)
                for rendered in (on, off):
                    self.assertNotIn("<!-- ask-first", rendered)
                    self.assertNotIn("<!-- /", rendered)
                    self.assertNotIn("work-style:", rendered)
                self.assertIn("## How to approach a task", on)
                self.assertIn("Do small, clear tasks straight away.", on)
                self.assertIn("/speckit-constitution", on)
                self.assertNotIn("How to approach a task", off)
                self.assertNotIn("speckit", off)
                # The rest of the policy is the same either way.
                self.assertEqual(off, on.replace(on[on.index("## How to approach a task"):on.index("## Check the branch")], ""))
                self.assertEqual("with a review lane only for risky parts" in on, style == "balanced")
                self.assertEqual("then your own review of what they changed" in on, style == "fleet")
                self.assertEqual("then build, test and fix it yourself" in on, style == "direct")
                self.assertIn('--integration-options="--no-model-invocation"', on)
        self.assertEqual(provider_catalog.render_policy_for(template, {"workStyle": "direct"}),
                         provider_catalog.render_policy(template, "direct", True))
        self.assertEqual(provider_catalog.render_policy_for(template, {"workStyle": "direct", "askFirst": False}),
                         provider_catalog.render_policy(template, "direct", False))

    def test_policy_renders_branch_check_section_per_preference(self):
        template = (ROOT / "config" / "agentfleet.md").read_text(encoding="utf-8")
        for style in provider_catalog.WORK_STYLES:
            for ask_first in (True, False):
                with self.subTest(style=style, ask_first=ask_first):
                    on = provider_catalog.render_policy(template, style, ask_first, True)
                    off = provider_catalog.render_policy(template, style, ask_first, False)
                    for rendered in (on, off):
                        self.assertNotIn("branch-check -->", rendered)
                        self.assertNotIn("<!-- /", rendered)
                    self.assertIn("## Check the branch before changing files", on)
                    self.assertIn("git fetch --quiet", on)
                    self.assertIn("continue on this branch, switch to another, or create a new one", on)
                    self.assertIn("lanes never do these at all", on)
                    self.assertNotIn("Check the branch", off)
                    self.assertEqual(off, on.replace(on[on.index("## Check the branch"):on.index("## Make the smallest complete change")], ""))
                    # The smallest-change rules render whatever the toggles say.
                    self.assertIn("## Make the smallest complete change", off)
        self.assertEqual(provider_catalog.render_policy_for(template, {"branchCheck": False}),
                         provider_catalog.render_policy(template, "balanced", True, False))
        self.assertEqual(len(provider_catalog.policy_renderings(template)), 12)

    def test_config_branch_check_rerenders_policy_and_keeps_other_choices(self):
        store = self.claude / "agentfleet"
        store.mkdir(parents=True)
        template = (ROOT / "config" / "agentfleet.md").read_text(encoding="utf-8")
        (store / "policy-template.md").write_text(template, encoding="utf-8")
        self.assertIn("branch-check: on (default)", self.cli("config").stdout)
        self.assertEqual(self.cli("config", "ask-first", "off").returncode, 0)
        result = self.cli("config", "branch-check", "off")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Restart Claude Code to apply.", result.stdout)
        rules = self.claude / "rules" / "agentfleet.md"
        self.assertEqual(rules.read_text(encoding="utf-8"), provider_catalog.render_policy(template, "balanced", False, False))
        self.assertIn("branch-check: off", self.cli("config").stdout)
        self.assertEqual(self.cli("config", "work-style", "direct").returncode, 0)
        self.assertEqual(rules.read_text(encoding="utf-8"), provider_catalog.render_policy(template, "direct", False, False))
        self.assertEqual(self.cli("config", "branch-check", "on").returncode, 0)
        self.assertEqual(rules.read_text(encoding="utf-8"), provider_catalog.render_policy(template, "direct", False, True))
        self.assertEqual(self.cli("config", "branch-check", "sometimes").returncode, 2)
        with self.assertRaises(ValueError):
            provider_catalog.write_preferences(self.home, {"branchCheck": "yes"})

    def test_config_ask_first_rerenders_policy_and_keeps_work_style(self):
        store = self.claude / "agentfleet"
        store.mkdir(parents=True)
        template = (ROOT / "config" / "agentfleet.md").read_text(encoding="utf-8")
        (store / "policy-template.md").write_text(template, encoding="utf-8")
        self.assertIn("ask-first: on (default)", self.cli("config").stdout)
        self.assertEqual(self.cli("config", "work-style", "direct").returncode, 0)
        result = self.cli("config", "ask-first", "off")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Restart Claude Code to apply.", result.stdout)
        rules = self.claude / "rules" / "agentfleet.md"
        self.assertEqual(rules.read_text(encoding="utf-8"), provider_catalog.render_policy(template, "direct", False))
        self.assertIn("ask-first: off", self.cli("config").stdout)
        self.assertNotIn("ask-first: off (default)", self.cli("config").stdout)
        # Changing the work style afterwards keeps the ask-first choice.
        self.assertEqual(self.cli("config", "work-style", "balanced").returncode, 0)
        self.assertEqual(rules.read_text(encoding="utf-8"), provider_catalog.render_policy(template, "balanced", False))
        self.assertEqual(self.cli("config", "ask-first", "on").returncode, 0)
        self.assertEqual(rules.read_text(encoding="utf-8"), provider_catalog.render_policy(template, "balanced", True))
        self.assertEqual(self.cli("config", "ask-first", "maybe").returncode, 2)
        self.assertTrue(provider_catalog.load_preferences(self.home)["askFirst"])
        with self.assertRaises(ValueError):
            provider_catalog.write_preferences(self.home, {"askFirst": "yes"})

    def test_preset_carries_ask_first(self):
        store = self.claude / "agentfleet"
        store.mkdir(parents=True)
        template = (ROOT / "config" / "agentfleet.md").read_text(encoding="utf-8")
        (store / "policy-template.md").write_text(template, encoding="utf-8")
        preset = self.root / "preset.json"
        self.assertEqual(self.cli("configure", "--export", str(preset)).returncode, 0)
        data = json.loads(preset.read_text(encoding="utf-8"))
        self.assertIs(data["askFirst"], True)
        data["askFirst"] = False
        preset.write_text(json.dumps(data), encoding="utf-8")
        applied = self.cli("configure", "--preset", str(preset))
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertFalse(provider_catalog.load_preferences(self.home)["askFirst"])
        self.assertNotIn("How to approach a task", (self.claude / "rules" / "agentfleet.md").read_text(encoding="utf-8"))
        data["askFirst"] = "no"
        preset.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(self.cli("configure", "--preset", str(preset)).returncode, 2)

    def test_config_show_defaults_and_configure_preset_round_trip(self):
        shown = self.cli("config")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn("work-style: balanced (default)", shown.stdout)
        self.assertIn("agent-brain: not installed", shown.stdout)
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

    def test_config_shows_agent_brain_state_by_install_and_choice(self):
        def line(env=None):
            shown = self.cli("config", env=env)
            self.assertEqual(shown.returncode, 0, shown.stderr)
            return next(text for text in shown.stdout.splitlines() if text.startswith("agent-brain:"))
        self.assertEqual(line(), "agent-brain: not installed")
        provider_catalog.write_preferences(self.home, {"agentBrain": True})
        self.assertEqual(line(), "agent-brain: on, not installed")
        provider_catalog.write_preferences(self.home, {"agentBrain": False})
        self.assertEqual(line(), "agent-brain: off")
        brain_bin = self.root / "brain-bin"
        brain_bin.mkdir()
        brain = brain_bin / ("agent-brain.cmd" if os.name == "nt" else "agent-brain")
        if os.name == "nt":
            (brain_bin / "agent-brain.py").write_text("pass\n", encoding="utf-8")
            brain.write_text(f'@"{sys.executable}" "%~dp0agent-brain.py" %*\n', encoding="utf-8")
        else:
            brain.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            brain.chmod(0o755)
        env = dict(self.env, PATH=str(brain_bin) + os.pathsep + self.env["PATH"])
        provider_catalog.preferences_path(self.home).unlink(missing_ok=True)
        self.assertEqual(line(env), "agent-brain: auto (default), not connected")
        hook = {"type": "command", "command": f"{brain} hook stop"}
        (self.claude / "settings.json").write_text(json.dumps({"hooks": {"Stop": [{"hooks": [hook]}]}}), encoding="utf-8")
        self.assertEqual(line(env), "agent-brain: auto (default)")
        # A pre-2.6.0 AgentFleet copy of the hook is not agent-brain's own.
        hook["command"] += " # claude-agents-config:agent-brain"
        (self.claude / "settings.json").write_text(json.dumps({"hooks": {"Stop": [{"hooks": [hook]}]}}), encoding="utf-8")
        self.assertEqual(line(env), "agent-brain: auto (default), not connected")

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

    def test_instructions_command_runs_from_the_cli(self):
        # The real loader, not a mock: the module must import from the CLI.
        target = self.claude / "CLAUDE.md"
        original = b"Rule local.\nRule local.\n"
        target.write_bytes(original)
        result = self.cli("instructions", "--check")
        # The fake claude cannot do the model check, so the exit code is 1
        # either way; the report itself shows the command really ran.
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("Kind: duplicate", result.stdout, result.stderr)
        self.assertIn("Applied 0 edit(s).", result.stdout)
        self.assertEqual(target.read_bytes(), original)

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
