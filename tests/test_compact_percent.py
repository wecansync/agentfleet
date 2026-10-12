"""Global compaction percentage regressions."""
import json
import unittest

from tests.test_preferences import PreferencesTests, import_script, SCRIPTS, provider_catalog


class CompactPercentTests(unittest.TestCase):
    setUp = PreferencesTests.setUp
    gateway = PreferencesTests.gateway
    run_sync_hook = PreferencesTests.run_sync_hook
    set_percent = PreferencesTests.set_percent
    def test_range_and_learning_default(self):
        for value in (20, 95, "off"):
            self.assertTrue(provider_catalog.valid_compact_percent(value))
        for value in (19, 96, True, "70"):
            self.assertFalse(provider_catalog.valid_compact_percent(value))
        self.assertTrue(provider_catalog.load_preferences(self.home)["learn"])

    def test_settings_known_unknown_native_and_off(self):
        helper = provider_catalog.compaction_settings
        for context, expected in ((provider_catalog.NATIVE_CONTEXT_BASIS, 1000000),
                                  (200000, 200000), (2000000, 1000000),
                                  (None, None), ("unknown", None), (True, None), (99999, None)):
            self.assertEqual(helper(context, 20), {"percent": 20, "window": 1000000, "maxContext": expected})
        for percent in ("off", 19, 96):
            self.assertIsNone(helper(200000, percent))

    def test_switch_and_session_notices(self):
        self.gateway()
        # Migrating a 2.2.0 pair changes the percent and window: restart once.
        first = self.run_sync_hook("model-200k")
        self.assertEqual(json.loads(first.stdout)["systemMessage"],
                         "Auto-compact set to 70% of each model's context. Restart Claude Code to apply.")
        current = json.loads(self.settings_path.read_text())
        self.assertEqual(current["env"][provider_catalog.COMPACT_PERCENT_ENV], "70")
        self.assertEqual(current["autoCompactWindow"], 1000000)
        self.assertEqual(current["env"]["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "200000")
        self.assertEqual(self.run_sync_hook("model-200k").stdout, "")
        # After that a switch only reports a changed context cap.
        current["env"]["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = "100000"
        self.settings_path.write_text(json.dumps(current))
        self.assertEqual(json.loads(self.run_sync_hook("model-200k").stdout)["systemMessage"],
                         "Context limit for model-200k set to 200000 tokens; restart Claude Code to apply it. Auto-compact stays at 70% of each model's context.")
        self.set_percent(20)
        self.assertEqual(json.loads(self.run_sync_hook("model-200k", "SessionStart").stdout)["systemMessage"],
                         "Auto-compact set to 20% of each model's context. Restart Claude Code to apply.")

    def test_unknown_gateway_and_native_preserve_max(self):
        self.gateway()
        result = self.run_sync_hook("unlisted")
        self.assertIn("Auto-compact set to 70%", result.stdout)
        current = json.loads(self.settings_path.read_text())
        self.assertEqual(current["env"]["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "700000")
        self.assertEqual(current["autoCompactWindow"], 1000000)
        current["env"].pop("ANTHROPIC_BASE_URL")
        self.settings_path.write_text(json.dumps(current))
        self.assertEqual(self.run_sync_hook("sonnet").stdout, "")
        self.assertEqual(json.loads(self.settings_path.read_text())["env"]["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "700000")

    def test_reconcile_notices_unknown_and_off(self):
        reconcile = import_script("compact_reconcile", SCRIPTS / "fleet-reconcile.py")
        original = {"model": "small", "env": {"ANTHROPIC_BASE_URL": self.endpoint}}
        catalog = {"small": {"context_length": 200000}}
        updated, notice, window = reconcile.reconcile_context(original, catalog, self.home)
        self.assertEqual(window, 1000000)
        self.assertEqual(notice, "Auto-compact set to 70% of each model's context. Restart Claude Code to apply.")
        self.assertEqual(updated["env"][provider_catalog.COMPACT_PERCENT_ENV], "70")
        self.assertEqual(reconcile.reconcile_context(updated, catalog, self.home)[1], None)
        updated["env"]["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = "100000"
        repaired, notice, _ = reconcile.reconcile_context(updated, catalog, self.home)
        self.assertEqual(notice, "Context limit for small set to 200000 tokens; restart Claude Code to apply it. Auto-compact stays at 70% of each model's context.")
        unknown, _, _ = reconcile.reconcile_context(repaired, {"small": {}}, self.home)
        self.assertEqual(unknown["env"]["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "200000")
        missing, _, _ = reconcile.reconcile_context(original, {}, self.home)
        self.assertEqual(missing["autoCompactWindow"], 1000000)
        self.assertNotIn("CLAUDE_CODE_MAX_CONTEXT_TOKENS", missing["env"])
        native = {"model": "small", "env": {"CLAUDE_CODE_MAX_CONTEXT_TOKENS": "300000"}}
        native_updated, _, _ = reconcile.reconcile_context(native, catalog, self.home)
        self.assertEqual(native_updated["env"]["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "300000")
        self.set_percent("off")
        no_env = {"model": "small"}
        self.assertEqual(reconcile.reconcile_context(no_env, catalog, self.home), (no_env, None, None))
        self.assertEqual(reconcile.reconcile_context(original, catalog, self.home), (original, None, None))
        self.gateway()
        before = self.settings_path.read_bytes()
        self.assertEqual(self.run_sync_hook("model-200k").stdout, "")
        self.assertEqual(self.settings_path.read_bytes(), before)


del PreferencesTests
