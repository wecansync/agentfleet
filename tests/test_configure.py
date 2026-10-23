"""Configuration contracts with isolated homes and a safe fake Claude CLI."""
import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import fleet_configure as wizard

loader = importlib.machinery.SourceFileLoader("configure_agentfleet", str(ROOT / "bin/agentfleet"))
spec = importlib.util.spec_from_loader(loader.name, loader)
api = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = api
loader.exec_module(api)
KEYS = ["compact", "jev", "contextMode", "tokenWeather", "workStyle", "askFirst", "branchCheck", "reviewerAccess", "laneGuard",
        "statusBand", "sessionInfo", "brainMod", "providerUsage", "learn", "agentBrainInstall", "agentBrain", "agentBrainLocalMemory", "autoUpdate", "instructions"]
ACTIONS = {"instructions"}


class ConfigureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.paths = api.Paths(argparse.Namespace(home=str(self.home), config_home=str(self.home / "config"), prefix=None))
        self.paths.claude.mkdir()
        self.paths.settings.write_text('{"env":{"KEEP":"value"}}')
        (self.paths.claude / ".claude-agents-config-install.json").write_text(json.dumps({"version": "2.3.0", "modsMarketplace": True}))
        bindir = self.home / "bin"
        bindir.mkdir()
        cli = bindir / "claude"
        cli_source = "import json,os,sys\nfrom pathlib import Path\np=Path(os.environ['HOME'])/'plugins.json'\ns=json.loads(p.read_text()) if p.exists() else []\nwith open(Path(os.environ['HOME'])/'commands.log','a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')\nif sys.argv[1:]==['plugin','list','--json']: print(json.dumps(s))\nelse:\n ident=sys.argv[-1]; action=sys.argv[-2]\n s=[v for v in s if v['id']!=ident]+[{'id':ident,'enabled':action!='disable'}]\n p.write_text(json.dumps(s))\n"
        cli.write_text(cli_source, encoding="utf-8")
        if os.name == "nt":
            # PATH lookup on Windows finds claude.CMD, not the extensionless script.
            (bindir / "claude.cmd").write_text(f'@"{sys.executable}" "%~dp0claude" %*\n', encoding="utf-8")
        else:
            cli.write_text("#!" + sys.executable + "\n" + cli_source, encoding="utf-8")
            cli.chmod(0o755)
        self.env = {**os.environ, "HOME": str(self.home), "PATH": str(bindir), "FAKE_STATE": str(self.home / "plugins.json"), "FAKE_LOG": str(self.home / "commands.log")}
        self.env.pop("TYPESAFE_API_KEY", None)
        self.env_patch = patch.dict(os.environ, self.env, clear=True)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def command(self, *args):
        return [sys.executable, "-B", str(ROOT / "bin/agentfleet"), "--home", str(self.home), "configure", *args]

    def cli(self, *args):
        return subprocess.run(self.command(*args), env=self.env, text=True, capture_output=True, timeout=30)

    def preset(self, data):
        path = self.home / "preset.json"
        path.write_text(json.dumps({"format": "agentfleet.preset.v1", **data}))
        return path

    def test_update_forwards_configuration_environment(self):
        args = argparse.Namespace(installer_args=['--mods', '--preset', str(self.home / 'team.json')], yes=False, preset=None)
        with patch.object(api, '_run_installer', return_value=0) as installer:
            self.assertEqual(api.cmd_update(self.paths, args), 0)
        self.assertEqual(installer.call_args.args[1], ['--mods'])
        self.assertEqual(installer.call_args.kwargs['configure_args'], ['--preset', str(self.home / 'team.json')])
        class Download:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self):
                return b'exit 0\n'
        with patch.object(api.urllib.request, 'urlopen', return_value=Download()), patch.object(api.subprocess, 'run', return_value=argparse.Namespace(returncode=0)) as run:
            self.assertEqual(api._run_installer(self.paths, ['--mods'], configure_args=['--yes']), 0)
        self.assertEqual(json.loads(run.call_args.kwargs['env']['AGENTFLEET_CONFIGURE_ARGS']), ['--yes'])
        self.assertNotIn('--yes', run.call_args.args[0])

    def test_background_installer_ignores_leftover_environment_configuration(self):
        class Download:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self):
                return b'exit 0\n'
        leftover = {"AGENTFLEET_PRESET": "team.json", "AGENTFLEET_CONFIGURE_ARGS": '["--yes"]'}
        with patch.dict(os.environ, leftover), patch.object(api.urllib.request, 'urlopen', return_value=Download()), patch.object(api.subprocess, 'run', return_value=argparse.Namespace(returncode=0)) as run:
            self.assertEqual(api._run_installer(self.paths, []), 0)
        self.assertNotIn('AGENTFLEET_PRESET', run.call_args.kwargs['env'])
        self.assertNotIn('AGENTFLEET_CONFIGURE_ARGS', run.call_args.kwargs['env'])

    def test_update_converts_environment_preset_to_absolute_path(self):
        args = argparse.Namespace(installer_args=[], yes=False, preset=None)
        with patch.dict(os.environ, {"AGENTFLEET_PRESET": "team.json"}), patch.object(api, '_run_installer', return_value=0) as installer:
            self.assertEqual(api.cmd_update(self.paths, args), 0)
        self.assertEqual(installer.call_args.kwargs['configure_args'], ['--preset', str(Path('team.json').resolve())])

    def test_update_explicit_preset_overrides_environment_preset(self):
        args = argparse.Namespace(installer_args=[], yes=False, preset=str(self.home / 'team.json'))
        with patch.dict(os.environ, {"AGENTFLEET_PRESET": "other.json"}), patch.object(api, '_run_installer', return_value=0) as installer:
            self.assertEqual(api.cmd_update(self.paths, args), 0)
        self.assertEqual(installer.call_args.kwargs['configure_args'], ['--preset', str(self.home / 'team.json')])

    def test_update_forwards_no_configure(self):
        args = argparse.Namespace(installer_args=['--no-configure'], yes=False, preset=None)
        with patch.object(api, '_run_installer', return_value=0) as installer:
            self.assertEqual(api.cmd_update(self.paths, args), 0)
        self.assertIn('--no-configure', installer.call_args.args[1])

    def test_unknown_preset_field_is_escaped(self):
        data = {"format": "agentfleet.preset.v1", "bad\x1b[31mfield": True}
        with self.assertRaises(ValueError) as raised:
            wizard.validate_preset(data)
        self.assertNotIn("\x1b", str(raised.exception))
        self.assertIn("\\x1b", str(raised.exception))

    def test_key_prompt_discloses_storage(self):
        output = io.StringIO()
        with patch.object(wizard.getpass, 'getpass', return_value='') as ask, contextlib.redirect_stdout(output):
            wizard.ask_key(api, self.paths)
        ask.assert_called_once()
        self.assertIn('~/.claude/settings.json', output.getvalue())
        self.assertIn('TYPESAFE_API_KEY', output.getvalue())
        self.assertIn('/plugin', output.getvalue())

    def test_learning_question_names_documentation_sites(self):
        learn = next(f for f in wizard.FEATURES if f.key == 'learn')
        self.assertIn('documentation sites', learn.question)
        self.assertIn('never prompt text', learn.question)

    def test_plugin_features_stay_unasked_without_mods(self):
        (self.paths.claude / ".claude-agents-config-install.json").write_text('{}')
        result = self.cli('--after-install')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "New AgentFleet options are available: run agentfleet configure")
        self.assertEqual(self.cli('--yes').returncode, 0)
        asked = json.loads((self.paths.store / 'wizard.json').read_text())['asked']
        self.assertTrue({'compact', 'reviewerAccess', 'learn', 'autoUpdate', 'workStyle', 'agentBrain'}.issubset(asked))
        self.assertNotIn('instructions', asked)
        (self.paths.claude / ".claude-agents-config-install.json").write_text(json.dumps({"version": "2.3.0", "modsMarketplace": True}))
        self.assertIn("New AgentFleet options are available", self.cli('--after-install').stdout)

    def test_no_hint_without_mods_when_only_plugin_features_remain(self):
        (self.paths.claude / ".claude-agents-config-install.json").write_text('{}')
        self.paths.store.mkdir(parents=True, exist_ok=True)
        asked = [f.key for f in wizard.FEATURES if f.key not in {"jev", "statusBand", "laneGuard", "tokenWeather", "brainMod", "sessionInfo"}]
        (self.paths.store / 'wizard.json').write_text(json.dumps({"format": "agentfleet.wizard.v1", "asked": asked, "version": "2.3.0"}))
        self.assertEqual(self.cli('--after-install').stdout, "")

    def test_registry_order(self):
        self.assertEqual([f.key for f in wizard.FEATURES], KEYS)
        self.assertIn("prices", wizard.FLEET_KEYS)
        introduced = {f.key: f.introduced for f in wizard.FEATURES}
        self.assertEqual({k for k, v in introduced.items() if v == "2.12.0"}, {"contextMode", "branchCheck", "brainMod", "providerUsage"})
        self.assertEqual({k for k, v in introduced.items() if v == "2.13.0"}, {"sessionInfo"})
        self.assertEqual([f.key for f in wizard.FEATURES[13:16]], ["learn", "agentBrainInstall", "agentBrain"])

    def test_groups_and_closing_pointers_are_interactive_only(self):
        _, _, output = self.interactive_run({"agentBrainInstall": False, "instructions": False})
        for heading in ("Context", "Lanes", "Status and reports", "agent-brain", "Updates and checks"):
            self.assertEqual(output.count(heading), 1)
        self.assertIn("Lane models: /fleet-setup inside Claude Code", output)
        self.assertIn("Provider and profiles: agentfleet use", output)
        self.assertIn("Model prices: agentfleet config prices", output)
        noninteractive = self.cli("--yes")
        self.assertNotIn("Context\n", noninteractive.stdout)
        self.assertNotIn("Lane models:", noninteractive.stdout)

    def test_all_skipped_plugin_group_is_not_printed(self):
        (self.paths.claude / ".claude-agents-config-install.json").write_text('{}')
        asked = [f.key for f in wizard.FEATURES if f.key not in {"jev", "statusBand", "laneGuard", "tokenWeather", "brainMod", "sessionInfo"}]
        self.paths.store.mkdir(parents=True, exist_ok=True)
        (self.paths.store / "wizard.json").write_text(json.dumps({"format": "agentfleet.wizard.v1", "asked": asked}))
        class Terminal(io.StringIO):
            def isatty(self): return True
        output = Terminal()
        args = argparse.Namespace(after_install=True, yes=False, export=None, preset=None)
        with patch.object(sys, "stdin", Terminal()), contextlib.redirect_stdout(output), patch.object(wizard, "claude_cli_available", return_value=False):
            self.assertEqual(wizard.run(api, self.paths, args), 0)
        self.assertNotIn("Context", output.getvalue())
        self.assertNotIn("Status and reports", output.getvalue())

    def test_new_preferences_read_apply_and_validate(self):
        wizard.provider_catalog.write_preferences(self.home, {"branchCheck": False, "providerUsage": False})
        (self.paths.claude / "plugins" / "installed_plugins.json").parent.mkdir(parents=True, exist_ok=True)
        (self.paths.claude / "plugins" / "installed_plugins.json").write_text(json.dumps({"plugins": {"context-mode@context-mode": [{"scope": "user"}]}}))
        settings = self.paths.claude / "settings.json"
        settings.write_text(json.dumps({"enabledPlugins": {"context-mode@context-mode": True}}))
        fake_env = {**self.env, "PATH": self.env["PATH"]}
        with patch.dict(os.environ, fake_env):
            self.assertFalse(wizard.current(api, self.paths, "branchCheck"))
            self.assertFalse(wizard.current(api, self.paths, "providerUsage"))
            self.assertTrue(wizard.current(api, self.paths, "contextMode"))
        for key, setting in (("branchCheck", "branch-check"), ("providerUsage", "provider-usage"), ("contextMode", "context-mode")):
            with patch.object(api, "cmd_config", return_value=0) as config:
                self.assertEqual(wizard.apply(api, self.paths, key, True), 0)
            self.assertEqual((config.call_args.args[1].setting, config.call_args.args[1].value), (setting, "on"))
        self.assertEqual(wizard.PLUGIN_IDS["brainMod"], "brain@agentfleet")
        for key in ("branchCheck", "providerUsage", "contextMode", "brainMod"):
            self.assertTrue(wizard.validate_preset({"format": "agentfleet.preset.v1", key: True})[key])
        for key in ("branchCheck", "providerUsage", "contextMode", "brainMod"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                wizard.validate_preset({"format": "agentfleet.preset.v1", key: "on"})

    def test_update_asks_existing_users_only_the_new_questions(self):
        new = ["contextMode", "branchCheck", "brainMod", "providerUsage"]
        self.paths.store.mkdir(parents=True, exist_ok=True)
        asked = [f.key for f in wizard.FEATURES if f.key not in new]
        (self.paths.store / "wizard.json").write_text(json.dumps({"format": "agentfleet.wizard.v1", "asked": asked}))
        class Terminal(io.StringIO):
            def isatty(self): return True
        output = Terminal()
        prompted = []
        args = argparse.Namespace(after_install=True, yes=False, export=None, preset=None)
        with patch.object(wizard, "claude_cli_available", return_value=True), \
                patch.object(wizard, "prompt", side_effect=lambda feature, value: prompted.append(feature.key) or value), \
                patch.object(wizard, "apply", return_value=0), \
                patch.object(sys, "stdin", Terminal()), contextlib.redirect_stdout(output):
            self.assertEqual(wizard.run(api, self.paths, args), 0)
        self.assertEqual(prompted, new)
        self.assertNotIn("Updates and checks", output.getvalue())
        self.assertNotIn("Lane models:", output.getvalue())
        state = json.loads((self.paths.store / "wizard.json").read_text())
        self.assertTrue(set(new).issubset(state["asked"]))

    def test_context_mode_skips_when_claude_cli_is_missing(self):
        original = wizard.current
        class Terminal(io.StringIO):
            def isatty(self): return True
        output = Terminal()
        prompted = []
        args = argparse.Namespace(after_install=False, yes=False, export=None, preset=None)
        with patch.object(wizard, "claude_cli_available", return_value=False), \
                patch.object(wizard, "current", side_effect=lambda api, paths, key: original(api, paths, key) if key != "contextMode" else False), \
                patch.object(wizard, "prompt", side_effect=lambda feature, value: prompted.append(feature.key) or value), \
                patch.object(sys, "stdin", Terminal()), contextlib.redirect_stdout(output):
            self.assertEqual(wizard.run(api, self.paths, args), 0)
        self.assertNotIn("contextMode", prompted)
        state = json.loads((self.paths.store / "wizard.json").read_text())
        self.assertNotIn("contextMode", state["asked"])

    def test_validation(self):
        for data in ({"unknown": True}, {"apiKey": "x"}, {"token": "x"}, {"secret": "x"}, {"tokenSecret": "x"}, {"compact": True}, {"compact": 19}, {"compact": 96}, {"reviewerAccess": []}, {"learn": 1}, {"jev": "yes"}, {"workStyle": "bogus"}, {"instructions": True}, {"agentBrainMemory": True}, {"agentBrainInstall": "yes"}):
            with self.subTest(data=data):
                result = self.cli("--preset", str(self.preset(data)))
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertFalse((self.paths.store / "wizard.json").exists())
                self.assertFalse((self.home / "commands.log").exists())

    def test_local_memory_is_skipped_when_agent_brain_is_off(self):
        wizard.provider_catalog.write_preferences(self.home, {"agentBrain": False})
        result = self.cli("--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        asked = json.loads((self.paths.store / 'wizard.json').read_text())['asked']
        self.assertIn("agentBrainLocalMemory", asked)
        self.assertFalse(wizard.agent_brain.managed_binary(self.home).exists())

    def test_local_memory_is_skipped_when_agent_brain_was_declined(self):
        wizard.provider_catalog.write_preferences(self.home, {"agentBrain": False})
        state = {"format": "agentfleet.wizard.v1", "asked": ["agentBrainInstall", "agentBrain"]}
        self.paths.store.mkdir(parents=True, exist_ok=True)
        (self.paths.store / "wizard.json").write_text(json.dumps(state))
        result = self.cli("--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        asked = json.loads((self.paths.store / 'wizard.json').read_text())['asked']
        self.assertIn("agentBrainLocalMemory", asked)

    def test_local_memory_already_active_is_skipped_and_saved(self):
        binary = wizard.agent_brain.managed_binary(self.home)
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\nprintf 'Memory:           active; 280 entries for test\\n'\n")
        binary.chmod(0o755)
        result = self.cli("--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        asked = json.loads((self.paths.store / 'wizard.json').read_text())['asked']
        self.assertIn("agentBrainLocalMemory", asked)
        self.assertTrue(wizard.provider_catalog.load_preferences(self.home)["agentBrainLocalMemory"])

    def test_apply_functions(self):
        args = argparse.Namespace(after_install=False, yes=False, export=None, preset=str(self.preset({"compact": "off", "reviewerAccess": "run", "learn": False, "autoUpdate": False, "jev": True, "statusBand": True, "laneGuard": True})))
        with patch.object(api, "cmd_config", return_value=0) as config, patch.object(api, "cmd_auto_update", return_value=0) as auto, patch.object(api, "cmd_jev", return_value=0) as jev, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(wizard.run(api, self.paths, args), 0)
        self.assertEqual([(c.args[1].setting, c.args[1].value) for c in config.call_args_list], [("compact", "off"), ("reviewer-access", "run"), ("learn", "off")])
        self.assertEqual(auto.call_args.args[1].action, "off")
        self.assertEqual(jev.call_args.args[1].action, "on")
        commands = (self.home / "commands.log").read_text()
        self.assertIn('"install", "fleet-status@agentfleet"', commands)
        self.assertIn('"install", "lane-guard@agentfleet"', commands)

    def test_plugin_install_enable_disable(self):
        for value in (True, False, True):
            result = self.cli('--preset', str(self.preset({'jev': value, 'statusBand': value, 'laneGuard': value, 'tokenWeather': value, 'brainMod': value, 'sessionInfo': value})))
            self.assertEqual(result.returncode, 0, result.stderr)
        calls = [json.loads(line) for line in (self.home / 'commands.log').read_text().splitlines()]
        for ident in wizard.PLUGIN_IDS.values():
            self.assertIn(['plugin', 'install', ident], calls)
            self.assertIn(['plugin', 'disable', ident], calls)
            self.assertIn(['plugin', 'enable', ident], calls)
        self.assertIn(["plugin", "install", "brain@agentfleet"], calls)

    def test_key_skip_and_environment(self):
        with patch.object(wizard.getpass, 'getpass', return_value='') as ask, contextlib.redirect_stdout(io.StringIO()) as output:
            wizard.ask_key(api, self.paths)
        self.assertIn('Set the key later with /plugin', output.getvalue())
        self.assertEqual(ask.call_count, 1)
        with patch.dict(os.environ, {'TYPESAFE_API_KEY': 'ts-' + '1234567890' * 2}), patch.object(wizard.getpass, 'getpass') as ask:
            wizard.ask_key(api, self.paths)
        ask.assert_not_called()

    def test_export_round_trip(self):
        path = self.home / "export.json"
        result = self.cli("--export", str(path))
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(path.read_text())
        self.assertEqual(list(data), ["format"] + [key for key in KEYS if key not in ACTIONS])
        self.assertIs(data["agentBrainInstall"], False)
        # Never turned on here, so the question's suggested yes is not exported.
        self.assertIs(data["agentBrainLocalMemory"], False)
        if os.name == "posix":
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)
        self.assertEqual(wizard.validate_preset(data), {k: data[k] for k in KEYS if k not in ACTIONS})
        self.assertEqual(self.cli("--preset", str(path)).returncode, 0)
        wizard.provider_catalog.write_preferences(self.home, {"prices": {"claude-opus-5.5": {"input": 5, "output": 25}}})
        self.assertEqual(self.cli("--export", str(path)).returncode, 0)
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("prices", data)
        data["prices"] = {"claude-opus-5.5": {"input": 5, "output": 25, "cacheRead": 0.5}}
        self.assertEqual(wizard.validate_preset(data)["prices"], {"claude-opus-5.5": {"input": 5.0, "output": 25.0, "cacheRead": 0.5}})
        path.write_text(json.dumps(data), encoding="utf-8")
        applied = self.cli("--preset", str(path))
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual(wizard.provider_catalog.load_preferences(self.home)["prices"], data["prices"])
        exported = self.home / "export-with-prices.json"
        self.assertEqual(self.cli("--export", str(exported)).returncode, 0)
        round_trip = json.loads(exported.read_text(encoding="utf-8"))
        self.assertEqual(round_trip["prices"], data["prices"])
        self.assertEqual(wizard.validate_preset(round_trip)["prices"], data["prices"])
        wizard_state = self.paths.store / "wizard.json"
        if wizard_state.is_file():
            self.assertNotIn("prices", json.loads(wizard_state.read_text()).get("asked", []))

    def test_yes_and_after_install(self):
        result = self.cli("--after-install")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "New AgentFleet options are available: run agentfleet configure")
        result = self.cli("--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        state_path = self.paths.store / "wizard.json"
        self.assertEqual(set(json.loads(state_path.read_text())['asked']), set(KEYS) - {'instructions'})
        self.assertEqual(json.loads(state_path.read_text())['format'], 'agentfleet.wizard.v1')
        self.assertEqual(json.loads(state_path.read_text())['version'], '2.3.0')
        if os.name == "posix":
            self.assertEqual(state_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.cli("--after-install").stdout, "")
        self.assertEqual(self.cli().returncode, 2)

    def test_lock_failure_never_writes(self):
        with patch.object(wizard.provider_catalog, 'acquire_lock', return_value=(None, self.home / 'lock')), patch.object(wizard.getpass, 'getpass', return_value='ts-' + '1234567890' * 2):
            with self.assertRaises(RuntimeError):
                wizard.ask_key(api, self.paths)
            with self.assertRaises(RuntimeError):
                wizard.save_state(api, self.paths, self.paths.store / 'wizard.json', KEYS)
        self.assertNotIn('TYPESAFE_API_KEY', self.paths.settings.read_text())
        self.assertFalse((self.paths.store / 'wizard.json').exists())

    def test_key_storage_hidden(self):
        key = "ts-" + "1234567890" * 2
        output = io.StringIO()
        with patch.object(wizard.getpass, "getpass", return_value=key), contextlib.redirect_stdout(output):
            wizard.ask_key(api, self.paths)
        settings = json.loads(self.paths.settings.read_text())
        self.assertEqual(settings['env']['TYPESAFE_API_KEY'], key)
        self.assertEqual(settings['env']['KEEP'], 'value')
        if os.name == "posix":
            self.assertEqual(self.paths.settings.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(key, output.getvalue())
        self.assertNotIn('TYPESAFE_API_KEY', os.environ)
        exported = self.home / 'export.json'
        self.assertEqual(self.cli('--export', str(exported)).returncode, 0)
        self.assertNotIn(key, exported.read_text())

    def test_mods_skipped(self):
        (self.paths.claude / ".claude-agents-config-install.json").write_text('{}')
        result = self.cli('--yes')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count(wizard.MODS_MESSAGE), 1)
        self.assertFalse((self.home / 'commands.log').exists())
        asked = json.loads((self.paths.store / 'wizard.json').read_text())['asked']
        self.assertTrue({'compact', 'reviewerAccess', 'learn', 'autoUpdate', 'workStyle', 'agentBrain'}.issubset(asked))
        self.assertNotIn('instructions', asked)

    def test_apply_failure(self):
        args = argparse.Namespace(after_install=False, yes=False, export=None, preset=str(self.preset({'learn': False})))
        with patch.object(api, 'cmd_config', return_value=1), contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(wizard.run(api, self.paths, args), 1)
        self.assertIn('learn', error.getvalue())
        self.assertFalse((self.paths.store / 'wizard.json').exists())

    def fake_agent_brain_install(self, paths, args):
        managed = wizard.agent_brain.managed_binary(self.home)
        managed.parent.mkdir(parents=True, exist_ok=True)
        managed.write_text("fake")
        return 0

    def test_preset_installs_and_connects_agent_brain(self):
        args = argparse.Namespace(after_install=False, yes=False, export=None, preset=str(self.preset({"agentBrainInstall": True})))
        with patch.object(api, "cmd_config", side_effect=self.fake_agent_brain_install) as config, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(wizard.run(api, self.paths, args), 0)
        self.assertEqual([(c.args[1].setting, c.args[1].value) for c in config.call_args_list], [("agent-brain", "on")])
        asked = json.loads((self.paths.store / 'wizard.json').read_text())['asked']
        self.assertTrue({'agentBrainInstall', 'agentBrain'}.issubset(asked))

    def test_preset_can_install_agent_brain_unconnected(self):
        preset = self.preset({"agentBrainInstall": True, "agentBrain": False})
        args = argparse.Namespace(after_install=False, yes=False, export=None, preset=str(preset))
        with patch.object(api, "cmd_config", side_effect=self.fake_agent_brain_install) as config, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(wizard.run(api, self.paths, args), 0)
        self.assertEqual([(c.args[1].setting, c.args[1].value) for c in config.call_args_list],
                         [("agent-brain", "on"), ("agent-brain", "off")])

    def test_failed_agent_brain_install_is_offered_again(self):
        args = argparse.Namespace(after_install=False, yes=False, export=None, preset=str(self.preset({"agentBrainInstall": True})))
        with patch.object(api, "cmd_config", return_value=1), contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(wizard.run(api, self.paths, args), 0)
        self.assertIn("agentfleet config agent-brain on", error.getvalue())
        state = self.paths.store / 'wizard.json'
        asked = json.loads(state.read_text())['asked'] if state.exists() else []
        # Connecting it waits for it too.
        for key in ('agentBrainInstall', 'agentBrain'):
            self.assertNotIn(key, asked)

    def test_yes_never_installs_agent_brain(self):
        with patch.object(api, "cmd_config", return_value=0) as config, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(wizard.run(api, self.paths, argparse.Namespace(after_install=False, yes=True, export=None, preset=None)), 0)
        self.assertNotIn(("agent-brain", "on"), [(c.args[1].setting, c.args[1].value) for c in config.call_args_list])
        self.assertFalse(wizard.agent_brain.managed_binary(self.home).exists())

    def interactive_run(self, answers, after_install=False):
        class Terminal(io.StringIO):
            def isatty(self):
                return True
        prompted = []
        def fake_prompt(feature, value):
            prompted.append(feature.key)
            return answers.get(feature.key, value)
        args = argparse.Namespace(after_install=after_install, yes=False, export=None, preset=None)
        output = Terminal()
        original_current = wizard.current
        with patch.object(wizard, "prompt", side_effect=fake_prompt), \
                patch.object(wizard, "plugins", return_value=[]), \
                patch.object(api, "cmd_config", side_effect=self.fake_agent_brain_install) as config, \
                patch.object(wizard, "claude_cli_available", return_value=True), \
                patch.object(wizard, "current", side_effect=lambda api, paths, key: False if key in {"branchCheck", "providerUsage", "contextMode"} else original_current(api, paths, key)), \
                patch.object(sys, "stdin", Terminal()), contextlib.redirect_stdout(output):
            self.assertEqual(wizard.run(api, self.paths, args), 0)
        return prompted, [(c.args[1].setting, c.args[1].value) for c in config.call_args_list], output.getvalue()

    def test_interactive_install_connects_agent_brain_then_offers_memory(self):
        # None: Enter, which keeps memory off.
        for after_install, memory in ((False, True), (True, True), (False, False), (False, None)):
            with self.subTest(after_install=after_install, memory=memory):
                (self.paths.store / 'wizard.json').unlink(missing_ok=True)
                wizard.agent_brain.managed_binary(self.home).unlink(missing_ok=True)
                answers = {"agentBrainInstall": True, "instructions": False}
                if memory is not None:
                    answers["agentBrainLocalMemory"] = memory
                prompted, calls, output = self.interactive_run(answers, after_install=after_install)
                # Installing it answers whether to connect it; memory is asked next.
                self.assertIn("agentBrainInstall", prompted)
                self.assertNotIn("agentBrain", prompted)
                self.assertIn("agentBrainLocalMemory", prompted)
                self.assertEqual(calls, [("agent-brain", "on")] + ([("agent-brain-memory", "on")] if memory else []))
                self.assertIn("agent-brain is installed and connected", output)
                self.assertIn("Lane models: /fleet-setup inside Claude Code", output)
                for word in ("login", "sign in"):
                    self.assertNotIn(word, output)
                asked = json.loads((self.paths.store / 'wizard.json').read_text())['asked']
                self.assertTrue({'agentBrainInstall', 'agentBrain', 'agentBrainLocalMemory'}.issubset(asked))
        self.assertIs(wizard.provider_catalog.load_preferences(self.home)["agentBrainLocalMemory"], False)

    def test_yes_and_presets_with_memory(self):
        binary = wizard.agent_brain.managed_binary(self.home)
        binary.parent.mkdir(parents=True)
        binary.write_text("fake")
        # --yes never turns memory on: it needs a yes.
        with patch.object(api, "cmd_config", return_value=0) as config, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(wizard.run(api, self.paths, argparse.Namespace(after_install=False, yes=True, export=None, preset=None)), 0)
        self.assertNotIn(("agent-brain-memory", "on"), [(c.args[1].setting, c.args[1].value) for c in config.call_args_list])
        self.assertIs(wizard.provider_catalog.load_preferences(self.home)["agentBrainLocalMemory"], False)
        # A preset turns it on, and off where it is on, even with agent-brain
        # disconnected.
        for chosen, active, connected, expected in ((True, False, None, [("agent-brain-memory", "on")]),
                                                    (False, True, None, [("agent-brain-memory", "off")]),
                                                    (False, True, False, [("agent-brain-memory", "off")]),
                                                    (False, False, None, [])):
            if connected is not None:
                wizard.provider_catalog.write_preferences(self.home, {"agentBrain": connected})
            with self.subTest(chosen=chosen, active=active, connected=connected), \
                    patch.object(wizard.agent_brain, "memory_active", return_value=active), \
                    patch.object(api, "cmd_config", return_value=0) as config, contextlib.redirect_stdout(io.StringIO()):
                args = argparse.Namespace(after_install=False, yes=False, export=None,
                                          preset=str(self.preset({"agentBrainLocalMemory": chosen})))
                self.assertEqual(wizard.run(api, self.paths, args), 0)
            self.assertEqual([(c.args[1].setting, c.args[1].value) for c in config.call_args_list], expected)

    def test_export_writes_memory_as_agent_brain_reports_it(self):
        # Turned on through AgentFleet, then off with agent-brain itself.
        wizard.provider_catalog.write_preferences(self.home, {"agentBrainLocalMemory": True})
        path = self.home / "export.json"
        for active in (False, True):
            with self.subTest(active=active), patch.object(wizard.agent_brain, "memory_active", return_value=active), \
                    contextlib.redirect_stdout(io.StringIO()):
                args = argparse.Namespace(after_install=False, yes=False, export=str(path), preset=None)
                self.assertEqual(wizard.run(api, self.paths, args), 0)
            self.assertIs(json.loads(path.read_text())["agentBrainLocalMemory"], active)

    def test_failed_memory_apply_leaves_the_question_open(self):
        binary = wizard.agent_brain.managed_binary(self.home)
        binary.parent.mkdir(parents=True)
        binary.write_text("fake")
        with patch.object(api, "cmd_config", return_value=1), contextlib.redirect_stdout(io.StringIO()):
            args = argparse.Namespace(after_install=False, yes=False, export=None,
                                      preset=str(self.preset({"agentBrainLocalMemory": True})))
            self.assertEqual(wizard.run(api, self.paths, args), 0)
        state = self.paths.store / 'wizard.json'
        asked = json.loads(state.read_text())['asked'] if state.exists() else []
        self.assertNotIn('agentBrainLocalMemory', asked)

    def test_no_question_mentions_an_agent_brain_account_or_memory(self):
        for feature in wizard.FEATURES:
            for word in ("Pro ", "sign in", "login"):
                self.assertNotIn(word, feature.question, feature.key)
        self.assertIn("memory", next(f for f in wizard.FEATURES if f.key == "agentBrainLocalMemory").question)

    @unittest.skipUnless(os.name == 'posix', 'pty requires POSIX')
    def test_interactive_pty(self):
        import pty
        import select
        import signal
        pid, fd = pty.fork()
        if pid == 0:
            os.execve(sys.executable, self.command(), self.env)
        output = b''
        index = 0
        # agent-brain is not installed, so only its install question is
        # asked, and declined; local memory is skipped. The instruction check
        # is declined so no headless Claude run starts.
        questions = [f for f in wizard.FEATURES if f.key not in {'agentBrain', 'agentBrainLocalMemory'}]
        declined = ('agentBrainInstall', 'instructions')
        deadline = time.monotonic() + 20
        reaped = False
        try:
            while time.monotonic() < deadline:
                ready, _, _ = select.select([fd], [], [], .1)
                if ready:
                    try:
                        chunk = os.read(fd, 4096)
                    except OSError:
                        break
                    if not chunk:
                        break
                    output += chunk
                    text = output.decode('utf-8', errors='replace')
                    if index < len(questions) and questions[index].question in text:
                        os.write(fd, b'n\n' if questions[index].key in declined else b'\n')
                        index += 1
                waited, status = os.waitpid(pid, os.WNOHANG)
                if waited:
                    reaped = True
                    self.assertEqual(os.waitstatus_to_exitcode(status), 0, output.decode(errors='replace'))
                    break
            if not reaped:
                waited, status = os.waitpid(pid, os.WNOHANG)
                if waited:
                    reaped = True
                    self.assertEqual(os.waitstatus_to_exitcode(status), 0, output.decode(errors='replace'))
            self.assertTrue(reaped, 'interactive wizard exceeded deadline')
            self.assertEqual(index, len(questions))
        finally:
            if not reaped:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            os.close(fd)


if __name__ == '__main__':
    unittest.main()
