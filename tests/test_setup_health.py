#!/usr/bin/env python3
"""Tests for claude-fleet-setup compaction percentage and health audit checks."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from provider_catalog import cache_payload, generic_policy, load_preferences, write_preferences  # noqa: E402


class SetupHealthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="setup-health-test-")
        self.home = Path(self.temp_dir)
        self.claude_dir = self.home / ".claude"
        self.claude_dir.mkdir(parents=True)
        self.cache_dir = self.claude_dir / "cache"
        self.cache_dir.mkdir(parents=True)
        self.plugins_dir = self.claude_dir / "plugins"
        self.plugins_dir.mkdir(parents=True)

        policy_dir = self.home / ".config" / "delegate-skills"
        policy_dir.mkdir(parents=True)
        (policy_dir / "provider-policy.json").write_text(json.dumps(generic_policy()))

        self.endpoint = "https://omniroute.example.com"
        self.token = "test-token"
        self.models_list = [
            {"id": "claude-opus-5", "display_name": "Opus 5", "context_length": 1000000},
            {"id": "claude-sonnet-5", "display_name": "Sonnet 5", "context_length": 1000000},
            {"id": "claude-haiku", "display_name": "Haiku", "context_length": 200000},
        ]
        self.catalog_data = cache_payload(self.models_list, self.endpoint, self.token)
        (self.cache_dir / "provider-models-cache.json").write_text(json.dumps(self.catalog_data, indent=2))

        self.settings_data = {
            "model": "claude-opus-5[1m]",
            "autoCompactWindow": 1000000,
            "env": {
                "ANTHROPIC_BASE_URL": self.endpoint,
                "ANTHROPIC_AUTH_TOKEN": self.token,
                "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "1000000",
                "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "70",
                "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "1000000",
            },
        }
        (self.claude_dir / "settings.json").write_text(json.dumps(self.settings_data, indent=2))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def run_fleet_setup(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        cmd = [sys.executable, "-B", str(ROOT / "bin" / "claude-fleet-setup"), "--home", str(self.home), *args]
        run_env = dict(os.environ if env is None else env)
        run_env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(cmd, capture_output=True, text=True, check=False, env=run_env)

    def test_compaction_audit_gateway_1m_at_70_and_55(self) -> None:
        # Default percent is 70; 1M model active
        proc = self.run_fleet_setup("--audit", "--json", "--no-health")
        self.assertEqual(proc.returncode, 0, f"Error: {proc.stderr}")
        data = json.loads(proc.stdout)
        comp = data["settings"]["compaction"]
        self.assertEqual(comp["window"], 1000000)
        self.assertEqual(comp["percent"], 70)
        self.assertEqual(comp["expected"], {"window": 1000000, "percent": 70})
        self.assertEqual(comp["window"], 1000000)
        self.assertTrue(comp["optimal"])

        # Change preference to 55
        write_preferences(self.home, {"compactPercent": 55})
        proc = self.run_fleet_setup("--audit", "--json", "--no-health")
        self.assertEqual(proc.returncode, 0, f"Error: {proc.stderr}")
        data = json.loads(proc.stdout)
        comp = data["settings"]["compaction"]
        self.assertEqual(comp["window"], 1000000)
        self.assertEqual(comp["preference"], 55)
        self.assertEqual(comp["expected"], {"window": 1000000, "percent": 55})
        # Current is 1000000, expected 550000 -> not optimal
        self.assertFalse(comp["optimal"])

        # Check text summary output
        proc_text = self.run_fleet_setup("--audit", "--no-health")
        self.assertEqual(proc_text.returncode, 0)
        self.assertIn("Auto-compact: 55% of each model", proc_text.stdout)

    def test_compaction_audit_gateway_200k_at_70_and_55(self) -> None:
        # Switch model to 200k (claude-haiku)
        self.settings_data["model"] = "claude-haiku"
        self.settings_data["autoCompactWindow"] = 1000000
        self.settings_data["env"]["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = "1000000"
        (self.claude_dir / "settings.json").write_text(json.dumps(self.settings_data, indent=2))

        # At 70%: 200_000 * 70 // 100 = 140_000
        write_preferences(self.home, {"compactPercent": 70})
        proc = self.run_fleet_setup("--audit", "--json", "--no-health")
        self.assertEqual(proc.returncode, 0, f"Error: {proc.stderr}")
        data = json.loads(proc.stdout)
        comp = data["settings"]["compaction"]
        self.assertEqual(comp["window"], 1000000)
        self.assertEqual(comp["percent"], 70)
        self.assertEqual(comp["expected"], {"window": 1000000, "percent": 70})
        self.assertTrue(comp["optimal"])

        # At 55%: 200_000 * 55 // 100 = 110_000
        write_preferences(self.home, {"compactPercent": 55})
        proc = self.run_fleet_setup("--audit", "--json", "--no-health")
        self.assertEqual(proc.returncode, 0, f"Error: {proc.stderr}")
        data = json.loads(proc.stdout)
        comp = data["settings"]["compaction"]
        self.assertEqual(comp["window"], 1000000)
        self.assertEqual(comp["preference"], 55)
        self.assertEqual(comp["expected"], {"window": 1000000, "percent": 55})
        self.assertFalse(comp["optimal"])

    def test_compaction_audit_native_basis(self) -> None:
        # Native profile carries no ANTHROPIC_BASE_URL
        native_settings = {
            "model": "claude-sonnet-4",
            "autoCompactWindow": 1000000,
            "env": {
                "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "1000000",
                "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "70",
            },
        }
        (self.claude_dir / "settings.json").write_text(json.dumps(native_settings, indent=2))
        write_preferences(self.home, {"compactPercent": 70})

        proc = self.run_fleet_setup("--audit", "--json", "--no-health")
        self.assertEqual(proc.returncode, 0, f"Error: {proc.stderr}")
        data = json.loads(proc.stdout)
        self.assertTrue(data["settings"]["native"])
        comp = data["settings"]["compaction"]
        self.assertEqual(comp["window"], 1000000)
        self.assertEqual(comp["percent"], 70)
        self.assertEqual(comp["expected"], {"window": 1000000, "percent": 70})
        self.assertTrue(comp["optimal"])

    def test_compaction_audit_off(self) -> None:
        write_preferences(self.home, {"compactPercent": "off"})
        proc = self.run_fleet_setup("--audit", "--json", "--no-health")
        self.assertEqual(proc.returncode, 0, f"Error: {proc.stderr}")
        data = json.loads(proc.stdout)
        comp = data["settings"]["compaction"]
        self.assertEqual(comp["preference"], "off")
        self.assertIsNone(comp["expected"])
        self.assertTrue(comp["optimal"])

        proc_text = self.run_fleet_setup("--audit", "--no-health")
        self.assertEqual(proc_text.returncode, 0)
        self.assertIn("Compaction window: 1000000 (AgentFleet leaves it alone: compact off)", proc_text.stdout)

    def test_fix_settings_writes_computed_window(self) -> None:
        # Setup settings with non-optimal window
        self.settings_data["autoCompactWindow"] = 235929
        self.settings_data["env"]["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = "235929"
        self.settings_data["env"]["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = "235929"
        (self.claude_dir / "settings.json").write_text(json.dumps(self.settings_data, indent=2))
        write_preferences(self.home, {"compactPercent": 70})

        proc = self.run_fleet_setup("--fix-settings")
        self.assertEqual(proc.returncode, 0, f"Error: {proc.stderr}")
        self.assertIn("235929 -> 1000000", proc.stdout)

        updated = json.loads((self.claude_dir / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(updated["autoCompactWindow"], 1000000)
        self.assertEqual(updated["env"]["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "1000000")
        self.assertEqual(updated["env"]["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "1000000")

    def test_compaction_override_writes_pair_and_sets_percent_off(self) -> None:
        proc = self.run_fleet_setup("--compaction", "750000")
        self.assertEqual(proc.returncode, 0, f"Error: {proc.stderr}")
        self.assertIn("750000", proc.stdout)
        self.assertIn("off", proc.stdout)

        updated = json.loads((self.claude_dir / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(updated["autoCompactWindow"], 750000)
        self.assertEqual(updated["env"]["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "750000")

        prefs = load_preferences(self.home)
        self.assertEqual(prefs["compactPercent"], "off")

    def test_compact_percent_flag(self) -> None:
        # Set to 60%; active model has 1M context -> expected 1000000
        proc = self.run_fleet_setup("--compact-percent", "60")
        self.assertEqual(proc.returncode, 0, f"Error: {proc.stderr}")

        prefs = load_preferences(self.home)
        self.assertEqual(prefs["compactPercent"], 60)

        updated = json.loads((self.claude_dir / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(updated["autoCompactWindow"], 1000000)
        self.assertEqual(updated["env"]["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "1000000")
        self.assertEqual(updated["env"]["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "1000000")

    def test_compact_percent_flag_accepts_range_floor(self) -> None:
        proc = self.run_fleet_setup("--compact-percent", "20")
        self.assertEqual(proc.returncode, 0, f"Error: {proc.stderr}")
        self.assertEqual(load_preferences(self.home)["compactPercent"], 20)

    def test_fix_settings_unknown_context_skips_max_context(self) -> None:
        self.settings_data["model"] = "opus"
        self.settings_data["env"].pop("ANTHROPIC_DEFAULT_OPUS_MODEL", None)
        self.settings_data["env"]["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = "700000"
        (self.claude_dir / "settings.json").write_text(json.dumps(self.settings_data, indent=2))
        write_preferences(self.home, {"compactPercent": 70})

        proc = self.run_fleet_setup("--fix-settings")
        self.assertEqual(proc.returncode, 0, f"Error: {proc.stderr}")
        updated = json.loads((self.claude_dir / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(updated["env"]["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "700000")
        self.assertNotIn("None", json.dumps(updated))
        self.assertEqual(updated["env"]["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"], "70")

    def _setup_fake_claude_on_path(self) -> tuple[str, dict[str, str]]:
        bin_dir = self.home / "fake-bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        fake_claude = bin_dir / "claude"

        script_content = """import json
import sys

sys.stdout.reconfigure(encoding="utf-8")
if len(sys.argv) >= 3 and sys.argv[1] == "plugin" and sys.argv[2] == "list":
    data = [
        {"id": "plugin-alpha", "enabled": True},
        {"id": "plugin-beta", "enabled": False},
        {"id": "plugin-gamma", "enabled": True, "error": "Plugin failed to load properly: unexpected token in manifest"},
    ]
    print(json.dumps(data))
    sys.exit(0)

if len(sys.argv) >= 3 and sys.argv[1] == "mcp" and sys.argv[2] == "list":
    output = \"\"\"Checking MCP server health…

plugin:context7:context7: https://mcp.context7.com/mcp?client=claude-code-plugin (HTTP) - ! Needs authentication
plugin:github:github: https://api.githubcopilot.com/mcp/ (HTTP) - ✘ Failed to connect — HTTP 400: Error POSTing to endpoint
agent-brain-memory: /usr/local/bin/agent-brain mcp - ✔ Connected
\"\"\"
    print(output)
    sys.exit(0)

sys.exit(1)
"""
        fake_claude.write_text(script_content, encoding="utf-8")
        if os.name == "nt":
            # PATH lookup on Windows finds claude.CMD, not the extensionless script.
            (bin_dir / "claude.cmd").write_text(f'@"{sys.executable}" "%~dp0claude" %*\n', encoding="utf-8")
        else:
            fake_claude.write_text("#!/usr/bin/env python3\n" + script_content, encoding="utf-8")
            fake_claude.chmod(fake_claude.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        env = dict(os.environ)
        env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
        return str(fake_claude), env

    def test_health_parsing_with_fake_claude(self) -> None:
        _, env = self._setup_fake_claude_on_path()

        # Seed installed_plugins.json
        installed_data = {
            "version": 2,
            "plugins": {
                "plugin-alpha": [{"scope": "user"}],
                "plugin-beta": [{"scope": "user"}],
                "plugin-gamma": [{"scope": "user"}],
            },
        }
        (self.plugins_dir / "installed_plugins.json").write_text(json.dumps(installed_data), encoding="utf-8")

        proc = self.run_fleet_setup("--audit", "--json", env=env)
        self.assertEqual(proc.returncode, 0, f"Error: {proc.stderr}")
        data = json.loads(proc.stdout)
        self.assertIn("health", data)

        plugins_health = data["health"]["plugins"]
        self.assertTrue(plugins_health["available"])
        self.assertEqual(plugins_health["installed"], 3)
        self.assertEqual(plugins_health["enabled"], 2)
        self.assertEqual(len(plugins_health["errors"]), 1)
        self.assertEqual(plugins_health["errors"][0]["name"], "plugin-gamma")
        self.assertIn("unexpected token", plugins_health["errors"][0]["error"])

        mcp_health = data["health"]["mcp"]
        self.assertTrue(mcp_health["available"])
        self.assertEqual(mcp_health["total"], 3)
        self.assertEqual(mcp_health["healthy"], 1)
        self.assertEqual(len(mcp_health["failed"]), 2)

        failed_by_name = {item["name"]: item for item in mcp_health["failed"]}
        self.assertIn("plugin:context7:context7", failed_by_name)
        self.assertEqual(failed_by_name["plugin:context7:context7"]["status"], "needs-auth")
        self.assertEqual(failed_by_name["plugin:context7:context7"]["hint"], "Run /mcp and authenticate.")

        self.assertIn("plugin:github:github", failed_by_name)
        self.assertEqual(failed_by_name["plugin:github:github"]["status"], "failed")
        self.assertEqual(
            failed_by_name["plugin:github:github"]["hint"],
            "Check the server's command or credentials, then run /mcp to reconnect.",
        )

        # Test text output
        proc_text = self.run_fleet_setup("--audit", env=env)
        self.assertEqual(proc_text.returncode, 0)
        self.assertIn("Plugins: 2/3 enabled, 1 with errors", proc_text.stdout)
        self.assertIn("MCP: 1/3 healthy", proc_text.stdout)
        self.assertIn("Run /mcp and authenticate.", proc_text.stdout)
        self.assertIn("Check the server's command or credentials, then run /mcp to reconnect.", proc_text.stdout)

    def test_no_health_flag(self) -> None:
        _, env = self._setup_fake_claude_on_path()
        proc = self.run_fleet_setup("--audit", "--json", "--no-health", env=env)
        self.assertEqual(proc.returncode, 0, f"Error: {proc.stderr}")
        data = json.loads(proc.stdout)
        self.assertNotIn("health", data)

        proc_text = self.run_fleet_setup("--audit", "--no-health", env=env)
        self.assertEqual(proc_text.returncode, 0)
        self.assertNotIn("Plugins:", proc_text.stdout)
        self.assertNotIn("MCP:", proc_text.stdout)

    def test_missing_cli(self) -> None:
        empty_bin = self.home / "empty-bin"
        empty_bin.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        # Empty PATH containing only an empty directory
        env["PATH"] = str(empty_bin)

        proc = self.run_fleet_setup("--audit", "--json", env=env)
        self.assertEqual(proc.returncode, 0, f"Error: {proc.stderr}")
        data = json.loads(proc.stdout)
        self.assertIn("health", data)

        plugins_health = data["health"]["plugins"]
        self.assertFalse(plugins_health["available"])
        self.assertIn("reason", plugins_health)

        mcp_health = data["health"]["mcp"]
        self.assertFalse(mcp_health["available"])
        self.assertIn("reason", mcp_health)


if __name__ == "__main__":
    unittest.main()
