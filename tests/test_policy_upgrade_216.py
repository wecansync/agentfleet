#!/usr/bin/env python3
"""Upgrades from 2.15.0 to 2.16.0: the managed policy (rules/agentfleet.md and
its template) is rewritten to the new rendering, the user's own preferences
are kept, and a later `agentfleet config` change still works.

The 2.15.0 policy bytes are a fixture (tests/fixtures), because a CI checkout
has no 2.15.0 commit to read them from."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
INSTALLER = ROOT / "bin" / "install.py"
AGENTFLEET = ROOT / "bin" / "agentfleet"
OLD_TEMPLATE = ROOT / "tests" / "fixtures" / "agentfleet-2.15.0-policy.md"
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "scripts"))
import provider_catalog  # noqa: E402

# Choices the user made that the upgrade must not change.
USER_CHOICES = {"workStyle": "fleet", "branchCheck": False, "compactPercent": 65, "learn": False}


@unittest.skipIf(shutil.which("node") is None, "Node.js is required for the agent generator")
class Upgrade216Tests(unittest.TestCase):
    def setUp(self):
        self.raw = tempfile.TemporaryDirectory(prefix="agentfleet 216 ")
        self.addCleanup(self.raw.cleanup)
        self.home = Path(self.raw.name) / "home"
        self.config = self.home / ".config"
        self.claude = self.home / ".claude"
        self.env = {
            "PATH": os.environ.get("PATH", ""), "HOME": str(self.home), "XDG_CONFIG_HOME": str(self.config),
            "PYTHONDONTWRITEBYTECODE": "1", "AGENTFLEET_MODS": "0", "AGENTFLEET_NONINTERACTIVE": "1",
            "AGENTFLEET_NO_CONFIGURE": "1", "CLAUDE_FLEET_HOME": str(self.home),
        }
        if os.name == "nt":
            self.env.update({name: os.environ[name] for name in ("SYSTEMROOT", "COMSPEC", "PATHEXT", "TEMP", "TMP") if name in os.environ})
            self.env["USERPROFILE"] = str(self.home)

    def installer(self, *options: str):
        command = [PYTHON, "-B", str(INSTALLER), *options, "--home", str(self.home), "--config-home", str(self.config),
                   "--prefix", str(self.home / ".local")]
        result = subprocess.run(command, cwd=ROOT, env=self.env, text=True, capture_output=True, stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return result

    def agentfleet(self, *args: str):
        return subprocess.run([PYTHON, "-B", str(AGENTFLEET), "--home", str(self.home), *args],
                              text=True, capture_output=True, env=self.env, cwd=ROOT, timeout=60)

    def simulate_2_15_install(self):
        """Leave the policy files and preferences as a 2.15.0 install left them."""
        old_template = OLD_TEMPLATE.read_text(encoding="utf-8")
        rules = self.claude / "rules" / "agentfleet.md"
        rules.write_text(provider_catalog.render_policy(old_template, "fleet", True, False), encoding="utf-8")
        (self.claude / "agentfleet" / "policy-template.md").write_text(old_template, encoding="utf-8")
        provider_catalog.write_preferences(self.home, USER_CHOICES)
        return old_template

    def test_upgrade_rewrites_the_policy_and_keeps_user_choices(self):
        self.installer("--apply", "--no-wizard", "--provider", "native")
        old_template = self.simulate_2_15_install()
        new_template = (ROOT / "config" / "agentfleet.md").read_text(encoding="utf-8")
        old_rendering = (self.claude / "rules" / "agentfleet.md").read_text(encoding="utf-8")

        self.installer("--apply", "--no-wizard")

        rules = (self.claude / "rules" / "agentfleet.md").read_text(encoding="utf-8")
        expected = provider_catalog.render_policy(new_template, "fleet", True, False)
        self.assertEqual(rules, expected)
        self.assertNotEqual(rules, old_rendering)
        # The new fleet rendering carries the 2.16.0 sections, and the branch check is still off.
        self.assertIn("**Several lanes:**", rules)
        self.assertIn("## Handoff", rules)
        self.assertNotIn("## Check the branch", rules)
        self.assertEqual((self.claude / "agentfleet" / "policy-template.md").read_text(encoding="utf-8"), new_template)
        self.assertNotEqual(new_template, old_template)
        preferences = provider_catalog.load_preferences(self.home)
        for key, value in USER_CHOICES.items():
            self.assertEqual(preferences[key], value, key)

    def test_config_change_after_upgrade_is_not_refused_as_user_edits(self):
        self.installer("--apply", "--no-wizard", "--provider", "native")
        self.simulate_2_15_install()
        self.installer("--apply", "--no-wizard")

        result = self.agentfleet("config", "ask-first", "off")

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertNotIn("your own edits", result.stderr)
        rules = (self.claude / "rules" / "agentfleet.md").read_text(encoding="utf-8")
        new_template = (ROOT / "config" / "agentfleet.md").read_text(encoding="utf-8")
        self.assertEqual(rules, provider_catalog.render_policy(new_template, "fleet", False, False))
        self.assertNotIn("**Several lanes:**", rules)
        preferences = provider_catalog.load_preferences(self.home)
        self.assertEqual(preferences["workStyle"], "fleet")
        self.assertEqual(preferences["compactPercent"], 65)
        self.assertFalse(preferences["branchCheck"])


if __name__ == "__main__":
    unittest.main()
