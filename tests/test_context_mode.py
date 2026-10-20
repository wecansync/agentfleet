#!/usr/bin/env python3
"""Hermetic tests for AgentFleet's opt-in upstream Context Mode command."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
AGENTFLEET = ROOT / "bin" / "agentfleet"
PLUGIN_ID = "context-mode@context-mode"
MARKETPLACE_SOURCE = "mksglu/context-mode"
FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

claude = Path.home() / ".claude"
state_path = claude / "test-context-mode-state.json"
log_path = claude / "test-context-mode-calls.jsonl"
state = json.loads(state_path.read_text()) if state_path.exists() else {"marketplaces": [], "plugins": []}
argv = sys.argv[1:]
with log_path.open("a", encoding="utf-8") as log:
    log.write(json.dumps({"argv": argv, "env": sorted(os.environ), "credentials": {
        name: os.environ.get(name) for name in (
            "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "TYPESAFE_API_KEY", "OPENAI_API_KEY", "CONTEXT_MODE_SECRET"
        ) if name in os.environ
    }}) + "\n")

if state.get("invalid_marketplace_list") and argv == ["plugin", "marketplace", "list", "--json"]:
    print("plugin output containing private data")
    sys.exit(0)
if argv == ["plugin", "marketplace", "list", "--json"]:
    print(json.dumps(state.get("marketplaces", [])))
elif argv == ["plugin", "marketplace", "add", "mksglu/context-mode"]:
    state.setdefault("marketplaces", []).append({
        "name": "context-mode",
        "source": {"source": "github", "repo": "mksglu/context-mode"},
    })
    state_path.write_text(json.dumps(state))
elif argv == ["plugin", "install", "context-mode@context-mode", "--scope", "user"]:
    state.setdefault("plugins", []).append({"id": "context-mode@context-mode", "version": "1.0.0"})
    installed_path = claude / "plugins" / "installed_plugins.json"
    installed_path.parent.mkdir(parents=True, exist_ok=True)
    installed = json.loads(installed_path.read_text()) if installed_path.exists() else {"plugins": {}}
    installed.setdefault("plugins", {})["context-mode@context-mode"] = [{"scope": "user", "version": "1.0.0"}]
    installed_path.write_text(json.dumps(installed))
    settings_path = claude / "settings.json"
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    settings.setdefault("enabledPlugins", {})["context-mode@context-mode"] = True
    settings_path.write_text(json.dumps(settings))
elif argv in (["plugin", "enable", "context-mode@context-mode", "--scope", "user"], ["plugin", "disable", "context-mode@context-mode", "--scope", "user"]):
    settings_path = claude / "settings.json"
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    wanted = argv[1] == "enable"
    # Like the real CLI: asking for the state the plugin is already in fails.
    if settings.get("enabledPlugins", {}).get("context-mode@context-mode") is wanted:
        print(f"Plugin context-mode@context-mode is already {argv[1]}d at user scope", file=sys.stderr)
        sys.exit(1)
    settings.setdefault("enabledPlugins", {})["context-mode@context-mode"] = wanted
    settings_path.write_text(json.dumps(settings))
else:
    sys.exit(2)
'''


def write_fake_claude(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "claude"
    script.write_text(FAKE_CLAUDE, encoding="utf-8")
    if os.name == "nt":
        (directory / "claude.cmd").write_text(f'@"{PYTHON}" "%~dp0claude" %*\n', encoding="utf-8")
    else:
        script.chmod(0o755)
    return script


class ContextModeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="agentfleet-context-mode-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.config = self.root / "config"
        self.claude_dir = self.home / ".claude"
        self.claude_dir.mkdir(parents=True)
        self.fake_bin = self.root / "fake-bin"
        write_fake_claude(self.fake_bin)
        self.env = {
            "PATH": str(self.fake_bin) + os.pathsep + os.defpath,
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "XDG_CONFIG_HOME": str(self.config),
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "ANTHROPIC_AUTH_TOKEN": "context-mode-test-auth-secret",
            "ANTHROPIC_API_KEY": "context-mode-test-api-secret",
            "TYPESAFE_API_KEY": "context-mode-test-typesafe-secret",
            "OPENAI_API_KEY": "context-mode-test-openai-secret",
            "CONTEXT_MODE_SECRET": "context-mode-test-private-secret",
        }
        self.state_path = self.claude_dir / "test-context-mode-state.json"
        self.calls_path = self.claude_dir / "test-context-mode-calls.jsonl"
        self.installed_path = self.claude_dir / "plugins" / "installed_plugins.json"
        self.settings_path = self.claude_dir / "settings.json"
        self.node_bin = self.root / "node-bin"
        self.node_bin.mkdir()
        node = self.node_bin / "node"
        node.write_text("#!/bin/sh\nprintf 'v26.7.0\\n'\n", encoding="utf-8")
        node.chmod(0o755)
        self.env["PATH"] = str(self.fake_bin) + os.pathsep + str(self.node_bin) + os.pathsep + os.defpath

    def run_agentfleet(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [PYTHON, str(AGENTFLEET), "--home", str(self.home), "--config-home", str(self.config), *args],
            text=True, capture_output=True, env=env or self.env, cwd=ROOT, timeout=20,
        )

    def state(self) -> dict:
        return json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.exists() else {"marketplaces": [], "plugins": []}

    def calls(self) -> list[dict]:
        if not self.calls_path.exists():
            return []
        return [json.loads(line) for line in self.calls_path.read_text(encoding="utf-8").splitlines()]

    def set_installed(self, enabled: bool | None, pinned_node: Path | None = None) -> None:
        self.state_path.write_text(json.dumps({"marketplaces": [{
            "name": "context-mode", "source": {"source": "github", "repo": MARKETPLACE_SOURCE},
        }], "plugins": []}), encoding="utf-8")
        record = {"scope": "user", "version": "1.0.0"}
        if pinned_node is not None:
            # The installed copy pins the Node that first ran it.
            install_path = self.claude_dir / "plugins" / "cache" / "context-mode" / "context-mode" / "1.0.0"
            (install_path / ".claude-plugin").mkdir(parents=True)
            (install_path / ".claude-plugin" / "plugin.json").write_text(json.dumps({
                "mcpServers": {"context-mode": {"command": str(pinned_node), "args": ["start.mjs"]}},
            }), encoding="utf-8")
            record["installPath"] = str(install_path)
        self.installed_path.parent.mkdir(parents=True, exist_ok=True)
        self.installed_path.write_text(json.dumps({"plugins": {PLUGIN_ID: [record]}}), encoding="utf-8")
        settings = {"enabledPlugins": {PLUGIN_ID: enabled}} if enabled is not None else {}
        self.settings_path.write_text(json.dumps(settings), encoding="utf-8")

    def test_on_adds_upstream_marketplace_then_installs_plugin(self):
        result = self.run_agentfleet("config", "context-mode", "on")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("/context-mode:ctx-doctor", result.stdout)
        self.assertIn("user scope", result.stdout)
        self.assertIn("Restart Claude Code", result.stdout)
        self.assertNotRegex(result.stdout + result.stderr, r"[0-9,]+%|[0-9,]+ tokens saved")
        self.assertNotIn("Warning:", result.stdout)  # the fake node on PATH is 26.7.0
        self.assertEqual([call["argv"] for call in self.calls()], [
            ["plugin", "marketplace", "list", "--json"],
            ["plugin", "marketplace", "add", MARKETPLACE_SOURCE],
            ["plugin", "marketplace", "list", "--json"],
            ["plugin", "install", PLUGIN_ID, "--scope", "user"],
        ])
        self.assertTrue(self.installed_path.is_file())
        settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertIs(settings["enabledPlugins"][PLUGIN_ID], True)
        self.assertTrue(all(call["credentials"] == {} for call in self.calls()))
        for secret in self.env.values():
            self.assertNotIn(secret, result.stdout + result.stderr)

    def test_on_enables_an_installed_disabled_plugin_without_reinstalling(self):
        self.set_installed(False)
        result = self.run_agentfleet("config", "context-mode", "on")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call["argv"] for call in self.calls()], [
            ["plugin", "marketplace", "list", "--json"],
            ["plugin", "enable", PLUGIN_ID, "--scope", "user"],
        ])
        settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertIs(settings["enabledPlugins"][PLUGIN_ID], True)

    def test_off_disables_but_keeps_installed_plugin_and_data(self):
        self.set_installed(True)
        before = self.installed_path.read_bytes()
        result = self.run_agentfleet("config", "context-mode", "off")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("disabled", result.stdout.lower())
        self.assertIn("Restart Claude Code", result.stdout)
        self.assertEqual([call["argv"] for call in self.calls()], [[
            "plugin", "disable", PLUGIN_ID, "--scope", "user",
        ]])
        self.assertEqual(self.installed_path.read_bytes(), before)
        self.assertIs(json.loads(self.settings_path.read_text(encoding="utf-8"))["enabledPlugins"][PLUGIN_ID], False)

    def test_on_when_already_enabled_succeeds_without_running_claude(self):
        self.set_installed(True)
        result = self.run_agentfleet("config", "context-mode", "on")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already enabled", result.stdout)
        self.assertEqual(self.calls(), [])

    def test_off_when_already_disabled_succeeds_without_running_claude(self):
        self.set_installed(False)
        result = self.run_agentfleet("config", "context-mode", "off")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already disabled", result.stdout)
        self.assertEqual(self.calls(), [])

    def test_off_when_not_installed_succeeds_without_running_claude(self):
        result = self.run_agentfleet("config", "context-mode", "off")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("not installed", result.stdout)
        self.assertEqual(self.calls(), [])

    def test_on_warns_about_the_pinned_node_not_the_one_on_path(self):
        gone = self.root / "old-node" / "node"
        self.set_installed(False, pinned_node=gone)
        result = self.run_agentfleet("config", "context-mode", "on")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"Warning: Context Mode runs Node.js from {gone}, which no longer exists", result.stdout)

    def test_on_warns_when_the_pinned_node_is_too_old(self):
        old = self.root / "node-20" / "node"
        old.parent.mkdir()
        old.write_text("#!/bin/sh\nprintf 'v20.11.1\\n'\n", encoding="utf-8")
        old.chmod(0o755)
        self.set_installed(False, pinned_node=old)
        result = self.run_agentfleet("config", "context-mode", "on")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"Context Mode needs Node.js 22.5 or later; {old} is 20.11.1", result.stdout)

    def test_node_warning_shows_even_when_enabling_fails(self):
        old = self.root / "node-18" / "node"
        old.parent.mkdir()
        old.write_text("#!/bin/sh\nprintf 'v18.19.1\\n'\n", encoding="utf-8")
        old.chmod(0o755)
        env = dict(self.env)
        env["PATH"] = str(self.fake_bin) + os.pathsep + str(old.parent) + os.pathsep + os.defpath
        self.state_path.write_text(json.dumps({"marketplaces": [{
            "name": "context-mode", "source": {"source": "github", "repo": "someone/else"},
        }], "plugins": []}), encoding="utf-8")
        result = self.run_agentfleet("config", "context-mode", "on", env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"Warning: Context Mode needs Node.js 22.5 or later; {old} is 18.19.1", result.stdout)

    def test_status_uses_settings_and_installed_metadata_without_running_claude(self):
        result = self.run_agentfleet("config", "context-mode", "status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("context-mode: not installed", result.stdout)
        self.assertEqual(self.calls(), [])

        self.set_installed(True)
        result = self.run_agentfleet("config", "context-mode", "status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("context-mode: installed, enabled", result.stdout)
        self.assertEqual(self.calls(), [])

        self.set_installed(False)
        result = self.run_agentfleet("config", "context-mode", "status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("context-mode: installed, disabled", result.stdout)
        self.assertEqual(self.calls(), [])

    def test_on_refuses_a_different_marketplace_with_the_same_name(self):
        self.state_path.write_text(json.dumps({"marketplaces": [{
            "name": "context-mode", "source": {"source": "github", "repo": "someone/else"},
        }], "plugins": []}), encoding="utf-8")
        result = self.run_agentfleet("config", "context-mode", "on")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("different", result.stderr.lower())
        self.assertNotIn("someone/else", result.stdout + result.stderr)
        self.assertEqual([call["argv"] for call in self.calls()], [["plugin", "marketplace", "list", "--json"]])

    def test_missing_claude_cli_is_reported_without_credentials(self):
        env = dict(self.env)
        env["PATH"] = os.defpath
        result = self.run_agentfleet("config", "context-mode", "on", env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Claude Code CLI", result.stderr)
        self.assertNotIn("context-mode-test", result.stdout + result.stderr)

    def test_invalid_action_is_rejected_without_running_claude(self):
        result = self.run_agentfleet("config", "context-mode", "maybe")
        self.assertEqual(result.returncode, 2)
        self.assertIn("on, off, or status", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_unsupported_marketplace_output_is_not_echoed(self):
        self.state_path.write_text(json.dumps({"marketplaces": [], "plugins": [], "invalid_marketplace_list": True}), encoding="utf-8")
        result = self.run_agentfleet("config", "context-mode", "on")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported", result.stderr.lower())
        self.assertNotIn("private data", result.stdout + result.stderr)
        self.assertEqual([call["argv"] for call in self.calls()], [["plugin", "marketplace", "list", "--json"]])


if __name__ == "__main__":
    unittest.main()
