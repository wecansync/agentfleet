#!/usr/bin/env python3
"""Focused preference, compaction, plugin, and status-marker tests."""
from __future__ import annotations

import importlib.util
import importlib.machinery
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
AGENTFLEET = ROOT / "bin" / "agentfleet"
SYNC_CONTEXT = ROOT / "scripts" / "sync-model-context.py"
SCRIPTS = ROOT / "scripts"
sys.dont_write_bytecode = True
sys.path.insert(0, str(SCRIPTS))
import provider_catalog


def import_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class PreferencesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="agentfleet preferences ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.config = self.root / "config"
        (self.home / ".claude" / "cache").mkdir(parents=True)
        (self.config / "delegate-skills").mkdir(parents=True)
        (self.config / "delegate-skills" / "provider-policy.json").write_text(
            json.dumps(provider_catalog.generic_policy()), encoding="utf-8",
        )
        self.endpoint = "https://gateway.example.test/api"
        self.token = "test-token-never-printed"
        self.cache_path = self.home / ".claude" / "cache" / "provider-models-cache.json"
        self.settings_path = self.home / ".claude" / "settings.json"
        self.env = dict(os.environ)
        self.env.update({
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.config),
            "PYTHONDONTWRITEBYTECODE": "1",
        })

    def gateway(self, model: str = "model-1m[1m]") -> None:
        settings = {
            "model": model,
            "autoCompactWindow": 700_000,
            "env": {
                "ANTHROPIC_BASE_URL": self.endpoint,
                "ANTHROPIC_AUTH_TOKEN": self.token,
                "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "700000",
                "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "700000",
            },
        }
        self.settings_path.write_text(json.dumps(settings), encoding="utf-8")
        payload = provider_catalog.cache_payload([
            {"id": "model-1m", "context_length": 1_000_000},
            {"id": "model-200k", "context_length": 200_000},
        ], self.endpoint, self.token, fetched_at=int(time.time()))
        self.cache_path.write_text(json.dumps(payload), encoding="utf-8")

    def run_sync_hook(self, model: str, event: str = "PostModelSwitch") -> subprocess.CompletedProcess:
        return subprocess.run(
            [PYTHON, str(SYNC_CONTEXT), "--home", str(self.home), "--config-home", str(self.config)],
            input=json.dumps({"hook_event_name": event, "to_model": model}),
            text=True, capture_output=True, env=self.env, cwd=ROOT,
        )

    def run_agentfleet(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [PYTHON, str(AGENTFLEET), "--home", str(self.home), "--config-home", str(self.config), *args],
            text=True, capture_output=True, env=env or self.env, cwd=ROOT,
        )

    def set_percent(self, percent: int | str) -> None:
        provider_catalog.write_preferences(self.home, {"compactPercent": percent})

    def test_sync_model_context_hook_uses_gateway_percent_for_1m_and_200k_models(self):
        self.gateway()
        result = self.run_sync_hook("model-1m[1m]")
        self.assertEqual(result.returncode, 0, result.stderr)
        current = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(current["autoCompactWindow"], 1_000_000)
        self.assertEqual(current["env"]["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "1000000")
        self.assertEqual(current["env"]["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "1000000")
        self.assertIn("Auto-compact set to 70% of each model's context. Restart Claude Code to apply.", result.stdout)

        self.set_percent(55)
        result = self.run_sync_hook("model-1m[1m]")
        self.assertEqual(result.returncode, 0, result.stderr)
        current = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(current["autoCompactWindow"], 1_000_000)
        self.assertEqual(current["env"]["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "1000000")

        result = self.run_sync_hook("model-200k")
        self.assertEqual(result.returncode, 0, result.stderr)
        current = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(current["autoCompactWindow"], 1_000_000)
        self.assertEqual(current["env"]["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "1000000")
        self.assertEqual(current["env"]["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "200000")

        self.set_percent(70)
        result = self.run_sync_hook("model-200k")
        self.assertEqual(result.returncode, 0, result.stderr)
        current = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(current["autoCompactWindow"], 1_000_000)

        self.set_percent("off")
        previous = self.settings_path.read_bytes()
        result = self.run_sync_hook("model-1m[1m]")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.settings_path.read_bytes(), previous)
        result = self.run_sync_hook("model-200k")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.settings_path.read_bytes(), previous)

    def test_sync_model_context_native_basis_does_not_add_max_context(self):
        self.settings_path.write_text(json.dumps({"model": "sonnet", "env": {}}), encoding="utf-8")
        self.set_percent(55)
        result = self.run_sync_hook("sonnet", event="PreModelSwitch")
        self.assertEqual(result.returncode, 0, result.stderr)
        current = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(current["autoCompactWindow"], 1_000_000)
        self.assertEqual(current["env"]["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "1000000")
        self.assertNotIn("CLAUDE_CODE_MAX_CONTEXT_TOKENS", current["env"])
        self.assertIn("Auto-compact set to 55% of each model's context.", result.stdout)
        # A second switch on the same settings changes nothing and says nothing.
        self.assertEqual(self.run_sync_hook("haiku", event="PostModelSwitch").stdout, "")

    def test_reconcile_context_uses_gateway_and_native_percentage_and_off(self):
        reconcile = import_script("preferences_fleet_reconcile", SCRIPTS / "fleet-reconcile.py")
        self.set_percent(70)
        gateway_settings = {
            "model": "model-1m[1m]",
            "env": {"ANTHROPIC_BASE_URL": self.endpoint},
        }
        updated, notice, window = reconcile.reconcile_context(
            gateway_settings,
            {"model-1m[1m]": {"context_length": 1_000_000}},
            self.home,
        )
        self.assertEqual(window, 1_000_000)
        self.assertIn("70% of each model's context", notice)
        self.assertEqual(updated["env"]["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "1000000")

        self.set_percent(55)
        smaller, _, window = reconcile.reconcile_context(
            {"model": "model-200k", "env": {"ANTHROPIC_BASE_URL": self.endpoint}},
            {"model-200k": {"context_length": 200_000}}, self.home,
        )
        self.assertEqual(window, 1_000_000)
        self.assertEqual(smaller["autoCompactWindow"], 1_000_000)

        native, _, window = reconcile.reconcile_context({"model": "sonnet", "env": {}}, {}, self.home)
        self.assertEqual(window, 1_000_000)
        self.assertEqual(native["env"]["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "1000000")
        self.assertNotIn("CLAUDE_CODE_MAX_CONTEXT_TOKENS", native["env"])

        self.set_percent("off")
        unchanged = {"model": "model-1m[1m]", "env": {"ANTHROPIC_BASE_URL": self.endpoint}}
        result, notice, window = reconcile.reconcile_context(
            unchanged, {"model-1m[1m]": {"context_length": 1_000_000}}, self.home,
        )
        self.assertIsNone(window)
        self.assertIsNone(notice)
        self.assertEqual(result, unchanged)

    def test_agentfleet_config_show_set_off_and_invalid(self):
        self.gateway()
        shown = self.run_agentfleet("config")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn("compact: 70% of each model's context (default)", shown.stdout)
        self.assertIn("reviewer-access: full (default)", shown.stdout)
        self.assertIn("learn: on (default)", shown.stdout)
        self.assertIn("jev: ", shown.stdout)
        self.assertNotIn("auto-compact window", shown.stdout)

        changed = self.run_agentfleet("config", "compact", "55")
        self.assertEqual(changed.returncode, 0, changed.stderr)
        self.assertIn("55%", changed.stdout)
        current = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(current["autoCompactWindow"], 1_000_000)
        self.assertEqual(current["env"]["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "1000000")
        self.assertEqual(current["env"][provider_catalog.COMPACT_PERCENT_ENV], "55")

        learned = self.run_agentfleet("config", "learn", "on")
        self.assertEqual(learned.returncode, 0, learned.stderr)
        self.assertIn("never prompt text", learned.stdout)
        self.assertIn("agentfleet suggest --forget", learned.stdout)

        invalid = self.run_agentfleet("config", "compact", "19")
        self.assertEqual(invalid.returncode, 2)
        self.assertIn("20-95", invalid.stderr)
        invalid_reviewer = self.run_agentfleet("config", "reviewer-access", "unsafe")
        self.assertEqual(invalid_reviewer.returncode, 2)
        self.assertIn("reviewerAccess", invalid_reviewer.stderr)

        off = self.run_agentfleet("config", "compact", "off")
        self.assertEqual(off.returncode, 0, off.stderr)
        self.assertIn("no longer manages auto-compact", off.stdout)
        self.assertNotIn(provider_catalog.COMPACT_PERCENT_ENV, json.loads(self.settings_path.read_text(encoding="utf-8"))["env"])
        self.assertEqual(json.loads(self.settings_path.read_text(encoding="utf-8"))["autoCompactWindow"], 1_000_000)

    def test_agentfleet_jev_uses_safe_cli_without_token_environment(self):
        fake_bin = self.root / "fake-bin"
        fake_bin.mkdir()
        fake_log = self.root / "fake-claude.log"
        fake = fake_bin / "claude"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            f"log = {str(fake_log)!r}\n"
            "with open(log, 'a', encoding='utf-8') as stream:\n"
            "    stream.write(json.dumps({'argv': sys.argv[1:], 'credentials': [name for name in ('ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_API_KEY', 'TYPESAFE_API_KEY') if name in os.environ]}) + '\\n')\n"
            "if sys.argv[1:] == ['plugin', 'list', '--json']:\n"
            "    print(json.dumps([{'id': 'fast-jev-compaction@agentfleet', 'enabled': True}]))\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        if os.name == "nt":
            # PATH lookup on Windows finds claude.CMD, not the extensionless script.
            (fake_bin / "claude.cmd").write_text(f'@"{PYTHON}" "%~dp0claude" %*\n', encoding="utf-8")
        metadata = self.home / ".claude" / ".claude-agents-config-install.json"
        metadata.write_text(json.dumps({"modsMarketplace": True}), encoding="utf-8")
        env = dict(self.env)
        env.update({
            "PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", ""),
            "FAKE_CLAUDE_LOG": str(fake_log),
            "ANTHROPIC_AUTH_TOKEN": "gateway-token-not-forwarded",
            "ANTHROPIC_API_KEY": "api-key-not-forwarded",
            "TYPESAFE_API_KEY": "typesafe-key-not-forwarded",
        })
        enabled = self.run_agentfleet("jev", "on", env=env)
        self.assertEqual(enabled.returncode, 0, enabled.stderr)
        self.assertIn("saved in ~/.claude/settings.json when you enter it in the configure wizard", enabled.stdout)
        self.assertNotIn("typesafe-key-not-forwarded", enabled.stdout)
        logged = [json.loads(line) for line in fake_log.read_text(encoding="utf-8").splitlines()]
        # A listed plugin is enabled (install would not turn a disabled one back on); an unlisted one is installed.
        self.assertEqual(logged[0]["argv"], ["plugin", "list", "--json"])
        self.assertIn(logged[1]["argv"], (["plugin", "enable", "fast-jev-compaction@agentfleet"],
                                          ["plugin", "install", "fast-jev-compaction@agentfleet"]))
        self.assertTrue(all(entry["credentials"] == [] for entry in logged), "credential variables must not reach the fake Claude CLI")

        status = self.run_agentfleet("jev", "status", env=env)
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn("fast-jev-compaction: installed, enabled", status.stdout)
        self.assertIn("TypeSafe API key: set", status.stdout)
        self.assertNotIn("typesafe-key-not-forwarded", status.stdout + status.stderr)
        env["TYPESAFE_API_KEY"] = ""
        missing_value = self.run_agentfleet("jev", "status", env=env)
        self.assertEqual(missing_value.returncode, 0, missing_value.stderr)
        self.assertIn("TypeSafe API key: not set", missing_value.stdout)

    def test_auto_update_hook_self_heals_active_and_enabled_before_reporting(self):
        self.settings_path.write_text(json.dumps({"model": "default", "env": {}}), encoding="utf-8")
        store = self.home / ".claude" / "agentfleet"
        store.mkdir(parents=True)
        state_path = store / "auto-update.json"
        state_path.write_text(json.dumps({"lastResult": {"version": "2.2.0", "ok": True}, "reported": False}), encoding="utf-8")
        result = self.run_agentfleet("auto-update", "--hook")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((store / "active").is_file())
        self.assertEqual((store / "active").read_text(encoding="utf-8").strip(), "native")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertIs(state["enabled"], True)
        self.assertIs(state["reported"], True)
        self.assertIn("AgentFleet was updated", result.stdout)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE((store / "active").stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(state_path.stat().st_mode), 0o600)

    def test_profiles_command_self_heals_missing_status_markers(self):
        self.settings_path.write_text(json.dumps({"model": "default", "env": {}}), encoding="utf-8")
        result = self.run_agentfleet("profiles")
        self.assertEqual(result.returncode, 0, result.stderr)
        store = self.home / ".claude" / "agentfleet"
        self.assertEqual((store / "active").read_text(encoding="utf-8").strip(), "native")
        state = json.loads((store / "auto-update.json").read_text(encoding="utf-8"))
        self.assertIs(state["enabled"], True)

    def test_native_switch_with_compact_off_preserves_existing_window(self):
        self.gateway()
        settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
        settings["autoCompactWindow"] = 610_000
        settings["env"]["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = "610000"
        self.settings_path.write_text(json.dumps(settings), encoding="utf-8")
        self.set_percent("off")
        loader = importlib.machinery.SourceFileLoader("preferences_agentfleet_module", str(AGENTFLEET))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        cli = importlib.util.module_from_spec(spec)
        loader.exec_module(cli)
        import argparse
        paths = cli.Paths(argparse.Namespace(home=str(self.home), config_home=str(self.config), prefix=None))
        fleet = {"lanes": {"implement": {"model": "model-1m[1m]", "tier": "balanced"}}}
        target = cli.native_profile(fleet, paths, settings)
        self.assertEqual(target["settings"]["autoCompactWindow"], 610_000)
        self.assertEqual(target["env"]["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "610000")
        self.assertNotIn("CLAUDE_CODE_MAX_CONTEXT_TOKENS", target["env"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
