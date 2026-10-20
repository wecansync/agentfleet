#!/usr/bin/env python3
"""Tests for supported gateway usage formats and cache behavior."""
from __future__ import annotations

import http.server
import importlib.util
import json
import os
from pathlib import Path
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "provider_usage.py"
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("provider_usage", SCRIPT)
provider_usage = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = provider_usage
spec.loader.exec_module(provider_usage)

# Built at runtime so the bundle's own secret scan never sees a key-shaped literal.
FAKE_KEY = "sk-" + "1234567890" * 2
SESSION = "session-123"


class UsageServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


class Handler(http.server.BaseHTTPRequestHandler):
    routes = {}

    def do_GET(self):
        parsed = urlsplit(self.path)
        value = self.routes.get(self.path, self.routes.get(parsed.path, (404, {"error": "missing"})))
        status, payload = value
        if status in {301, 302, 307, 308}:
            self.send_response(status)
            self.send_header("Location", payload)
            self.end_headers()
            return
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, fmt, *args):
        pass


class ProviderUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.claude = self.home / ".claude"
        self.claude.mkdir(parents=True)
        self.server = UsageServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        Handler.routes = {}
        self.base = f"http://127.0.0.1:{self.server.server_port}/api"
        self.set_gateway()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def set_gateway(self, base=None, token=FAKE_KEY):
        settings = {"env": {"ANTHROPIC_BASE_URL": base or self.base, "ANTHROPIC_AUTH_TOKEN": token}}
        (self.claude / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

    def route(self, path, payload, status=200):
        Handler.routes[path] = (status, payload)

    def refresh(self, session=None):
        return provider_usage.refresh(self.home, session)

    def stored(self):
        return json.loads(provider_usage._store_path(self.home).read_text(encoding="utf-8"))

    def test_omniroute_detection_mapping_limits_exhausted_and_notices(self):
        reset = provider_usage._now_iso(time.time() + 3600)
        payload = {"usage": {
            "cost": {"period": "monthly", "usedUsd": 42.5, "listPriceUsd": 50, "unpricedRequests": 2,
                     "limitUsd": 50, "remainingUsd": 7.5, "usedPercent": 85, "resetAt": reset},
            "daily": {"costUsd": 3, "listPriceUsd": 4, "unpricedRequests": 1, "requests": 3,
                      "tokens": {"total": 30}, "resetAt": reset},
            "weekly": {"costUsd": 12, "listPriceUsd": 14, "requests": 8,
                       "tokens": {"total": 80}, "resetAt": reset}},
            # The shape OmniRoute serves: scope is an object naming a provider, or global.
            "limits": [{"source": "token_limit", "metric": "tokens", "window": "hour", "scope": {"type": "provider", "value": "demo"},
                        "limit": 100, "used": 92, "utilization": 0.92, "resetAt": reset},
                       {"source": "token_limit", "metric": "tokens", "window": "daily", "scope": {"type": "global", "value": None},
                        "limit": 100, "used": 10, "utilization": 0.1, "resetAt": reset}],
            "accountQuotas": [{"provider": "demo", "plan": "pro", "quotas": {
                "model-a": {"usedPercentage": 100, "remainingPercentage": 0, "resetAt": reset}}}]}
        self.route("/api/v1/me/status", payload)
        self.assertEqual(self.refresh(), 0)
        result = self.stored()
        self.assertEqual(result["reader"], "omniroute")
        self.assertEqual(result["usage"]["spend"]["today"]["usd"], 3)
        self.assertEqual(result["usage"]["spend"]["week"]["listUsd"], 14)
        self.assertEqual(result["usage"]["spend"]["month"]["usd"], 42.5)
        self.assertEqual(result["usage"]["budget"]["percent"], 85)
        self.assertEqual(result["usage"]["limits"][0]["percent"], 92)
        self.assertEqual([row["name"] for row in result["usage"]["limits"]], ["demo hour tokens", "all providers daily tokens"])
        self.assertEqual(result["usage"]["exhausted"], [{"provider": "demo", "resetAt": reset}])
        self.assertEqual(len(result["notices"]), 3)
        self.assertIn("Gateway budget 85% used", result["notices"][0]["text"])
        self.assertTrue(all(row["until"] == reset for row in result["notices"]))

    def test_openrouter_detection_mapping_and_budgets(self):
        reset = provider_usage._now_iso(time.time() + 1800)
        self.route("/api/v1/key", {"data": {
            "usage": 10, "usage_daily": 1, "usage_weekly": 2, "usage_monthly": 5,
            "limit": 20, "limit_remaining": 15, "limit_reset": "monthly",
            "budgets": [{"limit_usd": 10, "spend_usd": 8.5, "remaining_usd": 1.5,
                         "reset_interval": "daily", "resets_at": reset}]}})
        self.assertEqual(self.refresh(), 0)
        result = self.stored()
        self.assertEqual(result["reader"], "openrouter")
        spend = result["usage"]["spend"]
        self.assertEqual([spend[key]["usd"] for key in ("today", "week", "month", "total")], [1, 2, 5, 10])
        self.assertEqual(result["usage"]["budget"]["usedUsd"], 5)
        self.assertEqual(result["usage"]["limits"][0]["name"], "daily budget")
        self.assertEqual(result["notices"][0]["text"], f"Gateway limit daily budget 85% used, resets {time.strftime('%H:%M UTC', time.gmtime(time.time() + 1800))}.")

    def test_litellm_detection_and_mapping(self):
        reset = provider_usage._now_iso(time.time() + 2000)
        self.route("/api/key/info", {"info": {"spend": 4.25, "max_budget": 10,
                                              "budget_duration": "30d", "budget_reset_at": reset}})
        self.assertEqual(self.refresh(), 0)
        result = self.stored()
        self.assertEqual(result["reader"], "litellm")
        self.assertEqual(result["usage"]["spend"]["period"]["usd"], 4.25)
        self.assertEqual(result["usage"]["spend"]["period"]["label"], "30d")
        self.assertEqual(result["usage"]["budget"]["limitUsd"], 10)

    def test_detection_order_and_fallthrough_on_404(self):
        self.route("/api/v1/me/status", {"error": "missing"}, 404)
        self.route("/api/v1/key", {"data": {"usage_daily": 2}})
        self.route("/api/key/info", {"info": {"spend": 9}})
        self.assertEqual(self.refresh(), 0)
        self.assertEqual(self.stored()["reader"], "openrouter")
        # The stored reader fails next time, so refresh tries readers in order.
        self.route("/api/v1/key", {"error": "gone"}, 404)
        self.route("/api/v1/me/status", {"usage": {"cost": {"usedUsd": 1}}})
        self.assertEqual(self.refresh(), 0)
        self.assertEqual(self.stored()["reader"], "omniroute")

    def test_all_unauthorized_sets_unknown_and_retries_after_24_hours(self):
        for path in ("/api/v1/me/status", "/api/v1/key", "/api/key/info"):
            self.route(path, {"error": "unauthorized"}, 401)
        self.assertEqual(self.refresh(), 0)
        value = self.stored()
        self.assertIsNone(value["reader"])
        self.assertIsNone(value["usage"])
        self.assertFalse(provider_usage.refresh_due(self.home, None, now=time.time() + 3600))
        self.assertTrue(provider_usage.refresh_due(self.home, None, now=time.time() + 90000))

    def test_redirect_is_refused(self):
        self.route("/api/v1/me/status", "http://127.0.0.1/redirected", 302)
        self.route("/api/v1/key", {"error": "missing"}, 404)
        self.route("/api/key/info", {"info": {"spend": 3}})
        self.assertEqual(self.refresh(), 0)
        self.assertEqual(self.stored()["reader"], "litellm")

    def test_oversized_response_and_invalid_json_fall_through(self):
        self.route("/api/v1/me/status", b"{" + b" " * (1024 * 1024 + 10), 200)
        self.route("/api/v1/key", b"not-json", 200)
        self.route("/api/key/info", {"info": {"spend": 7}})
        self.assertEqual(self.refresh(), 0)
        self.assertEqual(self.stored()["reader"], "litellm")

    def test_omniroute_session_row_with_and_without_list_price(self):
        self.route("/api/v1/me/status", {"usage": {"cost": {"usedUsd": 1}}})
        self.route("/api/v1/me/sessions?clientSessionId=session-123", {"sessions": [
            {"clientSessionId": SESSION, "costUsd": 2, "listPriceUsd": 3, "unpricedCount": 1,
             "requestCount": 4, "errorCount": 1, "tokens": {"total": 50}}]})
        self.assertEqual(self.refresh(SESSION), 0)
        session = self.stored()["sessions"][SESSION]
        self.assertEqual((session["usd"], session["listUsd"]), (2, 3))
        self.route("/api/v1/me/sessions?clientSessionId=session-123", {"sessions": [
            {"clientSessionId": SESSION, "costUsd": 8, "requestCount": 2, "tokens": {"total": 20}}]})
        self.assertEqual(self.refresh(SESSION), 0)
        session = self.stored()["sessions"][SESSION]
        self.assertIsNone(session["usd"])
        self.assertEqual(session["listUsd"], 8)

    def test_session_request_failure_only_omits_session(self):
        self.route("/api/v1/me/status", {"usage": {"cost": {"usedUsd": 1}}})
        self.route("/api/v1/me/sessions", {"error": "unavailable"}, 500)
        self.assertEqual(self.refresh(SESSION), 0)
        self.assertEqual(self.stored()["reader"], "omniroute")
        self.assertNotIn(SESSION, self.stored()["sessions"])

    def test_every_refresh_due_case_and_preference_off(self):
        self.assertFalse(provider_usage.refresh_due(self.home, None, now=100)) if False else None
        path = provider_usage._store_path(self.home)
        self.assertTrue(provider_usage.refresh_due(self.home, None, now=100))
        self.route("/api/v1/me/status", {"usage": {"cost": {"usedUsd": 1}}})
        self.assertEqual(self.refresh(), 0)
        stored = self.stored()
        stored["startedAt"] = None
        path = provider_usage._store_path(self.home)
        path.write_text(json.dumps(stored), encoding="utf-8")
        fixed_now = provider_usage._timestamp(stored["fetchedAt"]) or time.time()
        self.assertFalse(provider_usage.refresh_due(self.home, None, now=fixed_now + 10))
        self.assertTrue(provider_usage.refresh_due(self.home, SESSION, now=fixed_now + 30))
        stored["sessions"][SESSION] = {"usd": 1, "listUsd": 1, "fetchedAt": stored["fetchedAt"]}
        stored["startedAt"] = None
        path.write_text(json.dumps(stored), encoding="utf-8")
        self.assertFalse(provider_usage.refresh_due(self.home, SESSION, now=fixed_now + 30))
        self.assertTrue(provider_usage.refresh_due(self.home, None, now=fixed_now + 300))
        stored["startedAt"] = provider_usage._now_iso(fixed_now + 350)
        path.write_text(json.dumps(stored), encoding="utf-8")
        self.assertFalse(provider_usage.refresh_due(self.home, None, now=fixed_now + 355))
        self.set_gateway(base="http://127.0.0.1:65530/other")
        self.assertTrue(provider_usage.refresh_due(self.home, None, now=fixed_now + 355))
        (self.claude / "settings.json").write_text(json.dumps({"env": {
            "ANTHROPIC_BASE_URL": self.base, "ANTHROPIC_AUTH_TOKEN": FAKE_KEY}}), encoding="utf-8")
        (self.claude / "agentfleet").mkdir(exist_ok=True)
        (self.claude / "agentfleet" / "config.json").write_text(json.dumps({"providerUsage": False}), encoding="utf-8")
        self.assertFalse(provider_usage.refresh_due(self.home, None))

    def test_gateway_change_invalidates_load_and_subscription_has_no_gateway(self):
        self.route("/api/v1/me/status", {"usage": {"cost": {"usedUsd": 1}}})
        self.assertEqual(self.refresh(), 0)
        self.assertIsNotNone(provider_usage.load(self.home))
        self.set_gateway(base="http://127.0.0.1:65530/other")
        self.assertIsNone(provider_usage.load(self.home))
        self.set_gateway(base="https://api.anthropic.com")
        self.assertIsNone(provider_usage.gateway(self.home))
        self.assertEqual(provider_usage.refresh(self.home, None), 1)
        (self.claude / "settings.json").write_text(json.dumps({"env": {"ANTHROPIC_AUTH_TOKEN": FAKE_KEY}}), encoding="utf-8")
        self.assertIsNone(provider_usage.gateway(self.home))

    @unittest.skipUnless(os.name == "posix", "POSIX file permissions")
    def test_file_mode_is_private(self):
        self.route("/api/v1/me/status", {"usage": {"cost": {"usedUsd": 1}}})
        self.assertEqual(self.refresh(), 0)
        self.assertEqual(provider_usage._store_path(self.home).stat().st_mode & 0o777, 0o600)

    def test_key_never_in_file_or_cli_output(self):
        self.route("/api/v1/me/status", {"usage": {"cost": {"usedUsd": 1}}})
        self.assertEqual(self.refresh(), 0)
        self.assertNotIn(FAKE_KEY.encode(), provider_usage._store_path(self.home).read_bytes())
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.root), "LANG": "C.UTF-8",
               "PYTHONDONTWRITEBYTECODE": "1"}
        proc = subprocess.run([sys.executable, "-B", str(SCRIPT), "--refresh", "--home", str(self.home)],
                              env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        self.assertNotIn(FAKE_KEY.encode(), proc.stdout + proc.stderr)
        self.assertEqual(proc.returncode, 0)

    def test_exhausted_only_when_every_account_quota_is_exhausted(self):
        reset = provider_usage._now_iso(time.time() + 1000)
        self.route("/api/v1/me/status", {"usage": {"cost": {}}, "accountQuotas": [
            {"provider": "demo", "quotas": {"a": {"usedPercentage": 100, "resetAt": reset}}},
            {"provider": "demo", "quotas": {"a": {"remainingPercentage": 0, "resetAt": reset}}},
            {"provider": "partial", "quotas": {"a": {"usedPercentage": 100}}},
            {"provider": "partial", "quotas": {"a": {"usedPercentage": 20}}}]})
        self.assertEqual(self.refresh(), 0)
        self.assertEqual(self.stored()["usage"]["exhausted"], [{"provider": "demo", "resetAt": reset}])

    def test_only_twenty_most_recent_sessions_are_retained(self):
        now = time.time()
        sessions = {f"session-{index:02d}": {"usd": index, "fetchedAt": provider_usage._now_iso(now + index)}
                    for index in range(25)}
        self.assertEqual(len(provider_usage._clean_sessions(sessions)), 20)

    def test_invalid_gateway_url_and_file_symlink_are_refused(self):
        self.set_gateway(base="http://example.invalid/path")
        self.assertIsNone(provider_usage.gateway(self.home))
        self.set_gateway()
        target = self.root / "target"
        target.write_text("{}", encoding="utf-8")
        path = provider_usage._store_path(self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target)
        self.route("/api/v1/me/status", {"usage": {"cost": {"usedUsd": 1}}})
        self.assertEqual(self.refresh(), 1)
        self.assertEqual(target.read_text(encoding="utf-8"), "{}")


if __name__ == "__main__":
    unittest.main()
