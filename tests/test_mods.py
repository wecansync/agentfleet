#!/usr/bin/env python3
"""Hermetic integration tests for bundled Claude Code plugin registration."""
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

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
INSTALLER = ROOT / "bin" / "install.py"
MOD_ID = "fleet-status@agentfleet"
GUARD_ID = "lane-guard@agentfleet"
WEATHER_ID = "token-weather@agentfleet"
BRAIN_ID = "brain@agentfleet"
FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
state_path = root / "claude-state.json"
log_path = root / "claude-calls.jsonl"
state = json.loads(state_path.read_text())
argv = sys.argv[1:]
with log_path.open("a", encoding="utf-8") as log:
    log.write(json.dumps({"argv": argv, "env_names": sorted(os.environ), "HOME": os.environ.get("HOME")}) + "\n")

if argv == ["--version"]:
    command = "version"
elif argv[:3] == ["plugin", "marketplace", "list"]:
    command = "marketplace_list"
elif argv[:3] == ["plugin", "marketplace", "add"]:
    command = "marketplace_add"
elif argv[:3] == ["plugin", "marketplace", "remove"]:
    command = "marketplace_remove"
elif argv[:2] == ["plugin", "list"]:
    command = "plugin_list"
elif argv[:2] == ["plugin", "install"]:
    command = "plugin_install"
elif argv[:2] == ["plugin", "disable"]:
    command = "plugin_disable"
elif argv[:3] == ["plugin", "marketplace", "update"]:
    command = "marketplace_update"
elif argv[:2] == ["plugin", "update"]:
    command = "plugin_update"
else:
    sys.exit(2)

code = state.get("exit_codes", {}).get(command, 0)
if code:
    sys.exit(code)
if command == "version":
    print(state.get("version", "2.1.295 (Claude Code)"))
elif command == "marketplace_list":
    print(json.dumps(state.get("marketplaces", [])))
elif command == "marketplace_add":
    path = str(Path(argv[3]).resolve())
    markets = state.setdefault("marketplaces", [])
    if not any(item.get("name") == "agentfleet" for item in markets):
        markets.append({
            "name": "agentfleet",
            "source": {"source": "directory", "path": path},
            "path": path,
            "installLocation": path,
        })
    state_path.write_text(json.dumps(state))
elif command == "marketplace_remove":
    state["marketplaces"] = [item for item in state.get("marketplaces", []) if item.get("name") != argv[3]]
    state["plugins"] = [item for item in state.get("plugins", []) if not item.get("id", "").endswith("@agentfleet")]
    state_path.write_text(json.dumps(state))
elif command == "plugin_list":
    print(json.dumps(state.get("plugins", [])))
elif command == "plugin_install":
    plugins = state.setdefault("plugins", [])
    if not any(item.get("id") == argv[2] for item in plugins):
        plugins.append({"id": argv[2], "enabled": True})
    state_path.write_text(json.dumps(state))
elif command == "plugin_disable":
    plugins = state.setdefault("plugins", [])
    for item in plugins:
        if item.get("id") == argv[2]:
            item["enabled"] = False
    state_path.write_text(json.dumps(state))
elif command == "plugin_update":
    # Like the real CLI: take the catalog's version, keep the enabled state.
    market = next(item for item in state.get("marketplaces", []) if item.get("name") == "agentfleet")
    name = argv[2].split("@")[0]
    manifest = json.loads((Path(market["path"]) / name / ".claude-plugin" / "plugin.json").read_text())
    for item in state.get("plugins", []):
        if item.get("id") == argv[2]:
            item["version"] = manifest["version"]
    state_path.write_text(json.dumps(state))
sys.exit(0)
'''


def write_fake_claude(directory: Path) -> Path:
    script = directory / "claude"
    script.write_text(FAKE_CLAUDE, encoding="utf-8")
    script.chmod(0o755)
    if os.name == "nt":
        # PATH lookup on Windows finds claude.CMD, not the extensionless script.
        (directory / "claude.cmd").write_text(f'@"{PYTHON}" "%~dp0claude" %*\n', encoding="utf-8")
    return script


def node_dir_for_path(directory: Path, node: Path) -> Path:
    # Windows cannot create the symlink without Developer Mode; node is only
    # ever looked up on PATH, so its real directory can stand in for it.
    if os.name == "nt":
        return node.parent
    (directory / "node").symlink_to(node)
    return directory


def new_state(**overrides) -> dict:
    state = {"version": "2.1.295 (Claude Code)", "marketplaces": [], "plugins": [], "exit_codes": {}}
    state.update(overrides)
    return state


class ModInstallerTests(unittest.TestCase):
    def setUp(self):
        self.raw = tempfile.TemporaryDirectory(prefix="agentfleet mods ")
        self.addCleanup(self.raw.cleanup)
        self.root = Path(self.raw.name)
        self.fake_bin = self.root / "fake-bin"
        self.fake_bin.mkdir()
        self.claude = write_fake_claude(self.fake_bin)
        self.state_path = self.root / "claude-state.json"
        self.log_path = self.root / "claude-calls.jsonl"

        # Keep the real Claude CLI out of these tests while preserving a
        # discoverable Node executable for installer code that needs it.
        self.node_bin = self.root / "node-bin"
        self.node_bin.mkdir()
        node = shutil.which("node")
        if node:
            self.node_bin = node_dir_for_path(self.node_bin, Path(node).resolve())
        self.system_path = [path for path in ("/usr/bin", "/bin") if Path(path).is_dir()]
        self.set_fake_state()

    def set_fake_state(self, state: dict | None = None):
        self.state_path.write_text(json.dumps(state or new_state()), encoding="utf-8")
        self.log_path.write_text("", encoding="utf-8")

    def calls(self) -> list[dict]:
        if not self.log_path.is_file():
            return []
        return [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines() if line]

    def home_for(self, label: str = "home") -> Path:
        return self.root / label

    def env_for(self, home: Path, *, fake: bool = True, mods: str | None = None,
                path: str | None = None, extra_env: dict[str, str] | None = None) -> dict[str, str]:
        path_parts = ([str(self.fake_bin)] if fake else []) + [str(self.node_bin), *self.system_path]
        env = {
            "PATH": path if path is not None else os.pathsep.join(path_parts),
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "AGENTFLEET_NONINTERACTIVE": "1",
            "FAKE_CLAUDE_STATE": str(self.state_path),
            "FAKE_CLAUDE_LOG": str(self.log_path),
        }
        if os.name == "nt":
            # cmd.exe needs these to launch the claude.cmd shim.
            env.update({name: os.environ[name] for name in ("SYSTEMROOT", "COMSPEC", "PATHEXT", "TEMP", "TMP") if name in os.environ})
            env["USERPROFILE"] = str(home)
        if mods is not None:
            env["AGENTFLEET_MODS"] = mods
        env.update(extra_env or {})
        return env

    def run_installer(self, home: Path, *options: str, fake: bool = True, mods: str | None = None,
                      path: str | None = None, extra_env: dict[str, str] | None = None,
                      cwd: Path = ROOT):
        env = self.env_for(home, fake=fake, mods=mods, path=path, extra_env=extra_env)
        command = [PYTHON, str(INSTALLER), *options, "--home", str(home), "--config-home", str(home / ".config")]
        return subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True)

    def install(self, home: Path, *options: str, fake: bool = True, mods: str | None = None,
                path: str | None = None, extra_env: dict[str, str] | None = None, cwd: Path = ROOT):
        return self.run_installer(home, "--apply", "--no-wizard", "--provider", "native", *options,
                                  fake=fake, mods=mods, path=path, extra_env=extra_env, cwd=cwd)

    def uninstall(self, home: Path):
        return self.run_installer(home, "--uninstall", "--apply")

    def rollback(self, home: Path, mode: str):
        return self.run_installer(home, "--rollback", mode)

    @staticmethod
    def metadata(home: Path) -> dict:
        path = home / ".claude" / ".claude-agents-config-install.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    def test_fresh_install_copies_payload_and_registers_default_mods(self):
        home = self.home_for()
        result = self.install(home)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

        mods_dir = home / ".claude" / "agentfleet" / "mods"
        source_mods = ROOT / "mods"
        expected = {
            path.relative_to(source_mods).as_posix()
            for path in source_mods.rglob("*")
            if path.is_file()
            and "tests" not in path.relative_to(source_mods).parts
            and not any(path.relative_to(source_mods).parts[index:index + 2] == (".claude-plugin", "types")
                        for index in range(len(path.relative_to(source_mods).parts) - 1))
        }
        actual_paths = {path.relative_to(mods_dir).as_posix() for path in mods_dir.rglob("*") if path.is_file()}
        self.assertEqual(actual_paths, expected, "installed mod payload must include each source file except excluded tests/generated types")
        for rel in expected:
            self.assertEqual((mods_dir / rel).read_bytes(), (source_mods / rel).read_bytes(), rel)
        self.assertEqual({Path(path).parts[0] for path in expected if Path(path).parts[0] != ".claude-plugin"},
                         {"fleet-status", "lane-guard", "token-weather", "brain", "fast-jev-compaction"})
        self.assertFalse(any("tests" in path.parts for path in mods_dir.rglob("*")), "plugin test directories are not installed")
        for path in mods_dir.rglob("*"):
            if path.is_file() and os.name == "posix":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644, str(path))

        calls = self.calls()
        self.assertEqual([call["argv"] for call in calls], [
            ["--version"],
            ["plugin", "marketplace", "list", "--json"],
            ["plugin", "marketplace", "add", str(mods_dir)],
            ["plugin", "list", "--json"],
            ["plugin", "install", MOD_ID],
            ["plugin", "install", GUARD_ID],
            ["plugin", "install", WEATHER_ID],
            ["plugin", "install", BRAIN_ID],
        ])
        self.assertTrue(all(call["HOME"] == str(home) for call in calls))
        self.assertEqual(json.loads(self.state_path.read_text())["plugins"],
                         [{"id": MOD_ID, "enabled": True}, {"id": GUARD_ID, "enabled": True}, {"id": WEATHER_ID, "enabled": True}, {"id": BRAIN_ID, "enabled": True}])
        self.assertEqual(self.metadata(home).get("modsState"), "registered")
        self.assertTrue(self.metadata(home).get("modsMarketplace"))

    def test_fresh_registration_installs_brain_and_prints_its_message(self):
        home = self.home_for()
        result = self.install(home)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("plugin", [call["argv"][0] for call in self.calls()])
        self.assertIn(["plugin", "install", BRAIN_ID], [call["argv"] for call in self.calls()])
        self.assertIn("Added the brain mod: /brain shows agent-brain token usage and the latest project memories. Turn it off with /plugin.",
                      result.stdout + result.stderr)
        self.assertIn({"id": BRAIN_ID, "enabled": True}, json.loads(self.state_path.read_text())["plugins"])

    def test_second_install_does_not_repeat_marketplace_or_plugin_registration(self):
        home = self.home_for()
        first = self.install(home)
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        before = len(self.calls())
        second = self.install(home)
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        new_calls = self.calls()[before:]
        self.assertFalse(any(call["argv"][:3] == ["plugin", "marketplace", "add"] or
                             call["argv"][:2] == ["plugin", "install"] for call in new_calls), new_calls)
        self.assertEqual(len(new_calls), 0, "a registered marketplace requires no further CLI checks")

    def installed_with_stale_mods(self, versions: dict[str, str], *, recorded: bool = False) -> Path:
        """An install made before 2.7.2: registered, plugins at older versions,
        and no record of the synced mod versions."""
        home = self.home_for()
        first = self.install(home)
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        state = json.loads(self.state_path.read_text())
        shipped = {path.parent.parent.name: json.loads(path.read_text())["version"]
                   for path in (home / ".claude" / "agentfleet" / "mods").glob("*/.claude-plugin/plugin.json")}
        for item in state["plugins"]:
            name = item["id"].split("@")[0]
            item["version"] = versions.get(name, shipped[name])
        self.state_path.write_text(json.dumps(state))
        self.log_path.write_text("", encoding="utf-8")
        if not recorded:
            meta_path = home / ".claude" / ".claude-agents-config-install.json"
            meta = json.loads(meta_path.read_text())
            meta.pop("modsVersions", None)
            meta_path.write_text(json.dumps(meta))
        return home

    def test_fresh_registration_records_the_synced_mod_versions(self):
        home = self.home_for()
        result = self.install(home)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        versions = self.metadata(home).get("modsVersions")
        self.assertEqual(versions["brain"], json.loads((ROOT / "mods/brain/.claude-plugin/plugin.json").read_text())["version"])
        self.assertEqual(set(versions), {"fleet-status", "lane-guard", "token-weather", "brain", "fast-jev-compaction"})

    def test_update_brings_stale_installed_mods_to_the_catalog_versions(self):
        home = self.installed_with_stale_mods({"brain": "1.0.0", "fleet-status": "1.0.0"})
        result = self.install(home)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        argv = [call["argv"] for call in self.calls()]
        self.assertEqual(argv, [
            ["--version"],
            ["plugin", "list", "--json"],
            ["plugin", "marketplace", "update", "agentfleet"],
            ["plugin", "update", MOD_ID],
            ["plugin", "update", BRAIN_ID],
        ])
        brain = json.loads((ROOT / "mods/brain/.claude-plugin/plugin.json").read_text())["version"]
        self.assertIn(f"brain 1.0.0 to {brain}", result.stdout)
        self.assertIn("Restart Claude Code to use them.", result.stdout)
        self.assertEqual(self.metadata(home)["modsVersions"]["brain"], brain)
        # Recorded: the next update makes no CLI calls.
        self.log_path.write_text("", encoding="utf-8")
        again = self.install(home)
        self.assertEqual(again.returncode, 0, again.stderr + again.stdout)
        self.assertEqual(self.calls(), [])

    def test_update_keeps_a_disabled_stale_mod_disabled(self):
        home = self.installed_with_stale_mods({"brain": "1.0.0"})
        state = json.loads(self.state_path.read_text())
        for item in state["plugins"]:
            if item["id"] == BRAIN_ID:
                item["enabled"] = False
        self.state_path.write_text(json.dumps(state))
        result = self.install(home)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        argv = [call["argv"] for call in self.calls()]
        self.assertIn(["plugin", "update", BRAIN_ID], argv)
        self.assertFalse(any(args[:2] in (["plugin", "enable"], ["plugin", "install"]) for args in argv), argv)
        brain = next(item for item in json.loads(self.state_path.read_text())["plugins"] if item["id"] == BRAIN_ID)
        self.assertFalse(brain["enabled"])

    def test_current_mods_are_only_recorded_without_update_calls(self):
        home = self.installed_with_stale_mods({})
        result = self.install(home)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual([call["argv"] for call in self.calls()], [["--version"], ["plugin", "list", "--json"]])
        self.assertNotIn("Updated mods", result.stdout)
        self.assertIn("brain", self.metadata(home)["modsVersions"])

    def test_failed_mod_update_is_nonfatal_and_retried_next_time(self):
        home = self.installed_with_stale_mods({"brain": "1.0.0"})
        state = json.loads(self.state_path.read_text())
        state["exit_codes"] = {"plugin_update": 1}
        self.state_path.write_text(json.dumps(state))
        result = self.install(home)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn(f"Could not update {BRAIN_ID}", result.stdout)
        self.assertNotIn("modsVersions", self.metadata(home))
        state = json.loads(self.state_path.read_text())
        state.pop("exit_codes")
        self.state_path.write_text(json.dumps(state))
        self.log_path.write_text("", encoding="utf-8")
        retry = self.install(home)
        self.assertEqual(retry.returncode, 0, retry.stderr + retry.stdout)
        self.assertIn(["plugin", "update", BRAIN_ID], [call["argv"] for call in self.calls()])
        self.assertIn("modsVersions", self.metadata(home))

    def test_existing_disabled_plugin_is_not_installed_or_enabled(self):
        self.set_fake_state(new_state(plugins=[{"id": MOD_ID, "enabled": False}]))
        home = self.home_for()
        result = self.install(home)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        argv = [call["argv"] for call in self.calls()]
        self.assertIn(["plugin", "list", "--json"], argv)
        self.assertNotIn(["plugin", "install", MOD_ID], argv)
        self.assertFalse(any(args[:2] == ["plugin", "disable"] for args in argv))
        # The disabled plugin stays disabled; only the missing defaults are added.
        self.assertEqual(json.loads(self.state_path.read_text())["plugins"],
                         [{"id": MOD_ID, "enabled": False}, {"id": GUARD_ID, "enabled": True}, {"id": WEATHER_ID, "enabled": True}, {"id": BRAIN_ID, "enabled": True}])
        self.assertEqual(self.metadata(home).get("modsState"), "registered")
        self.assertTrue(self.metadata(home).get("modsMarketplace"))

    def test_foreign_marketplace_is_left_untouched_without_recording_registration(self):
        foreign = self.root / "other-marketplace"
        self.set_fake_state(new_state(marketplaces=[{
            "name": "agentfleet", "source": {"source": "directory", "path": str(foreign)}, "path": str(foreign),
        }]))
        home = self.home_for()
        result = self.install(home)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        output = result.stdout + result.stderr
        self.assertIn('different "agentfleet" marketplace', output)
        argv = [call["argv"] for call in self.calls()]
        self.assertNotIn(["plugin", "marketplace", "add", str(home / ".claude/agentfleet/mods")], argv)
        self.assertNotIn(["plugin", "install", MOD_ID], argv)
        self.assertNotIn("modsState", self.metadata(home))

    def test_missing_claude_is_nonfatal_and_a_later_run_registers(self):
        home = self.home_for()
        env = self.env_for(home, fake=False)
        self.assertIsNone(shutil.which("claude", path=env["PATH"]), env["PATH"])
        result = self.install(home, fake=False)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("claude plugin marketplace add", result.stdout)
        self.assertEqual(self.calls(), [])
        self.assertNotIn("modsState", self.metadata(home))

        later = self.install(home, fake=True)
        self.assertEqual(later.returncode, 0, later.stderr + later.stdout)
        self.assertEqual(self.metadata(home).get("modsState"), "registered")
        self.assertEqual([call["argv"] for call in self.calls()][-4:], [["plugin", "install", MOD_ID], ["plugin", "install", GUARD_ID], ["plugin", "install", WEATHER_ID], ["plugin", "install", BRAIN_ID]])

    def test_unsupported_claude_version_is_skipped_without_record(self):
        self.set_fake_state(new_state(version="2.1.200 (Claude Code)"))
        home = self.home_for()
        result = self.install(home)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("claude plugin marketplace add", result.stdout)
        self.assertEqual([call["argv"] for call in self.calls()], [["--version"]])
        self.assertNotIn("modsState", self.metadata(home))

    def test_no_mods_and_environment_declines_persist_until_explicit_override(self):
        for label, options, mods_env in (
            ("flag", ("--no-mods",), None),
            ("environment", (), "0"),
        ):
            with self.subTest(choice=label):
                home = self.home_for(label)
                self.set_fake_state()
                first = self.install(home, *options, mods=mods_env)
                self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
                self.assertIn("modsState", self.metadata(home))
                self.assertEqual(self.metadata(home)["modsState"], "declined")
                self.assertEqual(self.calls(), [], "a decline must not invoke any Claude CLI command")

                again = self.install(home)
                self.assertEqual(again.returncode, 0, again.stderr + again.stdout)
                self.assertEqual(self.calls(), [], "the recorded decline is persistent")

                override = self.install(home, "--mods", mods=mods_env)
                self.assertEqual(override.returncode, 0, override.stderr + override.stdout)
                self.assertEqual(self.metadata(home).get("modsState"), "registered")
                self.assertEqual([call["argv"] for call in self.calls()][-4:], [["plugin", "install", MOD_ID], ["plugin", "install", GUARD_ID], ["plugin", "install", WEATHER_ID], ["plugin", "install", BRAIN_ID]])

    def test_dry_run_makes_no_claude_calls(self):
        home = self.home_for()
        env = self.env_for(home)
        command = [PYTHON, str(INSTALLER), "--dry-run", "--no-wizard", "--provider", "native",
                   "--home", str(home), "--config-home", str(home / ".config")]
        result = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.calls(), [])

    def seed_mod_settings(self, home: Path, market_path: Path | None = None):
        path = home / ".claude" / "settings.json"
        settings = json.loads(path.read_text(encoding="utf-8"))
        settings.setdefault("extraKnownMarketplaces", {})["agentfleet"] = {
            "source": {"source": "directory", "path": str(market_path or (home / ".claude/agentfleet/mods"))}
        }
        settings.setdefault("enabledPlugins", {}).update({
            "fleet-status@agentfleet": True,
            "token-weather@agentfleet": False,
            "fast-jev-compaction@agentfleet": True,
            "unrelated@another-marketplace": True,
        })
        path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")

    def test_uninstall_removes_our_marketplace_settings_and_store(self):
        home = self.home_for()
        install = self.install(home)
        self.assertEqual(install.returncode, 0, install.stderr + install.stdout)
        self.seed_mod_settings(home)

        result = self.uninstall(home)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        argv = [call["argv"] for call in self.calls()]
        self.assertIn(["plugin", "marketplace", "remove", "agentfleet"], argv)
        settings = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertNotIn("agentfleet", settings.get("extraKnownMarketplaces", {}))
        self.assertFalse(any(key.endswith("@agentfleet") for key in settings.get("enabledPlugins", {})))
        self.assertTrue(settings["enabledPlugins"]["unrelated@another-marketplace"])
        self.assertFalse((home / ".claude" / "agentfleet").exists())

    def test_uninstall_does_not_remove_a_marketplace_that_became_foreign(self):
        home = self.home_for()
        install = self.install(home)
        self.assertEqual(install.returncode, 0, install.stderr + install.stdout)
        foreign = self.root / "foreign-after-install"
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        state["marketplaces"] = [{
            "name": "agentfleet", "source": {"source": "directory", "path": str(foreign)}, "path": str(foreign),
        }]
        self.state_path.write_text(json.dumps(state), encoding="utf-8")
        self.seed_mod_settings(home, foreign)

        result = self.uninstall(home)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        argv = [call["argv"] for call in self.calls()]
        self.assertNotIn(["plugin", "marketplace", "remove", "agentfleet"], argv)
        settings = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertIn("agentfleet", settings.get("extraKnownMarketplaces", {}))
        self.assertIn("fleet-status@agentfleet", settings.get("enabledPlugins", {}))

    def test_failed_plugin_install_is_nonfatal_and_registration_retries_later(self):
        self.set_fake_state(new_state(exit_codes={"plugin_install": 1}))
        home = self.home_for()
        result = self.install(home)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        manual = f'claude plugin marketplace add "{home / ".claude/agentfleet/mods"}" and claude plugin install {MOD_ID}'
        self.assertIn(manual, result.stdout)
        self.assertNotIn("modsState", self.metadata(home))

        self.set_fake_state()
        later = self.install(home)
        self.assertEqual(later.returncode, 0, later.stderr + later.stdout)
        self.assertEqual(self.metadata(home).get("modsState"), "registered")
        self.assertEqual([call["argv"] for call in self.calls()][-4:], [["plugin", "install", MOD_ID], ["plugin", "install", GUARD_ID], ["plugin", "install", WEATHER_ID], ["plugin", "install", BRAIN_ID]])

    def test_failed_plugin_install_still_records_marketplace_ownership_for_uninstall(self):
        home = self.home_for()
        self.set_fake_state(new_state(exit_codes={"plugin_install": 1}))
        install = self.install(home)
        self.assertEqual(install.returncode, 0, install.stderr + install.stdout)
        self.assertTrue(self.metadata(home).get("modsMarketplace"))
        self.assertNotIn("modsState", self.metadata(home))
        # The first default fails, so registration stops there and retries later.
        self.assertEqual([call["argv"] for call in self.calls()][-1], ["plugin", "install", MOD_ID])

        result = self.uninstall(home)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn(["plugin", "marketplace", "remove", "agentfleet"], [call["argv"] for call in self.calls()])

    def test_update_declining_after_registration_keeps_marketplace_owned_for_uninstall(self):
        home = self.home_for()
        install = self.install(home)
        self.assertEqual(install.returncode, 0, install.stderr + install.stdout)
        before_update = len(self.calls())

        update = self.install(home, "--no-mods")
        self.assertEqual(update.returncode, 0, update.stderr + update.stdout)
        self.assertEqual(len(self.calls()), before_update, "a later decline makes no Claude CLI calls")
        self.assertEqual(self.metadata(home).get("modsState"), "declined")
        self.assertTrue(self.metadata(home).get("modsMarketplace"))

        uninstall = self.uninstall(home)
        self.assertEqual(uninstall.returncode, 0, uninstall.stderr + uninstall.stdout)
        self.assertIn(["plugin", "marketplace", "remove", "agentfleet"], [call["argv"] for call in self.calls()])

    def test_uninstall_keeps_claude_generated_types_payload(self):
        home = self.home_for()
        install = self.install(home)
        self.assertEqual(install.returncode, 0, install.stderr + install.stdout)
        generated = home / ".claude/agentfleet/mods/fleet-status/.claude-plugin/types/generated.d.ts"
        generated.parent.mkdir(parents=True)
        generated.write_text("// generated by Claude Code\n", encoding="utf-8")

        result = self.uninstall(home)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertTrue(generated.is_file(), "uninstall preserves files outside the installer's managed payload")
        self.assertEqual(generated.read_text(encoding="utf-8"), "// generated by Claude Code\n")
        self.assertFalse((home / ".claude/.claude-agents-config-install.json").exists())
        self.assertNotIn("Uninstall left metadata", result.stdout + result.stderr)

    def test_rollback_to_snapshot_without_mods_unregisters_marketplace(self):
        home = self.home_for()
        install = self.install(home)
        self.assertEqual(install.returncode, 0, install.stderr + install.stdout)
        result = self.rollback(home, "--apply")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn(["plugin", "marketplace", "remove", "agentfleet"], [call["argv"] for call in self.calls()])
        self.assertFalse(any(path.is_file() for path in (home / ".claude/agentfleet/mods").rglob("*")))
        self.assertNotEqual(self.metadata(home).get("modsState"), "registered")

    def test_rollback_restores_registered_mods_and_reinstalls_missing_catalog(self):
        home = self.home_for()
        install = self.install(home)
        self.assertEqual(install.returncode, 0, install.stderr + install.stdout)
        uninstall = self.uninstall(home)
        self.assertEqual(uninstall.returncode, 0, uninstall.stderr + uninstall.stdout)
        self.assertFalse((home / ".claude/agentfleet/mods/fleet-status/.claude-plugin/plugin.json").exists())
        self.set_fake_state()

        dry = self.rollback(home, "--dry-run")
        self.assertEqual(dry.returncode, 0, dry.stderr + dry.stdout)
        self.assertIn("would register the AgentFleet plugin catalog", dry.stdout)
        self.assertEqual(self.calls(), [], "rollback dry-run must not invoke Claude")

        rollback = self.rollback(home, "--apply")
        self.assertEqual(rollback.returncode, 0, rollback.stderr + rollback.stdout)
        argv = [call["argv"] for call in self.calls()]
        self.assertIn(["plugin", "marketplace", "add", str(home / ".claude/agentfleet/mods")], argv)
        self.assertIn(["plugin", "install", MOD_ID], argv)
        self.assertIn(["plugin", "install", GUARD_ID], argv, "a re-added catalog gets every default mod back")
        self.assertIn(["plugin", "install", WEATHER_ID], argv)
        self.assertIn(["plugin", "install", BRAIN_ID], argv)
        self.assertEqual(self.metadata(home).get("modsState"), "registered")
        self.assertTrue(self.metadata(home).get("modsMarketplace"))

    def test_rollback_to_declined_mods_does_not_register_catalog(self):
        home = self.home_for()
        install = self.install(home)
        self.assertEqual(install.returncode, 0, install.stderr + install.stdout)
        declined = self.install(home, "--no-mods")
        self.assertEqual(declined.returncode, 0, declined.stderr + declined.stdout)
        self.assertEqual(self.metadata(home).get("modsState"), "declined")
        self.assertTrue(self.metadata(home).get("modsMarketplace"))
        uninstall = self.uninstall(home)
        self.assertEqual(uninstall.returncode, 0, uninstall.stderr + uninstall.stdout)
        self.set_fake_state()

        rollback = self.rollback(home, "--apply")
        self.assertEqual(rollback.returncode, 0, rollback.stderr + rollback.stdout)
        argv = [call["argv"] for call in self.calls()]
        self.assertFalse(any(args[:3] == ["plugin", "marketplace", "add"] or args[:2] == ["plugin", "install"] for args in argv), argv)
        self.assertEqual(self.metadata(home).get("modsState"), "declined")

    def test_cli_environment_drops_credentials_and_unlisted_variables(self):
        home = self.home_for()
        result = self.install(home, extra_env={
            "ANTHROPIC_AUTH_TOKEN": "sentinel1",
            "MY_GATEWAY_SECRET": "sentinel2",
        })
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        calls = self.calls()
        self.assertTrue(calls)
        self.assertTrue(all(call["HOME"] == str(home) for call in calls))
        for call in calls:
            self.assertNotIn("ANTHROPIC_AUTH_TOKEN", call["env_names"])
            self.assertNotIn("MY_GATEWAY_SECRET", call["env_names"])

    def test_relative_path_entry_does_not_trust_claude_executable(self):
        home = self.home_for()
        cwd = self.root / "relative-path-cwd"
        relative_bin = cwd / "relbin"
        relative_bin.mkdir(parents=True)
        write_fake_claude(relative_bin)
        result = self.install(home, path=f"relbin{os.pathsep}{os.pathsep.join([str(self.node_bin), *self.system_path])}", cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("claude plugin marketplace add", result.stdout)
        self.assertEqual(self.calls(), [])
        self.assertNotIn("modsState", self.metadata(home))


if __name__ == "__main__":
    unittest.main(verbosity=2)
