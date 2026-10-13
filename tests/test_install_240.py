#!/usr/bin/env python3
"""Installs and upgrades to 2.4.0: the policy moves from ~/.claude/CLAUDE.md
to ~/.claude/rules/agentfleet.md, the work style picks its block, lane effort
follows the bundle, and agent-brain can be switched off."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
INSTALLER = ROOT / "bin" / "install.py"
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "scripts"))
import provider_catalog  # noqa: E402

_spec = importlib.util.spec_from_file_location("agentfleet_install_240", INSTALLER)
install = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(install)

OLD_POLICY = "<!-- claude-agents-config:managed -->\n\n# Global System & Delegation Manual\n\nDelegate substantive work.\n"


@unittest.skipIf(shutil.which("node") is None, "Node.js is required for the agent generator")
class Install240Tests(unittest.TestCase):
    def setUp(self):
        self.raw = tempfile.TemporaryDirectory(prefix="agentfleet 240 ")
        self.addCleanup(self.raw.cleanup)
        self.home = Path(self.raw.name) / "home"
        self.config = self.home / ".config"
        self.claude = self.home / ".claude"
        self.claude.mkdir(parents=True)
        self.fake_bin = Path(self.raw.name) / "fake-bin"
        self.fake_bin.mkdir()
        self.simulated_snapshots: set[str] = set()
        self.env = {
            "PATH": os.environ.get("PATH", ""), "HOME": str(self.home), "XDG_CONFIG_HOME": str(self.config),
            "PYTHONDONTWRITEBYTECODE": "1", "AGENTFLEET_MODS": "0", "AGENTFLEET_NONINTERACTIVE": "1",
            "AGENTFLEET_NO_CONFIGURE": "1",
        }
        # The real agent-brain on a developer's PATH must not decide these tests.
        self.env["PATH"] = os.pathsep.join(
            part for part in self.env["PATH"].split(os.pathsep)
            if part and not (Path(part) / "agent-brain").exists() and not (Path(part) / "agent-brain.exe").exists()
        )
        if os.name == "nt":
            self.env.update({name: os.environ[name] for name in ("SYSTEMROOT", "COMSPEC", "PATHEXT", "TEMP", "TMP") if name in os.environ})
            self.env["USERPROFILE"] = str(self.home)

    # -- helpers ----------------------------------------------------------------
    def run_installer(self, *options: str):
        command = [PYTHON, "-B", str(INSTALLER), *options, "--home", str(self.home), "--config-home", str(self.config),
                   "--prefix", str(self.home / ".local")]
        return subprocess.run(command, cwd=ROOT, env=self.env, text=True, capture_output=True, stdin=subprocess.DEVNULL)

    def ok(self, result):
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return result

    def install_native(self):
        return self.ok(self.run_installer("--apply", "--no-wizard", "--provider", "native"))

    def update(self):
        return self.ok(self.run_installer("--apply", "--no-wizard"))

    def doctor(self):
        command = [PYTHON, "-B", str(ROOT / "bin" / "claude-agents-doctor"), "--check", "--home", str(self.home),
                   "--config-home", str(self.config), "--prefix", str(self.home / ".local"), "--bundle", str(ROOT)]
        return self.ok(subprocess.run(command, cwd=ROOT, env=self.env, text=True, capture_output=True))

    @property
    def policy(self) -> Path:
        return self.claude / "CLAUDE.md"

    @property
    def rules(self) -> Path:
        return self.claude / "rules" / "agentfleet.md"

    @property
    def metadata_path(self) -> Path:
        return self.claude / ".claude-agents-config-install.json"

    def as_231_with_managed_policy(self, content: str = OLD_POLICY, recorded: str | None = None):
        """Rewrite the installed state as 2.3.x left it: AgentFleet owned
        ~/.claude/CLAUDE.md, recorded with the checksum of the copy it wrote."""
        self.policy.write_text(content, encoding="utf-8")
        self.policy.chmod(0o644)
        meta = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        meta["version"] = "2.3.1"
        meta["managed"] = [item for item in meta["managed"] if item.get("id") != "home:.claude/CLAUDE.md"]
        meta["managed"].append({
            "id": "home:.claude/CLAUDE.md", "path": str(self.policy),
            "sha256": hashlib.sha256((recorded or content).encode("utf-8")).hexdigest(), "mode": "0o644",
        })
        self.metadata_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    def first_install_snapshot(self, original: bytes | None, name: str = "20260101T000000Z"):
        """The backup a first install over a personal CLAUDE.md took before
        replacing it (pre-2.4.0 installs needed --force-owned for that).
        None records an install that found no CLAUDE.md."""
        if original is None:
            self.policy.unlink(missing_ok=True)
        else:
            self.policy.write_bytes(original)
        root = self.claude / "backups" / "claude-agents-config" / name
        root.parent.mkdir(parents=True, exist_ok=True)
        install.snapshot_paths([self.policy, self.metadata_path], root, "apply")
        self.simulated_snapshots.add(name)

    def keep_only_simulated_snapshots(self):
        """The test's own 2.4.0 install stands in for a 2.3.x one, whose first
        snapshot is the simulated one, so drop the snapshots it really took."""
        for backup in (self.claude / "backups" / "claude-agents-config").iterdir():
            if backup.name not in self.simulated_snapshots:
                shutil.rmtree(backup)

    def agent_brain_on_path(self):
        script = self.fake_bin / ("agent-brain.cmd" if os.name == "nt" else "agent-brain")
        script.write_text("@exit /b 0\n" if os.name == "nt" else "#!/bin/sh\nexit 0\n", encoding="utf-8")
        script.chmod(0o755)
        self.env["PATH"] = str(self.fake_bin) + os.pathsep + self.env["PATH"]

    def hook_commands(self) -> list[str]:
        settings = json.loads((self.claude / "settings.json").read_text(encoding="utf-8"))
        return [hook.get("command", "") for groups in (settings.get("hooks") or {}).values() for group in groups
                for hook in group.get("hooks", []) if isinstance(hook, dict)]

    # -- fresh install ------------------------------------------------------------
    def test_fresh_install_leaves_personal_claude_md_and_installs_rules(self):
        original = b"My own rules\nAlways answer in French.\n"
        self.policy.write_bytes(original)
        self.install_native()
        self.assertEqual(self.policy.read_bytes(), original)
        template = (ROOT / "config" / "agentfleet.md").read_text(encoding="utf-8")
        self.assertEqual(self.rules.read_text(encoding="utf-8"), provider_catalog.render_policy(template, "balanced"))
        self.assertNotIn("work-style", self.rules.read_text(encoding="utf-8"))
        self.assertEqual((self.claude / "agentfleet" / "policy-template.md").read_text(encoding="utf-8"), template)
        if os.name == "posix":
            self.assertEqual(self.rules.stat().st_mode & 0o777, 0o644)
        meta = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        self.assertNotIn("home:.claude/CLAUDE.md", {item["id"] for item in meta["managed"]})
        self.doctor()

    def test_work_style_preference_picks_the_rendered_block(self):
        provider_catalog.write_preferences(self.home, {"workStyle": "direct"})
        self.install_native()
        rendered = self.rules.read_text(encoding="utf-8")
        self.assertIn("Do all work yourself.", rendered)
        self.assertNotIn("Work directly by default", rendered)
        self.doctor()

    # -- upgrade from 2.3.x -------------------------------------------------------
    def test_upgrade_restores_the_original_an_old_install_replaced(self):
        original = b"My original instructions\n"
        self.first_install_snapshot(original)
        self.policy.unlink()
        self.install_native()
        self.keep_only_simulated_snapshots()
        self.as_231_with_managed_policy()
        result = self.update()
        self.assertEqual(self.policy.read_bytes(), original)
        self.assertIn("Restored your original", result.stdout)
        meta = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        self.assertNotIn("home:.claude/CLAUDE.md", {item["id"] for item in meta["managed"]})
        self.doctor()
        # The restored file is the user's now: later updates leave it alone.
        self.update()
        self.assertEqual(self.policy.read_bytes(), original)

    def test_upgrade_restores_the_original_of_the_current_install_only(self):
        # An install over A, uninstalled, then a reinstall over B: B is the
        # file the current install replaced.
        self.first_install_snapshot(b"Instructions A\n", "20260101T000000Z")
        self.first_install_snapshot(b"Instructions B\n", "20260201T000000Z")
        self.policy.unlink()
        self.install_native()
        self.keep_only_simulated_snapshots()
        self.as_231_with_managed_policy()
        self.update()
        self.assertEqual(self.policy.read_bytes(), b"Instructions B\n")

    def test_upgrade_never_restores_an_original_from_an_earlier_install(self):
        # The reinstall found no CLAUDE.md, so A from the earlier install must
        # not come back.
        self.first_install_snapshot(b"Instructions A\n", "20260101T000000Z")
        self.first_install_snapshot(None, "20260201T000000Z")
        self.install_native()
        self.keep_only_simulated_snapshots()
        self.as_231_with_managed_policy()
        self.update()
        self.assertFalse(self.policy.exists())

    def test_upgrade_removes_an_unedited_old_policy_without_an_original(self):
        self.install_native()
        self.as_231_with_managed_policy()
        self.update()
        self.assertFalse(self.policy.exists())
        self.assertTrue(self.rules.is_file())
        self.doctor()

    def test_upgrade_keeps_a_user_edited_old_policy(self):
        self.install_native()
        edited = OLD_POLICY + "My addition.\n"
        self.as_231_with_managed_policy(edited, recorded=OLD_POLICY)
        result = self.update()
        self.assertEqual(self.policy.read_text(encoding="utf-8"), edited)
        self.assertIn("agentfleet instructions", result.stdout)
        self.doctor()
        self.update()
        self.assertEqual(self.policy.read_text(encoding="utf-8"), edited)

    # -- lane effort ----------------------------------------------------------------
    def test_lane_effort_follows_the_bundle_on_update(self):
        self.install_native()
        fleet_path = self.claude / "fleet.json"
        fleet = json.loads(fleet_path.read_text(encoding="utf-8"))
        fleet["lanes"]["implement"]["effort"] = "xhigh"
        fleet_path.write_text(json.dumps(fleet, indent=2) + "\n", encoding="utf-8")
        self.update()
        bundle = json.loads((ROOT / "config" / "delegate-fleet.json").read_text(encoding="utf-8"))
        installed = json.loads(fleet_path.read_text(encoding="utf-8"))
        self.assertEqual(bundle["lanes"]["implement"]["effort"], "high")
        self.assertEqual(installed["lanes"]["implement"]["effort"], "high")
        self.assertIn("\neffort: high\n", (self.claude / "agents" / "fleet-implement.md").read_text(encoding="utf-8"))
        self.assertIn("\neffort: medium\n", (self.claude / "agents" / "fleet-tests.md").read_text(encoding="utf-8"))
        self.assertIn("\neffort: xhigh\n", (self.claude / "agents" / "fleet-plan.md").read_text(encoding="utf-8"))

    # -- agent-brain ------------------------------------------------------------------
    def test_agent_brain_off_removes_its_hooks_and_agent_memory_lines(self):
        self.agent_brain_on_path()
        self.install_native()
        self.assertTrue(any("claude-agents-config:agent-brain" in command for command in self.hook_commands()))
        lane = (self.claude / "agents" / "fleet-implement.md").read_text(encoding="utf-8")
        # Lanes search memory but leave saving it to the main agent.
        self.assertIn("mcp__agent-brain-memory__memory_search", lane)
        self.assertNotIn("memory_save", lane)
        self.assertNotIn("session summary", lane)
        # agent-brain's tool-use checkpoint also reaches lanes; they skip it.
        self.assertIn("Do not save memory, even when a hook asks you to", lane)
        provider_catalog.write_preferences(self.home, {"agentBrain": False})
        self.update()
        self.assertFalse(any("claude-agents-config:agent-brain" in command for command in self.hook_commands()))
        agent = (self.claude / "agents" / "fleet-implement.md").read_text(encoding="utf-8")
        self.assertNotIn("agent-brain", agent)
        fleet = json.loads((self.claude / "fleet.json").read_text(encoding="utf-8"))
        self.assertIs(fleet["integrations"]["agentBrain"], False)
        self.doctor()

    def test_installer_and_generator_render_the_same_lane_memory_text(self):
        # The installer renders lanes itself when Node is missing; both copies
        # must say the same thing.
        pattern = r'MEMORY_LIFECYCLE = "([^"]*)"'
        generator = re.search(pattern, (ROOT / "scripts" / "generate-claude-agents.mjs").read_text(encoding="utf-8"))
        installer = re.search(pattern, (ROOT / "bin" / "install.py").read_text(encoding="utf-8"))
        self.assertIsNotNone(generator)
        self.assertIsNotNone(installer)
        self.assertEqual(generator.group(1), installer.group(1))

    # -- uninstall --------------------------------------------------------------------
    def test_uninstall_removes_the_rules_and_keeps_the_personal_file(self):
        original = b"Mine\n"
        self.policy.write_bytes(original)
        self.install_native()
        self.ok(self.run_installer("--uninstall", "--apply"))
        self.assertFalse(self.rules.exists())
        self.assertFalse((self.claude / "agentfleet" / "policy-template.md").exists())
        self.assertEqual(self.policy.read_bytes(), original)


    def test_uninstall_removes_the_rules_after_a_work_style_change(self):
        self.install_native()
        before = self.rules.read_bytes()
        command = [PYTHON, "-B", str(ROOT / "bin" / "agentfleet"), "--home", str(self.home),
                   "--prefix", str(self.home / ".local"), "config", "work-style", "direct"]
        self.ok(subprocess.run(command, cwd=ROOT, env=self.env, text=True, capture_output=True))
        self.assertNotEqual(self.rules.read_bytes(), before)
        result = self.ok(self.run_installer("--uninstall", "--apply"))
        self.assertFalse(self.rules.exists(), result.stdout)
        self.assertFalse(self.metadata_path.exists(), result.stdout)

    def test_uninstall_keeps_a_rules_file_the_user_edited(self):
        self.install_native()
        self.rules.write_text(self.rules.read_text(encoding="utf-8") + "\nMy own line.\n", encoding="utf-8")
        self.ok(self.run_installer("--uninstall", "--apply"))
        self.assertTrue(self.rules.is_file())
        self.assertIn("My own line.", self.rules.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
