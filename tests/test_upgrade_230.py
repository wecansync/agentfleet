#!/usr/bin/env python3
"""Upgrades to 2.3.0's percentage compaction: the gateway context cap, a
user's own percentage, a hand-set native window, and rollback of the
configuration wizard's state."""
from __future__ import annotations

import http.server
import json
import os
from pathlib import Path
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
INSTALLER = ROOT / "bin" / "install.py"
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "scripts"))
import provider_catalog  # noqa: E402

MODEL_IDS = ["claude-opus-5", "claude-sonnet-5", "claude-haiku", "codex-5.5"]
TOKEN = "token-upgrade-2300"
CONTEXT = 400_000


def gateway():
    """A fake gateway whose models all have a 400k context, or none."""
    class Handler(http.server.BaseHTTPRequestHandler):
        mode = "ok"

        def do_GET(self):  # noqa: N802
            if type(self).mode == "down":
                self.send_response(503)
                self.end_headers()
                return
            if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                self.send_response(401)
                self.end_headers()
                return
            sized = type(self).mode != "unsized"
            body = json.dumps({"data": [{"id": i, **({"context_length": CONTEXT} if sized else {})} for i in MODEL_IDS]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, Handler, f"http://127.0.0.1:{server.server_address[1]}"


@unittest.skipIf(shutil.which("node") is None, "Node.js is required for the agent generator")
class Upgrade230Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.handler, cls.url = gateway()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.handler.mode = "ok"
        self.raw = tempfile.TemporaryDirectory(prefix="agentfleet 230 ")
        self.addCleanup(self.raw.cleanup)
        self.home = Path(self.raw.name) / "home"
        self.config = self.home / ".config"
        self.claude = self.home / ".claude"
        self.env = {
            "PATH": os.environ.get("PATH", ""), "HOME": str(self.home), "XDG_CONFIG_HOME": str(self.config),
            "PYTHONDONTWRITEBYTECODE": "1", "AGENTFLEET_MODS": "0", "AGENTFLEET_NONINTERACTIVE": "1",
            "AGENTFLEET_NO_CONFIGURE": "1", "TOK_GW": TOKEN,
        }
        if os.name == "nt":
            # Python's SSL setup and cmd.exe both need these on Windows.
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

    def install_gateway(self):
        return self.ok(self.run_installer("--apply", "--no-wizard", "--provider", "gateway", "--gateway-url", self.url,
                                          "--allow-insecure-http", "--gateway-token-env", "TOK_GW"))

    def install_native(self):
        return self.ok(self.run_installer("--apply", "--no-wizard", "--provider", "native"))

    def update(self):
        # An update re-runs the installer with no provider arguments.
        return self.ok(self.run_installer("--apply", "--no-wizard"))

    def doctor(self):
        command = [PYTHON, "-B", str(ROOT / "bin" / "claude-agents-doctor"), "--check", "--home", str(self.home),
                   "--config-home", str(self.config), "--prefix", str(self.home / ".local"), "--bundle", str(ROOT)]
        return self.ok(subprocess.run(command, cwd=ROOT, env=self.env, text=True, capture_output=True))

    @property
    def settings_path(self) -> Path:
        return self.claude / "settings.json"

    @property
    def metadata_path(self) -> Path:
        return self.claude / ".claude-agents-config-install.json"

    def settings(self) -> dict:
        return json.loads(self.settings_path.read_text())

    def preferences(self) -> dict:
        path = provider_catalog.preferences_path(self.home)
        return json.loads(path.read_text()) if path.is_file() else {}

    def write_settings(self, value: dict):
        self.settings_path.write_text(json.dumps(value, indent=2) + "\n")

    def as_220(self, window: int | None = None, cap: int | None = None, percent: str | None = None):
        """Rewrite the installed state as 2.2.0 left it: the window its hooks
        scaled (pinned to the cap on a gateway), no percentage override, and
        no compaction preference or journal entries 2.2.0 never wrote."""
        settings = self.settings()
        env = settings.setdefault("env", {})
        env.pop(provider_catalog.COMPACT_PERCENT_ENV, None)
        if percent is not None:
            env[provider_catalog.COMPACT_PERCENT_ENV] = percent
        if window is not None:
            settings["autoCompactWindow"] = window
            env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = str(window)
        if cap is not None:
            env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = str(cap)
        self.write_settings(settings)
        meta = json.loads(self.metadata_path.read_text())
        dropped = {"value:env:" + provider_catalog.COMPACT_PERCENT_ENV, "value:env:CLAUDE_CODE_MAX_CONTEXT_TOKENS"}
        meta["settingsJournal"] = [entry for entry in meta.get("settingsJournal", []) if entry.get("id") not in dropped]
        meta["version"] = "2.2.0"
        self.metadata_path.write_text(json.dumps(meta, indent=2) + "\n")
        provider_catalog.preferences_path(self.home).unlink(missing_ok=True)

    def write_cache(self, age_seconds: int):
        rows = [{"id": i, "context_length": CONTEXT} for i in MODEL_IDS]
        payload = provider_catalog.cache_payload(rows, self.url, TOKEN, fetched_at=int(time.time()) - age_seconds)
        cache = self.claude / "cache" / "provider-models-cache.json"
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(payload))

    # -- defect 2: the cap on a fresh gateway install ---------------------------
    def test_fresh_gateway_install_caps_at_the_catalog_context(self):
        self.install_gateway()
        env = self.settings()["env"]
        self.assertEqual(env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], str(CONTEXT), "the cap is the model's real context, not 1M")
        self.assertEqual(env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "1000000")
        self.assertEqual(env[provider_catalog.COMPACT_PERCENT_ENV], "70")
        self.doctor()
        self.ok(self.run_installer("--uninstall", "--apply"))
        self.assertNotIn("CLAUDE_CODE_MAX_CONTEXT_TOKENS", self.settings().get("env", {}), "uninstall removes the cap it added")

    def test_fresh_gateway_install_without_a_known_context_writes_no_cap(self):
        self.handler.mode = "unsized"
        self.install_gateway()
        self.assertNotIn("CLAUDE_CODE_MAX_CONTEXT_TOKENS", self.settings()["env"])

    def test_native_install_never_writes_a_cap(self):
        self.install_native()
        self.assertNotIn("CLAUDE_CODE_MAX_CONTEXT_TOKENS", self.settings()["env"])

    # -- defect 1: the scaled 2.2.0 cap survives an upgrade ---------------------
    def test_gateway_upgrade_from_220_replaces_the_scaled_cap(self):
        self.install_gateway()
        self.as_220(window=280_000, cap=280_000)
        self.update()
        settings = self.settings()
        self.assertEqual(settings["autoCompactWindow"], 1_000_000)
        self.assertEqual(settings["env"]["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "1000000")
        self.assertEqual(settings["env"][provider_catalog.COMPACT_PERCENT_ENV], "70")
        self.assertEqual(settings["env"]["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], str(CONTEXT), "70% must apply to the real 400k context")
        self.doctor()

    def test_offline_gateway_upgrade_uses_an_older_cache_for_the_cap_and_window(self):
        self.install_gateway()
        self.as_220(window=280_000, cap=280_000)
        self.write_cache(age_seconds=7 * 60 * 60)  # older than the 6-hour startup TTL
        self.handler.mode = "down"
        self.update()
        settings = self.settings()
        self.assertEqual(settings["autoCompactWindow"], 1_000_000, "the scaled pair is AgentFleet's, not a hand edit")
        self.assertEqual(settings["env"]["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], str(CONTEXT))
        self.assertEqual(settings["env"][provider_catalog.COMPACT_PERCENT_ENV], "70")

    def test_offline_gateway_upgrade_with_unknown_context_keeps_the_cap(self):
        self.install_gateway()
        self.as_220(window=280_000, cap=280_000)
        (self.claude / "cache" / "provider-models-cache.json").unlink(missing_ok=True)
        self.handler.mode = "down"
        self.update()
        settings = self.settings()
        # The pair 2.2.0 pinned to the cap is still AgentFleet's.
        self.assertEqual(settings["autoCompactWindow"], 1_000_000)
        self.assertEqual(settings["env"]["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "280000", "an unknown context keeps the cap")

    def test_gateway_upgrade_keeps_a_cap_the_user_set(self):
        self.install_gateway()
        self.as_220(window=280_000, cap=280_000)
        settings = self.settings()
        settings["env"]["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = "333333"
        self.write_settings(settings)
        self.update()
        self.assertEqual(self.settings()["env"]["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "333333")

    # -- defect 3: the user's own percentage ------------------------------------
    def test_upgrade_keeps_and_records_the_users_own_percentage(self):
        self.install_native()
        self.as_220(percent="85")
        result = self.update()
        self.assertIn("Kept your auto-compact percentage (85%)", result.stdout)
        self.assertEqual(self.settings()["env"][provider_catalog.COMPACT_PERCENT_ENV], "85")
        self.assertEqual(self.preferences().get("compactPercent"), 85)
        self.doctor()
        self.ok(self.run_installer("--uninstall", "--apply"))
        self.assertEqual(self.settings()["env"][provider_catalog.COMPACT_PERCENT_ENV], "85", "uninstall leaves the user's value")

    def test_upgrade_leaves_a_percentage_outside_the_range_alone(self):
        self.install_native()
        self.as_220(percent="10")
        result = self.update()
        self.assertIn("Left your CLAUDE_AUTOCOMPACT_PCT_OVERRIDE setting as it is", result.stdout)
        self.assertEqual(self.settings()["env"][provider_catalog.COMPACT_PERCENT_ENV], "10")
        self.assertEqual(self.preferences().get("compactPercent"), "off")
        self.doctor()

    def test_fresh_install_keeps_a_percentage_set_before_agentfleet(self):
        self.claude.mkdir(parents=True)
        self.write_settings({"env": {provider_catalog.COMPACT_PERCENT_ENV: "60"}})
        self.install_native()
        self.assertEqual(self.settings()["env"][provider_catalog.COMPACT_PERCENT_ENV], "60")
        self.assertEqual(self.preferences().get("compactPercent"), 60)
        self.ok(self.run_installer("--uninstall", "--apply"))
        self.assertEqual(self.settings()["env"][provider_catalog.COMPACT_PERCENT_ENV], "60")

    def test_reinstall_refreshes_a_percentage_agentfleet_wrote(self):
        self.install_native()
        provider_catalog.write_preferences(self.home, {"compactPercent": 40})
        self.update()
        self.assertEqual(self.settings()["env"][provider_catalog.COMPACT_PERCENT_ENV], "40")
        # A journaled value stays AgentFleet's: without a preference it
        # returns to the default instead of being recorded as the user's.
        provider_catalog.preferences_path(self.home).unlink()
        self.update()
        self.assertEqual(self.settings()["env"][provider_catalog.COMPACT_PERCENT_ENV], "70")

    # -- defect 4: a hand-set native pair ---------------------------------------
    def test_hand_set_native_pair_gets_off_and_no_percentage(self):
        self.install_native()
        self.as_220(window=500_000)
        result = self.update()
        settings = self.settings()
        self.assertEqual(settings["autoCompactWindow"], 500_000)
        self.assertEqual(settings["env"]["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "500000")
        self.assertNotIn(provider_catalog.COMPACT_PERCENT_ENV, settings["env"], "'off' means no percentage is written")
        self.assertEqual(self.preferences().get("compactPercent"), "off")
        self.assertIn("Kept your auto-compact window (500000 tokens)", result.stdout)
        self.doctor()

    # -- defect 5: rollback and the wizard's state ------------------------------
    def test_rollback_restores_the_wizard_state(self):
        self.install_native()
        wizard = self.claude / "agentfleet" / "wizard.json"
        self.assertFalse(wizard.exists())
        self.update()
        # `agentfleet configure` runs after the update and records answers.
        provider_catalog.write_preferences(self.home, {"compactPercent": 50, "learn": True})
        wizard.write_text(json.dumps({"format": "agentfleet.wizard.v1", "asked": ["compact", "learn"], "version": "2.3.0"}))
        self.ok(self.run_installer("--rollback", "--apply"))
        self.assertFalse(wizard.exists(), "rollback forgets answers given after the snapshot")

    def test_rollback_keeps_wizard_state_from_before_the_update(self):
        self.install_native()
        wizard = self.claude / "agentfleet" / "wizard.json"
        before = json.dumps({"format": "agentfleet.wizard.v1", "asked": ["compact"], "version": "2.3.0"})
        wizard.write_text(before)
        self.update()
        wizard.write_text(json.dumps({"format": "agentfleet.wizard.v1", "asked": ["compact", "learn"], "version": "2.3.0"}))
        self.ok(self.run_installer("--rollback", "--apply"))
        self.assertEqual(wizard.read_text(), before)


if __name__ == "__main__":
    unittest.main()
