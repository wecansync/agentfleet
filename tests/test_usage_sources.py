#!/usr/bin/env python3
"""Tests for the usage and price sources added in 2.11.0: prices a gateway
publishes, lane runs priced by their configured model, the gateway section of
`agentfleet report`, the files the mods read, and `config provider-usage`."""
import sys
sys.dont_write_bytecode = True

import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
AGENTFLEET = REPO / "bin" / "agentfleet"
sys.path.insert(0, str(REPO / "scripts"))

import fleet_watch  # noqa: E402
import provider_catalog  # noqa: E402
import provider_usage  # noqa: E402

# Built at runtime so the bundle's own secret scan never sees a key-shaped literal.
FAKE_KEY = "sk-" + "1234567890" * 2
GATEWAY = "https://gateway.example/api"
ROWS = [
    {"id": "codex-luna", "type": "model", "pricing": {"prompt": "0.000002", "completion": "0.000008", "input_cache_read": "0.0000002"}},
    {"id": "claude-opus-5.5", "type": "model", "pricing": {"prompt": 0.00001, "completion": 0.00005}},
    {"id": "no-price", "type": "model"},
]


def iso(moment: dt.datetime) -> str:
    return moment.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class UsageSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.claude = self.home / ".claude"
        self.store = self.claude / "agentfleet"
        self.store.mkdir(parents=True)
        self.set_gateway(GATEWAY)
        self.write_cache(GATEWAY, FAKE_KEY)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def set_gateway(self, base: str | None) -> None:
        env = {"ANTHROPIC_BASE_URL": base, "ANTHROPIC_AUTH_TOKEN": FAKE_KEY} if base else {}
        (self.claude / "settings.json").write_text(json.dumps({"env": env}), encoding="utf-8")

    def write_cache(self, base: str, token: str) -> None:
        cache = self.claude / "cache"
        cache.mkdir(exist_ok=True)
        payload = provider_catalog.cache_payload(ROWS, base, token)
        (cache / "provider-models-cache.json").write_text(json.dumps(payload), encoding="utf-8")

    def write_usage(self, **overrides) -> Path:
        document = {
            "format": provider_usage.FORMAT,
            "endpoint": provider_catalog.endpoint_scope(GATEWAY),
            "account": provider_catalog.account_scope(FAKE_KEY),
            "reader": "omniroute",
            "detectedAt": iso(dt.datetime.now(dt.timezone.utc)),
            "fetchedAt": iso(dt.datetime.now(dt.timezone.utc)),
            "startedAt": None,
            "usage": {
                "spend": {"month": {"usd": 51.75, "listUsd": 391.2, "unpriced": 3}},
                "budget": {"limitUsd": 200.0, "usedUsd": 51.75, "percent": 25.9, "resetAt": "2026-11-01T00:00:00Z"},
                "limits": [], "exhausted": [{"provider": "antigravity", "resetAt": None}],
            },
            "sessions": {}, "notices": [],
        }
        document.update(overrides)
        path = self.store / provider_usage.STORE_NAME
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def write_prices(self, prices: dict) -> None:
        provider_catalog.write_preferences(self.home, {"prices": prices})

    def run_agentfleet(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-B", str(AGENTFLEET), "--home", str(self.home),
             "--config-home", str(Path(self.temp.name) / "config"), *args],
            capture_output=True, text=True, cwd=self.temp.name,
            env={"PATH": "/usr/bin:/bin", "HOME": str(self.home), "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"},
        )

    # Prices a gateway publishes -------------------------------------------

    def test_published_prices_are_read_per_million_tokens(self) -> None:
        prices = provider_catalog.catalog_prices(self.home)
        self.assertEqual(prices["codex-luna"], {"input": 2.0, "output": 8.0, "cacheRead": 0.2})
        self.assertEqual(prices["claude-opus-5.5"], {"input": 10.0, "output": 50.0})
        self.assertNotIn("no-price", prices)

    def test_published_prices_need_the_current_gateway_and_key(self) -> None:
        self.write_cache("https://other.example/api", FAKE_KEY)
        self.assertEqual(provider_catalog.catalog_prices(self.home), {})
        self.write_cache(GATEWAY, FAKE_KEY + "x")
        self.assertEqual(provider_catalog.catalog_prices(self.home), {})
        self.write_cache(GATEWAY, FAKE_KEY)
        self.set_gateway(None)
        self.assertEqual(provider_catalog.catalog_prices(self.home), {})

    def test_own_prices_replace_published_ones_model_by_model(self) -> None:
        self.write_prices({"Codex-Luna[1m]": {"input": 1, "output": 1}})
        prices = provider_catalog.effective_prices(self.home)
        self.assertEqual(prices["Codex-Luna[1m]"], {"input": 1, "output": 1})
        self.assertNotIn("codex-luna", prices)
        self.assertEqual(prices["claude-opus-5.5"]["input"], 10.0)

    # Files the mods read --------------------------------------------------

    def test_published_prices_file_changes_only_when_prices_do(self) -> None:
        path = self.store / "published-prices.json"
        fleet_watch._publish_prices(self.home)
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["format"], fleet_watch.PUBLISHED_PRICES_FORMAT)
        self.assertEqual((data["endpoint"], data["account"]), provider_usage.scope(self.home))
        self.assertEqual(data["prices"]["codex-luna"]["input"], 2.0)
        os.utime(path, (1, 1))
        fleet_watch._publish_prices(self.home)
        self.assertEqual(path.stat().st_mtime, 1)
        # Without a gateway nothing is published, and the prompt hook removes
        # the last gateway's file.
        self.set_gateway(None)
        fleet_watch._publish_prices(self.home)
        self.assertEqual(path.stat().st_mtime, 1)
        fleet_watch._start_usage_refresh(self.home, "session-1")
        self.assertFalse(path.exists())

    def test_another_gateways_figures_and_prices_are_removed(self) -> None:
        usage = self.write_usage()
        fleet_watch._publish_prices(self.home)
        prices = self.store / provider_usage.PUBLISHED_PRICES_NAME
        provider_usage.discard_foreign(self.home)
        self.assertTrue(usage.exists() and prices.exists(), "the current gateway keeps its files")
        # Same gateway, another key: the mods must not show the old key's spend.
        settings = json.loads((self.claude / "settings.json").read_text(encoding="utf-8"))
        settings["env"]["ANTHROPIC_AUTH_TOKEN"] = FAKE_KEY + "x"
        (self.claude / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
        provider_usage.discard_foreign(self.home)
        self.assertFalse(usage.exists())
        self.assertFalse(prices.exists())

    def test_session_start_removes_the_previous_profiles_figures(self) -> None:
        usage = self.write_usage(endpoint="https://other.example/api")
        result = subprocess.run(
            [sys.executable, "-B", str(AGENTFLEET), "--home", str(self.home),
             "--config-home", str(Path(self.temp.name) / "config"), "auto-update", "--hook"],
            capture_output=True, text=True, cwd=self.temp.name,
            env={"PATH": "/usr/bin:/bin", "HOME": str(self.home), "LANG": "C.UTF-8",
                 "PYTHONDONTWRITEBYTECODE": "1", "AGENTFLEET_AUTO_UPDATE": "0"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(usage.exists())

    def test_a_refresh_removes_temp_files_a_killed_reader_left(self) -> None:
        old = self.store / f".{provider_usage.STORE_NAME}.123.abc.tmp"
        fresh = self.store / f".{provider_usage.STORE_NAME}.456.def.tmp"
        for path in (old, fresh):
            path.write_text("{}", encoding="utf-8")
        os.utime(old, (1, 1))
        provider_usage._remove_stale_temps(self.home)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists(), "a live reader's temp file is left alone")

    def test_a_refresh_does_not_save_after_the_profile_changed(self) -> None:
        original = provider_usage._read_reader

        def switch_then_answer(reader, base, token, session_id, deadline):
            self.set_gateway("https://other.example/api")
            return {"spend": {"month": {"usd": 999.0}}, "budget": None, "limits": [], "exhausted": []}, None

        provider_usage._read_reader = switch_then_answer
        try:
            self.assertEqual(provider_usage.refresh(self.home, "session-1"), 1)
        finally:
            provider_usage._read_reader = original
        raw = (self.store / provider_usage.STORE_NAME).read_text(encoding="utf-8")
        self.assertNotIn("999", raw)
        self.assertIsNone(provider_usage.load(self.home))

    def test_no_published_prices_writes_no_file(self) -> None:
        self.set_gateway(None)
        fleet_watch._publish_prices(self.home)
        self.assertFalse((self.store / "published-prices.json").exists())

    def test_leftover_gateway_figures_are_removed_without_a_gateway(self) -> None:
        path = self.write_usage()
        fleet_watch._start_usage_refresh(self.home, "session-1")
        self.assertTrue(path.exists(), "a current gateway keeps its figures")
        self.set_gateway(None)
        fleet_watch._start_usage_refresh(self.home, "session-1")
        self.assertFalse(path.exists())

    # agentfleet report ----------------------------------------------------

    def write_lane_rows(self, *rows: dict) -> None:
        at = iso(dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1))
        lines = [json.dumps({"at": at, "lane": "implement", **row}) for row in rows]
        (self.store / "lane-reports.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_lane_runs_are_priced_by_their_configured_model_first(self) -> None:
        self.set_gateway(None)  # only the user's own prices
        self.write_prices({"codex-luna": {"input": 2, "output": 8}})
        self.write_lane_rows(
            {"model": "gpt-6-luna", "configuredModel": "codex-luna", "inputTokens": 1_000_000, "outputTokens": 1_000_000},
            {"model": "gpt-6-luna", "inputTokens": 1_000_000, "outputTokens": 0},
        )
        data = fleet_watch.report(self.home)
        self.assertEqual(data["lanes"]["implement"]["estimatedCost"], 10.0)
        self.assertIn("gpt-6-luna", json.dumps(data["cost"]))

    def test_published_prices_price_lane_runs_without_own_prices(self) -> None:
        self.write_lane_rows({"model": "codex-luna", "inputTokens": 1_000_000, "outputTokens": 0})
        self.assertEqual(fleet_watch.report(self.home)["lanes"]["implement"]["estimatedCost"], 2.0)

    def test_report_shows_what_the_gateway_reported(self) -> None:
        self.write_usage()
        data = fleet_watch.report(self.home)
        self.assertEqual(data["gateway"]["reader"], "omniroute")
        text = fleet_watch.format_report(data)
        self.assertIn("Reported by your gateway (omniroute", text)
        self.assertIn("This month   $51.75 (list price $391.20; 3 requests unpriced)", text)
        self.assertIn("Budget       26% used ($51.75 of $200.00), resets 2026-11-01 00:00 UTC", text)
        self.assertIn("Exhausted    all antigravity accounts", text)
        self.assertNotIn("Last session", text)

    def test_report_shows_the_last_session_the_hook_refreshed(self) -> None:
        now = dt.datetime.now(dt.timezone.utc)
        self.write_usage(sessions={
            "older-session-1": {"usd": None, "listUsd": 5.0, "fetchedAt": iso(now - dt.timedelta(hours=2))},
            "newer-session-2": {"usd": None, "listUsd": 403.6, "unpriced": 1726, "requests": 10147,
                                "fetchedAt": iso(now - dt.timedelta(minutes=1))},
        })
        text = fleet_watch.format_report(fleet_watch.report(self.home))
        self.assertIn("Last session $403.60 at list price (10.1k requests; 1.7k unpriced)", text)

    def test_report_leaves_out_other_gateways_and_the_setting_off(self) -> None:
        self.write_usage(endpoint="https://other.example/api")
        self.assertIsNone(fleet_watch.report(self.home).get("gateway"))
        self.write_usage()
        provider_catalog.write_preferences(self.home, {"providerUsage": False})
        self.assertIsNone(fleet_watch.report(self.home).get("gateway"))
        self.assertNotIn("Reported by your gateway", fleet_watch.format_report(fleet_watch.report(self.home)))

    # Preference and command -----------------------------------------------

    def test_provider_usage_preference_defaults_on_and_rejects_non_booleans(self) -> None:
        self.assertIs(provider_catalog.load_preferences(self.home)["providerUsage"], True)
        with self.assertRaises(Exception):
            provider_catalog.write_preferences(self.home, {"providerUsage": "no"})

    def test_config_provider_usage_off_deletes_the_figures_and_on_turns_back_on(self) -> None:
        path = self.write_usage()
        off = self.run_agentfleet("config", "provider-usage", "off")
        self.assertEqual(off.returncode, 0, off.stderr)
        self.assertFalse(path.exists())
        self.assertIs(provider_catalog.load_preferences(self.home)["providerUsage"], False)
        self.assertFalse(provider_usage.refresh_due(self.home, "session-1"))
        on = self.run_agentfleet("config", "provider-usage", "on")
        self.assertEqual(on.returncode, 0, on.stderr)
        self.assertIs(provider_catalog.load_preferences(self.home)["providerUsage"], True)
        bad = self.run_agentfleet("config", "provider-usage", "maybe")
        self.assertNotEqual(bad.returncode, 0)

    def test_config_tells_a_gateway_not_reached_from_one_without_a_report(self) -> None:
        self.write_usage(reader=None, detectedAt=None, fetchedAt=None, usage=None)
        unreached = self.run_agentfleet("config")
        self.assertEqual(unreached.returncode, 0, unreached.stderr)
        self.assertIn("gateway not reached yet; retrying", unreached.stdout)
        self.write_usage(reader=None, fetchedAt=None, usage=None)
        answered = self.run_agentfleet("config")
        self.assertIn("gateway reports no usage", answered.stdout)


class DeadlineTests(unittest.TestCase):
    """A gateway that keeps sending a few bytes must not hold a reader open."""

    def test_a_trickling_gateway_is_cut_off_at_the_deadline(self) -> None:
        import http.server
        import threading
        import time

        class Trickle(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", "100000")
                self.end_headers()
                try:
                    for _ in range(100):
                        self.wfile.write(b" ")
                        self.wfile.flush()
                        time.sleep(0.05)
                except OSError:
                    pass

            def log_message(self, *args) -> None:
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Trickle)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            started = time.monotonic()
            result = provider_usage._request_json(base, "/v1/me/status", FAKE_KEY, started + 0.5)
            elapsed = time.monotonic() - started
        finally:
            server.shutdown()
            server.server_close()
        # Cut off at the deadline: not an answer, so it must not count as one.
        self.assertIs(result, provider_usage._UNREACHABLE)
        self.assertLess(elapsed, 2.0, "the reader kept reading past its deadline")

    def test_the_watchdog_ends_a_reader_before_another_may_start(self) -> None:
        self.assertLess(provider_usage.WATCHDOG_SECONDS, provider_usage.RUNNING_INTERVAL)


if __name__ == "__main__":
    unittest.main()
