#!/usr/bin/env python3
"""Hermetic integration tests for AgentFleet 2.2.0 installer and doctor features."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
INSTALLER = ROOT / "bin" / "install.py"
DOCTOR = ROOT / "bin" / "claude-agents-doctor"

FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
state_path = Path(os.environ.get("FAKE_CLAUDE_STATE", root / "claude-state.json"))
log_path = Path(os.environ.get("FAKE_CLAUDE_LOG", root / "claude-calls.jsonl"))
state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
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
    state_path.write_text(json.dumps(state), encoding="utf-8")
elif command == "marketplace_remove":
    state["marketplaces"] = [item for item in state.get("marketplaces", []) if item.get("name") != argv[3]]
    state["plugins"] = [item for item in state.get("plugins", []) if not item.get("id", "").endswith("@agentfleet")]
    state_path.write_text(json.dumps(state), encoding="utf-8")
elif command == "plugin_list":
    print(json.dumps(state.get("plugins", [])))
elif command == "plugin_install":
    plugins = state.setdefault("plugins", [])
    if not any(item.get("id") == argv[2] for item in plugins):
        plugins.append({"id": argv[2], "enabled": True})
    state_path.write_text(json.dumps(state), encoding="utf-8")
elif command == "plugin_disable":
    plugins = state.setdefault("plugins", [])
    for item in plugins:
        if item.get("id") == argv[2]:
            item["enabled"] = False
    state_path.write_text(json.dumps(state), encoding="utf-8")
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


class Install220Tests(unittest.TestCase):
    def setUp(self):
        self.raw = tempfile.TemporaryDirectory(prefix="agentfleet 220 ")
        self.addCleanup(self.raw.cleanup)
        self.root = Path(self.raw.name)
        self.fake_bin = self.root / "fake-bin"
        self.fake_bin.mkdir()
        self.claude = write_fake_claude(self.fake_bin)
        self.state_path = self.root / "claude-state.json"
        self.log_path = self.root / "claude-calls.jsonl"

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
            "AGENTFLEET_NO_CONFIGURE": "1",
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
        command = [PYTHON, "-B", str(INSTALLER), *options, "--home", str(home), "--config-home", str(home / ".config"), "--prefix", str(home / ".local")]
        return subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True)

    def install(self, home: Path, *options: str, fake: bool = True, mods: str | None = None,
                path: str | None = None, extra_env: dict[str, str] | None = None, cwd: Path = ROOT):
        return self.run_installer(home, "--apply", "--no-wizard", "--provider", "native", *options,
                                  fake=fake, mods=mods, path=path, extra_env=extra_env, cwd=cwd)

    def uninstall(self, home: Path):
        return self.run_installer(home, "--uninstall", "--apply")

    def run_doctor(self, home: Path):
        env = self.env_for(home)
        cmd = [PYTHON, "-B", str(DOCTOR), "--check", "--home", str(home), "--config-home", str(home / ".config"), "--prefix", str(home / ".local"), "--bundle", str(ROOT)]
        return subprocess.run(cmd, cwd=ROOT, env=env, text=True, capture_output=True)

    @staticmethod
    def metadata(home: Path) -> dict:
        path = home / ".claude" / ".claude-agents-config-install.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    def test_new_files_installed_doctor_passes_and_uninstall_removes_them(self):
        home = self.home_for()
        res = self.install(home)
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)

        fleet_watch = home / ".claude" / "fleet_watch.py"
        fleet_suggest = home / ".claude" / "fleet_suggest.py"
        fleet_suggestions = home / ".claude" / "fleet-suggestions.json"
        skill_md = home / ".claude" / "skills" / "fleet-suggest" / "SKILL.md"

        self.assertTrue(fleet_watch.is_file())
        self.assertTrue(fleet_suggest.is_file())
        self.assertTrue(fleet_suggestions.is_file())
        self.assertTrue(skill_md.is_file())

        if os.name != "nt":
            self.assertTrue(bool(fleet_watch.stat().st_mode & 0o111))
            self.assertTrue(bool(fleet_suggest.stat().st_mode & 0o111))

        doc = self.run_doctor(home)
        self.assertEqual(doc.returncode, 0, doc.stderr + doc.stdout)

        un = self.uninstall(home)
        self.assertEqual(un.returncode, 0, un.stderr + un.stdout)

        self.assertFalse(fleet_watch.exists())
        self.assertFalse(fleet_suggest.exists())
        self.assertFalse(fleet_suggestions.exists())
        self.assertFalse(skill_md.exists())
        self.assertFalse((home / ".claude" / "skills" / "fleet-suggest").exists())

    def test_lane_guard_registered_on_fresh_install(self):
        home = self.home_for()
        res = self.install(home)
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)

        notice = "Added the lane-guard mod: fleet lanes can no longer commit, push, deploy, run migrations or destructive SQL, or revert the working tree. Turn it off with /plugin."
        self.assertIn(notice, res.stdout)

        installed_calls = [c["argv"] for c in self.calls()]
        self.assertIn(["plugin", "install", "fleet-status@agentfleet"], installed_calls)
        self.assertIn(["plugin", "install", "lane-guard@agentfleet"], installed_calls)

        meta = self.metadata(home)
        self.assertEqual(meta.get("modsState"), "registered")
        self.assertEqual(meta.get("modsInstalled"), ["fleet-status", "lane-guard"])

    def test_lane_guard_added_once_on_update_from_210_style_metadata(self):
        home = self.home_for()
        first = self.install(home)
        self.assertEqual(first.returncode, 0)

        # Simulate 2.1.0 install: modsState is registered, modsMarketplace is True, modsInstalled is absent
        meta_path = home / ".claude" / ".claude-agents-config-install.json"
        meta = json.loads(meta_path.read_text())
        meta.pop("modsInstalled", None)
        meta["modsState"] = "registered"
        meta["modsMarketplace"] = True
        meta_path.write_text(json.dumps(meta, indent=2) + "\n")

        # Fake CLI already has fleet-status registered (from 2.1.0)
        current_state = json.loads(self.state_path.read_text())
        current_state["plugins"] = [{"id": "fleet-status@agentfleet", "enabled": True}]
        self.set_fake_state(current_state)

        # First update to 2.2.0
        up = self.install(home)
        self.assertEqual(up.returncode, 0, up.stderr + up.stdout)
        notice = "Added the lane-guard mod: fleet lanes can no longer commit, push, deploy, run migrations or destructive SQL, or revert the working tree. Turn it off with /plugin."
        self.assertIn(notice, up.stdout)
        self.assertIn(["plugin", "install", "lane-guard@agentfleet"], [c["argv"] for c in self.calls()])

        meta_after = self.metadata(home)
        self.assertEqual(meta_after.get("modsInstalled"), ["fleet-status", "lane-guard"])

        # Second update: lane-guard must not be installed again
        self.log_path.write_text("")
        up2 = self.install(home)
        self.assertEqual(up2.returncode, 0, up2.stderr + up2.stdout)
        self.assertNotIn(["plugin", "install", "lane-guard@agentfleet"], [c["argv"] for c in self.calls()])

    def test_lane_guard_not_readded_when_modsinstalled_lists_it_but_cli_does_not(self):
        home = self.home_for()
        res = self.install(home)
        self.assertEqual(res.returncode, 0)

        # Simulate user uninstalled lane-guard via Claude CLI
        current_state = json.loads(self.state_path.read_text())
        current_state["plugins"] = [{"id": "fleet-status@agentfleet", "enabled": True}]
        self.set_fake_state(current_state)

        up = self.install(home)
        self.assertEqual(up.returncode, 0, up.stderr + up.stdout)
        calls = [c["argv"] for c in self.calls()]
        self.assertNotIn(["plugin", "install", "lane-guard@agentfleet"], calls)
        meta = self.metadata(home)
        self.assertIn("lane-guard", meta.get("modsInstalled", []))

    def test_lane_guard_not_touched_when_listed_as_disabled(self):
        home = self.home_for()
        res = self.install(home)
        self.assertEqual(res.returncode, 0)

        # Simulate disabled plugin in CLI and metadata has fleet-status only
        meta_path = home / ".claude" / ".claude-agents-config-install.json"
        meta = json.loads(meta_path.read_text())
        meta["modsInstalled"] = ["fleet-status"]
        meta_path.write_text(json.dumps(meta, indent=2) + "\n")

        current_state = json.loads(self.state_path.read_text())
        current_state["plugins"] = [{"id": "fleet-status@agentfleet", "enabled": True}, {"id": "lane-guard@agentfleet", "enabled": False}]
        self.set_fake_state(current_state)

        up = self.install(home)
        self.assertEqual(up.returncode, 0, up.stderr + up.stdout)
        calls = [c["argv"] for c in self.calls()]
        self.assertNotIn(["plugin", "install", "lane-guard@agentfleet"], calls)

    def test_no_mods_skips_mod_registration(self):
        home = self.home_for()
        res = self.install(home, "--no-mods")
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertEqual(self.calls(), [])
        meta = self.metadata(home)
        self.assertEqual(meta.get("modsState"), "declined")

    def test_fresh_install_compaction_window_default_is_1000000(self):
        home = self.home_for()
        res = self.install(home)
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        settings = json.loads((home / ".claude" / "settings.json").read_text())
        self.assertEqual(settings.get("autoCompactWindow"), 1000000)
        self.assertEqual(settings.get("env", {}).get("CLAUDE_CODE_AUTO_COMPACT_WINDOW"), "1000000")
        self.assertEqual(settings["env"]["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"], "70")
        self.assertNotIn("CLAUDE_CODE_MAX_CONTEXT_TOKENS", settings["env"])
        sys.path.insert(0, str(ROOT / "scripts"))
        import provider_catalog
        self.assertIs(provider_catalog.load_preferences(home)["learn"], True)

    def test_hook_written_220_pair_upgrades_and_learning_stays_off(self):
        home = self.home_for()
        self.assertEqual(self.install(home).returncode, 0)
        meta_path = home / ".claude" / ".claude-agents-config-install.json"
        meta = json.loads(meta_path.read_text())
        meta["version"] = "2.2.0"
        meta_path.write_text(json.dumps(meta, indent=2) + "\n")
        conf = home / ".claude" / "agentfleet" / "config.json"
        conf.write_text(json.dumps({"compactPercent": 40}))
        settings_file = home / ".claude" / "settings.json"
        settings = json.loads(settings_file.read_text())
        settings["autoCompactWindow"] = 400000
        settings["env"]["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = "400000"
        settings["env"].pop("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", None)
        settings_file.write_text(json.dumps(settings))
        result = self.install(home)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        settings = json.loads(settings_file.read_text())
        self.assertEqual(settings["autoCompactWindow"], 1000000)
        self.assertEqual(settings["env"]["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"], "40")
        self.assertIs(json.loads(conf.read_text())["learn"], False)

    def test_update_refreshes_bundle_owned_800000_pair_to_1000000(self):
        home = self.home_for()
        res = self.install(home)
        self.assertEqual(res.returncode, 0)

        # Set 2.1.0-style 800000 pair and record in journal
        settings_path = home / ".claude" / "settings.json"
        settings = json.loads(settings_path.read_text())
        settings["autoCompactWindow"] = 800000
        settings["env"]["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = "800000"
        settings_path.write_text(json.dumps(settings, indent=2) + "\n")

        meta_path = home / ".claude" / ".claude-agents-config-install.json"
        meta = json.loads(meta_path.read_text())
        meta["settingsJournal"] = [
            {"id": "value:autoCompactWindow", "kind": "value", "installed": 800000, "installedPresent": True, "path": ["autoCompactWindow"]},
            {"id": "value:env:CLAUDE_CODE_AUTO_COMPACT_WINDOW", "kind": "value", "installed": "800000", "installedPresent": True, "path": ["env", "CLAUDE_CODE_AUTO_COMPACT_WINDOW"]}
        ]
        meta_path.write_text(json.dumps(meta, indent=2) + "\n")

        up = self.install(home)
        self.assertEqual(up.returncode, 0, up.stderr + up.stdout)
        settings_after = json.loads(settings_path.read_text())
        self.assertEqual(settings_after.get("autoCompactWindow"), 1000000)
        self.assertEqual(settings_after.get("env", {}).get("CLAUDE_CODE_AUTO_COMPACT_WINDOW"), "1000000")

    def test_compact_percent_55_sets_window_to_1000000(self):
        home = self.home_for()
        conf_dir = home / ".claude" / "agentfleet"
        conf_dir.mkdir(parents=True, exist_ok=True)
        (conf_dir / "config.json").write_text(json.dumps({
            "format": "agentfleet.config.v1",
            "compactPercent": 55,
            "reviewerAccess": "full",
            "learn": False,
        }) + "\n")

        res = self.install(home)
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        settings = json.loads((home / ".claude" / "settings.json").read_text())
        self.assertEqual(settings.get("autoCompactWindow"), 1000000)
        self.assertEqual(settings.get("env", {}).get("CLAUDE_CODE_AUTO_COMPACT_WINDOW"), "1000000")

    def test_user_edited_compaction_pair_is_preserved(self):
        home = self.home_for()
        res = self.install(home)
        self.assertEqual(res.returncode, 0)

        # User edits compaction window to 600000
        settings_path = home / ".claude" / "settings.json"
        settings = json.loads(settings_path.read_text())
        settings["autoCompactWindow"] = 600000
        settings["env"]["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = "600000"
        settings_path.write_text(json.dumps(settings, indent=2) + "\n")

        up = self.install(home)
        self.assertEqual(up.returncode, 0, up.stderr + up.stdout)
        settings_after = json.loads(settings_path.read_text())
        self.assertEqual(settings_after.get("autoCompactWindow"), 600000)
        self.assertEqual(settings_after.get("env", {}).get("CLAUDE_CODE_AUTO_COMPACT_WINDOW"), "600000")
        # The native window is recorded as the user's own, so the startup
        # hook (which sizes native windows since 2.2.0) leaves it alone too.
        self.assertIn("Kept your auto-compact window (600000 tokens)", up.stdout)
        prefs = json.loads((home / ".claude" / "agentfleet" / "config.json").read_text())
        self.assertEqual(prefs.get("compactPercent"), "off")
        hook = subprocess.run([PYTHON, "-B", str(home / ".claude" / "agentfleet.py"), "--home", str(home), "--config-home", str(home / ".config"), "auto-update", "--hook"],
                              env={**self.env_for(home), "AGENTFLEET_AUTO_UPDATE": "0"}, text=True, capture_output=True)
        self.assertEqual(hook.returncode, 0, hook.stderr)
        self.assertEqual(json.loads(settings_path.read_text()).get("autoCompactWindow"), 600000)

    def test_already_listed_default_is_recorded_so_a_removal_sticks(self):
        # lane-guard was installed (and disabled) before AgentFleet first saw it.
        self.set_fake_state(new_state(plugins=[{"id": "lane-guard@agentfleet", "enabled": False}]))
        home = self.home_for()
        first = self.install(home)
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        self.assertIn("lane-guard", self.metadata(home).get("modsInstalled", []))
        # The user then removes it; the next update must not bring it back.
        state = json.loads(self.state_path.read_text())
        state["plugins"] = [item for item in state["plugins"] if item.get("id") != "lane-guard@agentfleet"]
        self.set_fake_state(state)
        again = self.install(home)
        self.assertEqual(again.returncode, 0, again.stderr + again.stdout)
        self.assertNotIn(["plugin", "install", "lane-guard@agentfleet"], [call["argv"] for call in self.calls()])

    def test_compact_percent_off_prevents_refresh_of_bundle_owned_pair(self):
        home = self.home_for()
        res = self.install(home)
        self.assertEqual(res.returncode, 0)

        # Simulate 800000 bundle-owned pair
        settings_path = home / ".claude" / "settings.json"
        settings = json.loads(settings_path.read_text())
        settings["autoCompactWindow"] = 800000
        settings["env"]["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = "800000"
        settings_path.write_text(json.dumps(settings, indent=2) + "\n")

        meta_path = home / ".claude" / ".claude-agents-config-install.json"
        meta = json.loads(meta_path.read_text())
        meta["settingsJournal"] = [
            {"id": "value:autoCompactWindow", "kind": "value", "installed": 800000, "installedPresent": True, "path": ["autoCompactWindow"]},
            {"id": "value:env:CLAUDE_CODE_AUTO_COMPACT_WINDOW", "kind": "value", "installed": "800000", "installedPresent": True, "path": ["env", "CLAUDE_CODE_AUTO_COMPACT_WINDOW"]}
        ]
        meta_path.write_text(json.dumps(meta, indent=2) + "\n")

        # Set compactPercent to "off"
        conf_dir = home / ".claude" / "agentfleet"
        conf_dir.mkdir(parents=True, exist_ok=True)
        (conf_dir / "config.json").write_text(json.dumps({
            "format": "agentfleet.config.v1",
            "compactPercent": "off",
            "reviewerAccess": "full",
            "learn": False,
        }) + "\n")

        up = self.install(home)
        self.assertEqual(up.returncode, 0, up.stderr + up.stdout)
        settings_after = json.loads(settings_path.read_text())
        self.assertEqual(settings_after.get("autoCompactWindow"), 800000)
        self.assertEqual(settings_after.get("env", {}).get("CLAUDE_CODE_AUTO_COMPACT_WINDOW"), "800000")

    def test_reviewer_access_read_only_renders_static_tools_and_doctor_passes(self):
        home = self.home_for()
        conf_dir = home / ".claude" / "agentfleet"
        conf_dir.mkdir(parents=True, exist_ok=True)
        (conf_dir / "config.json").write_text(json.dumps({
            "format": "agentfleet.config.v1",
            "compactPercent": 70,
            "reviewerAccess": "read-only",
            "learn": False,
        }) + "\n")

        res = self.install(home)
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)

        review_agent = home / ".claude" / "agents" / "fleet-review.md"
        self.assertTrue(review_agent.is_file())
        text = review_agent.read_text(encoding="utf-8")
        tools_line = next(line for line in text.splitlines() if line.startswith("tools:"))
        tools = {t.strip() for t in tools_line.removeprefix("tools:").split(",")}
        self.assertNotIn("Bash", tools)
        self.assertNotIn("Edit", tools)
        self.assertNotIn("Write", tools)
        self.assertNotIn("Agent", tools)
        self.assertIn("Read", tools)

        doc = self.run_doctor(home)
        self.assertEqual(doc.returncode, 0, doc.stderr + doc.stdout)

    def test_default_reviewer_access_renders_writable_tools_and_doctor_passes(self):
        home = self.home_for()
        res = self.install(home)
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)

        review_agent = home / ".claude" / "agents" / "fleet-review.md"
        self.assertTrue(review_agent.is_file())
        text = review_agent.read_text(encoding="utf-8")
        tools_line = next(line for line in text.splitlines() if line.startswith("tools:"))
        tools = {t.strip() for t in tools_line.removeprefix("tools:").split(",")}
        self.assertIn("Bash", tools)
        self.assertIn("Edit", tools)
        self.assertIn("Write", tools)
        self.assertNotIn("Agent", tools)

        doc = self.run_doctor(home)
        self.assertEqual(doc.returncode, 0, doc.stderr + doc.stdout)

    def test_windows_shims_and_hook_refusal(self):
        # Import functions from install.py
        sys.path.insert(0, str(ROOT / "bin"))
        import install

        probe = ROOT / "bin" / "install.py"
        # 5a: cmd_dispatch
        cmd_bytes = install.cmd_dispatch(probe, "python")
        self.assertIn(b'set "NoDefaultCurrentDirectoryInExePath=1"\r\n', cmd_bytes)
        self.assertIn(b'"%SystemRoot%\\System32\\where.exe" py >nul 2>nul', cmd_bytes)

        node_cmd_bytes = install.cmd_dispatch(probe, "node")
        self.assertIn(b'set "NoDefaultCurrentDirectoryInExePath=1"\r\n', node_cmd_bytes)
        self.assertIn(b'"%SystemRoot%\\System32\\where.exe" node >nul 2>nul', node_cmd_bytes)

        # 5c: powershell_dispatch has UTF-8 BOM
        ps1_bytes = install.powershell_dispatch(probe, "python")
        self.assertTrue(ps1_bytes.startswith(b"\xef\xbb\xbf"))

        # 5b: windows_command refuses $ and backtick
        with self.assertRaises(install.InstallerError):
            install.windows_command("C:\\Users\\$admin\\python.exe", "script.py")

        with self.assertRaises(install.InstallerError):
            install.windows_command("C:\\Users\\`test\\python.exe", "script.py")

        # Clean path works
        clean_cmd = install.windows_command("C:\\Python310\\python.exe", "C:\\script.py")
        self.assertEqual(clean_cmd, '"C:\\Python310\\python.exe" "C:\\script.py"')


if __name__ == "__main__":
    unittest.main()
