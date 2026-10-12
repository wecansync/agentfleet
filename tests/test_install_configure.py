"""Post-commit installer configuration, using real installs in isolated homes."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("install_configure_contract", ROOT / "bin/install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
HINT = "New AgentFleet options are available: run agentfleet configure"


class InstallConfigureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="agentfleet configure install ")
        self.addCleanup(self.tmp.cleanup)
        self.cwd = Path(self.tmp.name)
        self.home = self.cwd / "home"
        self.config = self.cwd / "config"
        self.env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.home),
                    "XDG_CONFIG_HOME": str(self.config), "PYTHONDONTWRITEBYTECODE": "1",
                    "AGENTFLEET_MODS": "0", "AGENTFLEET_NONINTERACTIVE": "1"}

    def run_install(self, *args, **env):
        result = subprocess.run([sys.executable, "-B", str(ROOT / "bin/install.py"),
                                 "--home", str(self.home), "--config-home", str(self.config), *args],
                                cwd=self.cwd, env={**self.env, **env}, text=True,
                                capture_output=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def preset(self):
        path = self.cwd / "team.json"
        path.write_text(json.dumps({"format": "agentfleet.preset.v1", "compact": 72}))
        return path

    def assert_applied(self):
        state = json.loads((self.home / ".claude/agentfleet/wizard.json").read_text())
        self.assertIn("compact", state["asked"])
        prefs = json.loads((self.home / ".claude/agentfleet/config.json").read_text())
        self.assertEqual(prefs["compactPercent"], 72)

    def test_applied_install_and_update_run_after_install_without_tty(self):
        self.assertIn(HINT, self.run_install().stdout)
        self.assertIn(HINT, self.run_install().stdout)

    def test_environment_and_cli_opt_out(self):
        for args, env in (([], {"AGENTFLEET_NO_CONFIGURE": "1"}), (["--no-configure"], {}), (["--no-wizard"], {})):
            with self.subTest(args=args, env=env):
                result = self.run_install(*args, **env)
                self.assertNotIn(HINT, result.stdout)
                self.assertFalse((self.home / ".claude/agentfleet/wizard.json").exists())

    def test_relative_environment_preset_applies(self):
        self.preset()
        self.run_install(AGENTFLEET_PRESET="team.json")
        self.assert_applied()

    def test_json_arguments_override_environment_preset(self):
        path = self.preset()
        self.run_install(AGENTFLEET_CONFIGURE_ARGS=json.dumps(["--preset", str(path)]),
                         AGENTFLEET_PRESET="missing.json")
        self.assert_applied()

    def test_invalid_json_warns_and_preserves_install_success(self):
        for value in ("{", "{}", '[1]', '"--yes"', 'null'):
            with self.subTest(value=value):
                result = self.run_install(AGENTFLEET_CONFIGURE_ARGS=value)
                self.assertIn("Warning: invalid AGENTFLEET_CONFIGURE_ARGS", result.stderr)
                self.assertNotIn(HINT, result.stdout)
                self.assertFalse((self.home / ".claude/agentfleet/wizard.json").exists())

    def test_configure_failure_does_not_roll_back(self):
        result = self.run_install(AGENTFLEET_PRESET="missing.json")
        self.assertIn("AgentFleet is installed; finish setup later with: agentfleet configure", result.stdout)
        self.assertTrue((self.home / ".claude/agentfleet.py").is_file())
        self.assertTrue((self.home / ".claude/.claude-agents-config-install.json").is_file())

    def test_dry_run_uninstall_and_rollback_skip_configuration(self):
        # A deliberate invalid configure argument exposes even noninteractive invocation.
        env = {"AGENTFLEET_CONFIGURE_ARGS": '["--not-a-configure-option"]'}
        result = self.run_install("--dry-run", **env)
        self.assertNotIn("finish setup later", result.stdout)
        self.run_install(AGENTFLEET_NO_CONFIGURE="1")
        for args in (("--rollback", "--apply"), ("--uninstall", "--apply")):
            result = self.run_install(*args, **env)
            self.assertNotIn("finish setup later", result.stdout)
            self.assertNotIn("unrecognized arguments", result.stderr)

    def test_configure_command_inherits_stdio_and_runs_after_apply_returns(self):
        events = []
        def apply(*args, **kwargs):
            events.append("committed and unlocked")
            return 0
        def run(command):
            events.append(command)
            return subprocess.CompletedProcess(command, 0)
        with patch.dict(os.environ, self.env, clear=True), patch.object(sys, "argv", ["install.py", "--home", str(self.home), "--config-home", str(self.config)]), patch.object(installer, "apply_install", side_effect=apply), patch.object(installer.subprocess, "run", side_effect=run):
            self.assertEqual(installer.main(), 0)
        self.assertEqual(events, ["committed and unlocked", [sys.executable, str(self.home / ".claude/agentfleet.py"), "--home", str(self.home), "--config-home", str(self.config), "configure", "--after-install"]])


if __name__ == "__main__":
    unittest.main()
