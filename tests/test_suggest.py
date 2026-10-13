#!/usr/bin/env python3
# claude-agents-config:managed
"""Tests for fleet_suggest capability scanner and catalog."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.dont_write_bytecode = True


def dir_link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as error:
        if os.name != "nt" or getattr(error, "winerror", None) != 1314:
            raise
        import _winapi
        _winapi.CreateJunction(str(target), str(link))


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import fleet_suggest  # noqa: E402


class FleetSuggestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fleet_suggest_test_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

        # Setup sandbox Claude home
        self.home = self.root / "home"
        self.home.mkdir(parents=True)
        self.claude_dir = self.home / ".claude"
        self.claude_dir.mkdir(parents=True)

        # Create marketplaces directory and official marketplace copy
        self.official_market_dir = self.claude_dir / "plugins" / "marketplaces" / "claude-plugins-official" / ".claude-plugin"
        self.official_market_dir.mkdir(parents=True)
        # Seed official marketplace with test plugins
        self.official_marketplace_json = {
            "name": "claude-plugins-official",
            "plugins": [
                {"name": "typescript-lsp", "description": "TypeScript LSP"},
                {"name": "pyright-lsp", "description": "Python Pyright LSP"},
                {"name": "php-lsp", "description": "PHP Intelephense LSP"},
                {"name": "laravel-boost", "description": "Laravel Boost MCP server"},
                {"name": "frontend-design", "description": "Frontend Design Assistant"},
                {"name": "modern-web-guidance", "description": "Modern Web Guidance"},
                {"name": "cloud-sql-postgresql", "description": "PostgreSQL database integration"},
                {"name": "stripe", "description": "Stripe API Integration"},
                {"name": "context7", "description": "Context7 Docs Lookup MCP server"},
                {"name": "playwright", "description": "Playwright Testing MCP server"},
                {"name": "security-guidance", "description": "Security Guidance"},
                {"name": "claude-security", "description": "Claude Security Scanner"},
            ]
        }
        (self.official_market_dir / "marketplace.json").write_text(
            json.dumps(self.official_marketplace_json, indent=2), encoding="utf-8"
        )

        # Agentfleet mods marketplace
        self.mods_market_dir = self.claude_dir / "agentfleet" / "mods" / ".claude-plugin"
        self.mods_market_dir.mkdir(parents=True)
        self.mods_marketplace_json = {
            "name": "agentfleet",
            "plugins": [
                {"name": "token-weather", "description": "Token Weather context forecast"},
                {"name": "fast-jev-compaction", "description": "Fast Jev Compaction mod"},
                {"name": "fleet-status", "description": "Fleet status mod"},
            ]
        }
        (self.mods_market_dir / "marketplace.json").write_text(
            json.dumps(self.mods_marketplace_json, indent=2), encoding="utf-8"
        )

        # Installed plugins file (empty initially)
        self.installed_plugins_file = self.claude_dir / "plugins" / "installed_plugins.json"
        self.installed_plugins_file.parent.mkdir(parents=True, exist_ok=True)
        self.installed_plugins_file.write_text(
            json.dumps({"version": 2, "plugins": {}}, indent=2), encoding="utf-8"
        )

    def test_catalog_schema_and_integrity(self):
        catalog = fleet_suggest.load_catalog(ROOT / "scripts")
        self.assertEqual(catalog.get("format"), "agentfleet.suggestions-catalog.v1")
        self.assertTrue(catalog.get("version"))
        entries = catalog.get("entries", [])
        self.assertGreaterEqual(len(entries), 30)

        valid_kinds = {"plugin", "mod", "hook", "create-skill", "claude-md"}
        seen_ids = set()

        for e in entries:
            eid = e.get("id")
            self.assertTrue(eid, "Entry missing id")
            self.assertNotIn(eid, seen_ids, f"Duplicate entry id: {eid}")
            seen_ids.add(eid)

            kind = e.get("kind")
            self.assertIn(kind, valid_kinds, f"Invalid kind: {kind} in {eid}")

            for field in ("id", "kind", "title", "why", "match", "install", "source", "access"):
                self.assertIn(field, e, f"Missing required field {field} in {eid}")

            if kind in ("plugin", "mod"):
                self.assertIsInstance(e["install"], dict, f"Install must be dict for {eid}")
                self.assertIn("plugin", e["install"])
                self.assertIn("@", e["install"]["plugin"])
            else:
                self.assertIsNone(e["install"], f"Install must be None for create kind {eid}")
                self.assertIn("template", e, f"Missing template for {eid}")

    def test_catalog_locations(self):
        # 1. Repo layout (../config/suggestions.json relative to scripts/)
        repo_cat = fleet_suggest.load_catalog(ROOT / "scripts")
        self.assertEqual(repo_cat.get("format"), "agentfleet.suggestions-catalog.v1")
        self.assertGreater(len(repo_cat.get("entries", [])), 0)

        # 2. Installed layout (fleet-suggestions.json next to script)
        fake_installed_dir = self.root / "installed"
        fake_installed_dir.mkdir()
        fake_cat_file = fake_installed_dir / "fleet-suggestions.json"
        fake_payload = {"format": "agentfleet.suggestions-catalog.v1", "version": "99.9.9", "entries": []}
        fake_cat_file.write_text(json.dumps(fake_payload), encoding="utf-8")
        installed_cat = fleet_suggest.load_catalog(fake_installed_dir)
        self.assertEqual(installed_cat.get("version"), "99.9.9")

    def test_sandbox_laravel_project(self):
        proj = self.root / "laravel_app"
        proj.mkdir()
        (proj / "artisan").write_text("#!/usr/bin/env php\n", encoding="utf-8")
        composer = {
            "name": "test/laravel-app",
            "require": {
                "php": "^8.2",
                "laravel/framework": "^11.0"
            }
        }
        (proj / "composer.json").write_text(json.dumps(composer), encoding="utf-8")

        suggs = fleet_suggest.suggest(self.home, proj)
        ids = {s["id"] for s in suggs}
        self.assertIn("laravel-boost", ids)
        self.assertIn("php-lsp", ids)

    def test_sandbox_nextjs_project(self):
        proj = self.root / "next_app"
        proj.mkdir()
        (proj / "tsconfig.json").write_text("{}", encoding="utf-8")
        pkg = {
            "name": "next-app",
            "dependencies": {
                "next": "14.0.0",
                "react": "^18.2.0",
                "react-dom": "^18.2.0"
            },
            "devDependencies": {
                "typescript": "^5.0.0"
            }
        }
        (proj / "package.json").write_text(json.dumps(pkg), encoding="utf-8")

        suggs = fleet_suggest.suggest(self.home, proj)
        ids = {s["id"] for s in suggs}
        self.assertIn("typescript-lsp", ids)
        self.assertIn("frontend-design", ids)
        self.assertIn("modern-web-guidance", ids)

    def test_sandbox_python_fastapi_compose_project(self):
        proj = self.root / "python_api"
        proj.mkdir()
        pyproject = """[project]
name = "api"
dependencies = [
    "fastapi>=0.110.0",
    "uvicorn",
    "psycopg2-binary"
]
"""
        (proj / "pyproject.toml").write_text(pyproject, encoding="utf-8")
        compose = """version: '3.8'
services:
  db:
    image: postgres:16-alpine
    ports:
      - "5432:5432"
"""
        (proj / "docker-compose.yml").write_text(compose, encoding="utf-8")

        suggs = fleet_suggest.suggest(self.home, proj)
        ids = {s["id"] for s in suggs}
        self.assertIn("pyright-lsp", ids)
        self.assertIn("cloud-sql-postgresql", ids)

    def test_sandbox_empty_dir_suggests_claude_md(self):
        empty_proj = self.root / "empty_dir"
        empty_proj.mkdir()
        suggs = fleet_suggest.suggest(self.home, empty_proj)
        ids = {s["id"] for s in suggs}
        self.assertIn("init-claude-md", ids)

        # If CLAUDE.md is created, init-claude-md is omitted
        (empty_proj / "CLAUDE.md").write_text("# Project Guide\n", encoding="utf-8")
        suggs2 = fleet_suggest.suggest(self.home, empty_proj)
        ids2 = {s["id"] for s in suggs2}
        self.assertNotIn("init-claude-md", ids2)

    def test_eligibility_installed_and_unavailable_and_dismissed(self):
        proj = self.root / "test_elig"
        proj.mkdir()
        (proj / "tsconfig.json").write_text("{}", encoding="utf-8")
        (proj / "package.json").write_text(json.dumps({"dependencies": {"typescript": "^5.0"}}), encoding="utf-8")

        # Initially typescript-lsp is suggested
        suggs = fleet_suggest.suggest(self.home, proj)
        self.assertIn("typescript-lsp", {s["id"] for s in suggs})

        # 1. Mark as installed in installed_plugins.json -> should be skipped
        installed_data = {
            "version": 2,
            "plugins": {
                "typescript-lsp@claude-plugins-official": [{"scope": "user"}]
            }
        }
        self.installed_plugins_file.write_text(json.dumps(installed_data), encoding="utf-8")
        suggs_after_install = fleet_suggest.suggest(self.home, proj)
        self.assertNotIn("typescript-lsp", {s["id"] for s in suggs_after_install})

        # 2. Unavailable plugin (not in marketplace)
        # Create package with stripe
        (proj / "package.json").write_text(json.dumps({"dependencies": {"stripe": "^1.0"}}), encoding="utf-8")
        # Stripe is in marketplace -> suggested
        suggs_stripe = fleet_suggest.suggest(self.home, proj)
        self.assertIn("stripe", {s["id"] for s in suggs_stripe})
        # Remove stripe from marketplace
        self.official_marketplace_json["plugins"] = [
            p for p in self.official_marketplace_json["plugins"] if p["name"] != "stripe"
        ]
        (self.official_market_dir / "marketplace.json").write_text(
            json.dumps(self.official_marketplace_json, indent=2), encoding="utf-8"
        )
        suggs_stripe_removed = fleet_suggest.suggest(self.home, proj)
        self.assertNotIn("stripe", {s["id"] for s in suggs_stripe_removed})

        # 3. Dismissed ID
        with io.StringIO() as buf, contextlib.redirect_stdout(buf):
            fleet_suggest.main(["--home", str(self.home), "--project", str(proj), "--dismiss", "init-claude-md"])
        suggs_dismissed = fleet_suggest.suggest(self.home, proj)
        self.assertNotIn("init-claude-md", {s["id"] for s in suggs_dismissed})

    def test_ranking_strength_and_kind_order(self):
        proj = self.root / "ranked_proj"
        proj.mkdir()
        # Explicit file + dep -> strength 3
        (proj / "tsconfig.json").write_text("{}", encoding="utf-8")
        (proj / "package.json").write_text(json.dumps({"dependencies": {"typescript": "^5.0"}}), encoding="utf-8")
        # Missing CLAUDE.md -> strength 1

        suggs = fleet_suggest.suggest(self.home, proj)
        self.assertGreaterEqual(len(suggs), 2)
        # typescript-lsp (plugin, strength 3) must precede init-claude-md (claude-md, strength 1)
        ids = [s["id"] for s in suggs]
        self.assertIn("typescript-lsp", ids)
        self.assertIn("init-claude-md", ids)
        self.assertLess(ids.index("typescript-lsp"), ids.index("init-claude-md"))

    def test_notice_hook_once_per_catalog_version(self):
        proj = self.root / "notice_proj"
        proj.mkdir()
        (proj / "artisan").write_text("#!/usr/bin/env php\n", encoding="utf-8")
        (proj / "composer.json").write_text(json.dumps({"require": {"laravel/framework": "^11.0"}}), encoding="utf-8")

        # First notice call emits text
        msg1 = fleet_suggest.notice(str(proj), self.home)
        self.assertIsNotNone(msg1)
        self.assertIn("AgentFleet:", msg1)
        self.assertIn("match this project", msg1)

        # Verify suggestions.json fields
        store_file = self.home / ".claude" / "agentfleet" / "suggestions.json"
        self.assertTrue(store_file.is_file())
        store = json.loads(store_file.read_text(encoding="utf-8"))
        p_rec = store["projects"][str(proj.resolve())]
        self.assertEqual(p_rec["name"], proj.name)
        self.assertGreater(p_rec["pending"], 0)
        self.assertEqual(p_rec["notified"], "2026.10.1")
        self.assertIsNone(p_rec["seen"])
        self.assertTrue(p_rec["updated"])

        # Second notice call must return None (already notified)
        msg2 = fleet_suggest.notice(str(proj), self.home)
        self.assertIsNone(msg2)

    def test_stale_store_lock_is_recovered(self):
        proj = self.root / "stale_lock_proj"
        proj.mkdir()
        lock = self.home / ".claude" / "agentfleet" / ".suggestions.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("", encoding="utf-8")
        old = time.time() - fleet_suggest.STALE_LOCK_SECONDS - 5
        os.utime(lock, (old, old))
        # A lock left by a killed process must not block every later prompt.
        self.assertIsNotNone(fleet_suggest.notice(str(proj), self.home))
        self.assertFalse(lock.exists())

    def _old_lock(self, content: str) -> Path:
        lock = self.home / ".claude" / "agentfleet" / ".suggestions.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(content, encoding="utf-8")
        old = time.time() - fleet_suggest.STALE_LOCK_SECONDS - 5
        os.utime(lock, (old, old))
        return lock

    def test_old_lock_with_live_owner_is_not_stolen(self):
        lock = self._old_lock(json.dumps({"pid": os.getpid(), "token": "held"}))
        with self.assertRaises(TimeoutError):
            with fleet_suggest._suggestions_lock(self.home):
                self.fail("lock was taken from a live owner")
        self.assertEqual(json.loads(lock.read_text(encoding="utf-8"))["token"], "held")

    def test_old_lock_with_dead_owner_is_recovered(self):
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        lock = self._old_lock(json.dumps({"pid": dead.pid, "token": "gone"}))
        with fleet_suggest._suggestions_lock(self.home):
            self.assertNotEqual(json.loads(lock.read_text(encoding="utf-8"))["token"], "gone")
        self.assertFalse(lock.exists())

    def test_very_old_lock_with_reused_live_pid_is_recovered(self):
        lock = self.home / ".claude" / "agentfleet" / ".suggestions.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(json.dumps({"pid": os.getpid(), "token": "reused"}), encoding="utf-8")
        old = time.time() - fleet_suggest.LOCK_MAX_SECONDS - 5
        os.utime(lock, (old, old))
        with fleet_suggest._suggestions_lock(self.home):
            self.assertNotEqual(json.loads(lock.read_text(encoding="utf-8"))["token"], "reused")
        self.assertFalse(lock.exists())

    def test_release_keeps_lock_owned_by_another_holder(self):
        lock = self.home / ".claude" / "agentfleet" / ".suggestions.lock"
        with fleet_suggest._suggestions_lock(self.home):
            lock.write_text(json.dumps({"pid": os.getpid(), "token": "other"}), encoding="utf-8")
        self.assertEqual(json.loads(lock.read_text(encoding="utf-8"))["token"], "other")

    def test_cli_seen_dismiss_forget(self):
        proj = self.root / "cli_proj"
        proj.mkdir()
        (proj / "artisan").write_text("#!/usr/bin/env php\n", encoding="utf-8")

        # Initial suggest
        with io.StringIO() as buf, contextlib.redirect_stdout(buf):
            fleet_suggest.main(["--home", str(self.home), "--project", str(proj)])
        store_file = self.home / ".claude" / "agentfleet" / "suggestions.json"
        store = json.loads(store_file.read_text(encoding="utf-8"))
        p_rec = store["projects"][str(proj.resolve())]
        self.assertEqual(p_rec["seen"], "2026.10.1")
        self.assertEqual(p_rec["pending"], 0)
        self.assertEqual(p_rec["new"], [])
        self.assertTrue(p_rec["shown"])

        # Run --seen
        with io.StringIO() as buf, contextlib.redirect_stdout(buf):
            fleet_suggest.main(["--home", str(self.home), "--project", str(proj), "--seen"])
        store = json.loads(store_file.read_text(encoding="utf-8"))
        self.assertEqual(store["projects"][str(proj.resolve())]["seen"], "2026.10.1")

        # Run --dismiss
        with io.StringIO() as buf, contextlib.redirect_stdout(buf):
            fleet_suggest.main(["--home", str(self.home), "--project", str(proj), "--dismiss", "init-claude-md"])
        store = json.loads(store_file.read_text(encoding="utf-8"))
        self.assertIn("init-claude-md", store["projects"][str(proj.resolve())]["dismissed"])

        # Seed usage directory
        usage_dir = self.home / ".claude" / "agentfleet" / "usage"
        usage_dir.mkdir(parents=True, exist_ok=True)
        (usage_dir / "test.json").write_text("{}", encoding="utf-8")

        # Run --forget
        with io.StringIO() as buf, contextlib.redirect_stdout(buf):
            fleet_suggest.main(["--home", str(self.home), "--forget"])
        self.assertFalse(usage_dir.exists())
        self.assertFalse(store_file.exists())

    def test_usage_driven_create_skill_and_formatter_hook(self):
        proj = self.root / "usage_proj"
        proj.mkdir()
        (proj / "CLAUDE.md").write_text("# CLAUDE.md\n", encoding="utf-8")

        key = fleet_suggest.hashlib.sha256(str(proj.resolve()).encode("utf-8")).hexdigest()[:16]
        usage_dir = self.home / ".claude" / "agentfleet" / "usage"
        usage_dir.mkdir(parents=True, exist_ok=True)
        usage_data = {
            "format": "agentfleet.usage.v1",
            "project": str(proj),
            "updated": "2026-10-11T00:00:00Z",
            "bash": {
                "php artisan test": {"count": 15, "sessions": 4, "last": "2026-10-11T00:00:00Z"},
                "npx prettier": {"count": 6, "sessions": 2, "last": "2026-10-11T00:00:00Z"}
            },
            "commands": {
                "/compact": {"count": 5}
            }
        }
        (usage_dir / f"{key}.json").write_text(json.dumps(usage_data), encoding="utf-8")

        suggs = fleet_suggest.suggest(self.home, proj)
        ids = {s["id"] for s in suggs}
        # create-skill for artisan test
        self.assertIn("create-skill-test", ids)
        # format-after-edit triggered by bash usage >= 5
        self.assertIn("format-after-edit", ids)
        # mods triggered by /compact counts
        self.assertIn("token-weather", ids)
        self.assertIn("fast-jev-compaction", ids)

    def test_env_values_never_read(self):
        proj = self.root / "secret_proj"
        proj.mkdir()

        sentinel_secret = "SENTINEL_NEVER_LEAK_SECRET_VAL_999888"
        (proj / ".env").write_text(f"API_KEY={sentinel_secret}\nDATABASE_URL=postgres://...\n", encoding="utf-8")
        (proj / ".env.example").write_text("STRIPE_KEY=\nDATABASE_URL=\n", encoding="utf-8")

        signals = fleet_suggest.scan(proj)
        self.assertTrue(signals["has_env"])
        self.assertIn("STRIPE_KEY", signals["env_keys"])
        self.assertNotIn(sentinel_secret, signals["env_keys"])

        suggs = fleet_suggest.suggest(self.home, proj)
        suggs_json = json.dumps(suggs)
        self.assertNotIn(sentinel_secret, suggs_json)

        # CLI run output test
        with io.StringIO() as buf, contextlib.redirect_stdout(buf):
            fleet_suggest.main(["--home", str(self.home), "--project", str(proj)])
            cli_out = buf.getvalue()
        self.assertNotIn(sentinel_secret, cli_out)
        store_file = self.home / ".claude" / "agentfleet" / "suggestions.json"
        store_text = store_file.read_text(encoding="utf-8")
        self.assertNotIn(sentinel_secret, store_text)

    def test_scan_bounds_and_limits(self):
        proj = self.root / "bounded_proj"
        proj.mkdir()

        # 1. Skipped directories
        node_modules = proj / "node_modules" / "foo"
        node_modules.mkdir(parents=True)
        (node_modules / "deep_file.js").write_text("console.log(1);", encoding="utf-8")

        # 2. Deep tree (depth > 3)
        deep = proj / "a" / "b" / "c" / "d" / "e"
        deep.mkdir(parents=True)
        (deep / "too_deep.txt").write_text("hello", encoding="utf-8")

        # 3. Big file (>256 KB)
        big_file = proj / "package.json"
        big_file.write_text(" " * (300 * 1024), encoding="utf-8")

        signals = fleet_suggest.scan(proj)
        self.assertFalse(any("node_modules" in f for f in signals["files"]))
        self.assertFalse(any("too_deep.txt" in f for f in signals["files"]))
        # big package.json skipped from manifest parsing
        self.assertEqual(len(signals["deps"]), 0)

    def test_runtime_under_300ms(self):
        proj = self.root / "perf_proj"
        proj.mkdir()
        for i in range(20):
            (proj / f"file_{i}.txt").write_text(f"content {i}", encoding="utf-8")
        (proj / "package.json").write_text(json.dumps({"dependencies": {"react": "18.0"}}), encoding="utf-8")

        start = time.perf_counter()
        res = fleet_suggest.notice(str(proj), self.home)
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        self.assertIsNotNone(res)
        self.assertLess(elapsed_ms, 300.0, f"notice took {elapsed_ms:.2f} ms, expected < 300 ms")

    def test_breadth_first_scan_finds_root_manifests_with_huge_subdir(self):
        proj = self.root / "bfs_proj"
        proj.mkdir()
        # Early-sorted directory holding 3000 files
        huge_sub = proj / "00_huge"
        huge_sub.mkdir()
        for i in range(3000):
            (huge_sub / f"item_{i:04d}.txt").write_text("x", encoding="utf-8")

        # Root manifests that sort AFTER 00_huge
        pkg = {"dependencies": {"react": "^18.2.0"}}
        (proj / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
        composer = {"require": {"laravel/framework": "^11.0"}}
        (proj / "composer.json").write_text(json.dumps(composer), encoding="utf-8")

        signals = fleet_suggest.scan(proj)
        self.assertIn("package.json", signals["files"])
        self.assertIn("composer.json", signals["files"])
        self.assertIn("react", signals["deps"])
        self.assertIn("laravel/framework", signals["deps"])

    def test_symlinked_project_usage_key_and_suggestions_store(self):
        real_proj = self.root / "real_project"
        real_proj.mkdir()
        (real_proj / "artisan").write_text("#!/usr/bin/env php\n", encoding="utf-8")

        symlink_proj = self.root / "symlink_project"
        dir_link(symlink_proj, real_proj)

        # Write usage file using real path's sha256[:16]
        real_resolved = str(real_proj.resolve())
        usage_key = fleet_suggest.hashlib.sha256(real_resolved.encode("utf-8")).hexdigest()[:16]
        usage_dir = self.home / ".claude" / "agentfleet" / "usage"
        usage_dir.mkdir(parents=True, exist_ok=True)
        usage_data = {
            "format": "agentfleet.usage.v1",
            "project": real_resolved,
            "updated": "2026-10-11T00:00:00Z",
            "bash": {
                "php artisan test": {"count": 15, "sessions": 4, "last": "2026-10-11T00:00:00Z"}
            }
        }
        (usage_dir / f"{usage_key}.json").write_text(json.dumps(usage_data), encoding="utf-8")

        # Scanning via symlink_proj must find the usage file and derive suggestion
        suggs = fleet_suggest.suggest(self.home, symlink_proj)
        ids = {s["id"] for s in suggs}
        self.assertIn("create-skill-test", ids)

        # notice via symlink_proj must record under real_resolved in suggestions.json
        line = fleet_suggest.notice(str(symlink_proj), self.home)
        self.assertIsNotNone(line)
        store_file = self.home / ".claude" / "agentfleet" / "suggestions.json"
        store = json.loads(store_file.read_text(encoding="utf-8"))
        self.assertIn(real_resolved, store["projects"])
        self.assertEqual(store["projects"][str(symlink_proj)], store["projects"][real_resolved])

    def test_pending_notice_and_mark_notified(self):
        proj = self.root / "pending_proj"
        proj.mkdir()
        (proj / "artisan").write_text("#!/usr/bin/env php\n", encoding="utf-8")
        (proj / "composer.json").write_text(json.dumps({"require": {"laravel/framework": "^11.0"}}), encoding="utf-8")

        store_file = self.home / ".claude" / "agentfleet" / "suggestions.json"

        # 1. pending_notice returns message but does NOT record notified
        line1 = fleet_suggest.pending_notice(str(proj), self.home)
        self.assertIsNotNone(line1)
        self.assertIn("AgentFleet:", line1)

        # store should either not exist or have notified is None
        if store_file.exists():
            store = json.loads(store_file.read_text(encoding="utf-8"))
            p_rec = store.get("projects", {}).get(str(proj.resolve()), {})
            self.assertIsNone(p_rec.get("notified"))

        # 2. calling pending_notice again still returns the message (not burned)
        line2 = fleet_suggest.pending_notice(str(proj), self.home)
        self.assertEqual(line1, line2)

        # 3. mark_notified records notified state
        fleet_suggest.mark_notified(str(proj), self.home)
        self.assertTrue(store_file.exists())
        store = json.loads(store_file.read_text(encoding="utf-8"))
        p_rec = store["projects"][str(proj.resolve())]
        self.assertEqual(p_rec["notified"], "2026.10.1")

        # 4. subsequent pending_notice and notice both return None
        self.assertIsNone(fleet_suggest.pending_notice(str(proj), self.home))
        self.assertIsNone(fleet_suggest.notice(str(proj), self.home))

    def test_listing_json_marks_shown_and_clears_band(self):
        proj = self.root / "listed"
        proj.mkdir()
        self.assertIsNotNone(fleet_suggest.notice(str(proj), self.home))
        with io.StringIO() as buf, contextlib.redirect_stdout(buf):
            fleet_suggest.main(["--home", str(self.home), "--project", str(proj), "--json"])
            ids = {s["id"] for s in json.loads(buf.getvalue())["suggestions"]}
        entry = fleet_suggest.load_suggestions_store(self.home)["projects"][str(proj)]
        self.assertEqual(set(entry["shown"]), ids)
        self.assertEqual(entry["pending"], 0)
        self.assertEqual(entry["new"], [])
        self.assertEqual(entry["seen"], fleet_suggest.load_catalog()["version"])
        self.assertIsNone(fleet_suggest.notice(str(proj), self.home))

    def test_notice_recomputes_new_pending_and_installed_items(self):
        proj = self.root / "rescanned"
        proj.mkdir()
        fleet_suggest.notice(str(proj), self.home)
        (proj / "tsconfig.json").write_text("{}")
        self.assertIsNotNone(fleet_suggest.notice(str(proj), self.home))
        entry = fleet_suggest.load_suggestions_store(self.home)["projects"][str(proj)]
        self.assertEqual(entry["pending"], len(fleet_suggest.suggest(self.home, proj)))
        self.assertLessEqual(len(entry["new"]), 3)
        self.assertIn("typescript-lsp", {s["id"] for s in entry["new"]})
        before = entry["pending"]
        self.installed_plugins_file.write_text(json.dumps({"version": 2, "plugins": {
            "typescript-lsp@claude-plugins-official": [{"scope": "user"}]}}))
        fleet_suggest.notice(str(proj), self.home)
        entry = fleet_suggest.load_suggestions_store(self.home)["projects"][str(proj)]
        self.assertEqual(entry["pending"], before - 1)
        self.assertNotIn("typescript-lsp", {s["id"] for s in entry["new"]})

    def test_route_suppression_and_once_per_id_set(self):
        proj = self.root / "routed"
        proj.mkdir()
        (self.claude_dir / "fleet.json").write_text(json.dumps({"lanes": {"implement": {"model": "haiku"}}}))
        settings = self.claude_dir / "settings.json"
        settings.write_text(json.dumps({"enabledPlugins": {"fleet-status@agentfleet": True}}))
        (self.claude_dir / "agentfleet").mkdir(parents=True, exist_ok=True)
        (self.claude_dir / "agentfleet" / "config.json").write_text(json.dumps({"format": "agentfleet.config.v1", "workStyle": "fleet"}))
        def route():
            result = subprocess.run([sys.executable, "-B", str(SCRIPTS / "route-to-fleet.py")],
                                    input=json.dumps({"cwd": str(proj), "prompt": "implement a new API endpoint for users"}),
                                    text=True, capture_output=True,
                                    env={**os.environ, "CLAUDE_FLEET_HOME": str(self.home)})
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout
        self.assertNotIn("AgentFleet:", route())
        entry = fleet_suggest.load_suggestions_store(self.home)["projects"][str(proj)]
        self.assertGreater(entry["pending"], 0)
        settings.write_text(json.dumps({"enabledPlugins": {"fleet-status@agentfleet": False}}))
        self.assertIn("AgentFleet:", route())
        self.assertNotIn("AgentFleet:", route())
        (proj / "tsconfig.json").write_text("{}")
        self.assertIn("AgentFleet:", route())
        self.assertNotIn("AgentFleet:", route())

    def test_sanitization_escapes_and_verbs(self):
        proj = self.root / "escape_proj"
        proj.mkdir()

        # File name containing CSI sequence (ESC[31m); Windows forbids control characters in names
        if os.name != "nt":
            dirty_file = proj / "\x1b[31mevil_component.tsx\x1b[0m"
            dirty_file.write_text("export const Evil = () => null;", encoding="utf-8")

        # .env.example with OSC 52 sequence in key name
        env_content = "\x1b]52;c;c2VjcmV0\x07STRIPE_KEY=\n"
        (proj / ".env.example").write_text(env_content, encoding="utf-8")

        signals = fleet_suggest.scan(proj)
        if os.name != "nt":
            self.assertIn("evil_component.tsx", signals["files"])
        self.assertFalse(any("\x1b" in f for f in signals["files"]))
        self.assertIn("STRIPE_KEY", signals["env_keys"])
        self.assertFalse(any("\x1b" in k for k in signals["env_keys"]))

        # Check suggestions sanitized
        suggs = fleet_suggest.suggest(self.home, proj)
        suggs_json = json.dumps(suggs)
        self.assertNotIn("\x1b", suggs_json)

        # Usage head with path traversal 'npm run ../../evil' yields safe verb 'evil'
        self.assertEqual(fleet_suggest._derive_verb("npm run ../../evil"), "evil")
        self.assertEqual(fleet_suggest._derive_verb("npm run ../../"), "")
        self.assertEqual(fleet_suggest._derive_verb("php artisan evil:cmd"), "evil-cmd")


if __name__ == "__main__":
    unittest.main()
