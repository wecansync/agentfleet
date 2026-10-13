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
KEYS = ["compact", "jev", "reviewerAccess", "learn", "statusBand", "laneGuard", "autoUpdate", "workStyle", "agentBrain", "instructions"]


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
        cli.write_text("#!" + sys.executable + "\nimport json,os,sys\nfrom pathlib import Path\np=Path(os.environ['HOME'])/'plugins.json'\ns=json.loads(p.read_text()) if p.exists() else []\nwith open(Path(os.environ['HOME'])/'commands.log','a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')\nif sys.argv[1:]==['plugin','list','--json']: print(json.dumps(s))\nelse:\n ident=sys.argv[-1]; action=sys.argv[-2]\n s=[v for v in s if v['id']!=ident]+[{'id':ident,'enabled':action!='disable'}]\n p.write_text(json.dumps(s))\n")
        cli.chmod(0o755)
        if os.name == "nt":
            # PATH lookup on Windows finds claude.CMD, not the extensionless script.
            (bindir / "claude.cmd").write_text(f'@"{sys.executable}" "%~dp0claude" %*\n', encoding="utf-8")
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
        (self.paths.store / 'wizard.json').write_text(json.dumps({"format": "agentfleet.wizard.v1", "asked": ['compact', 'reviewerAccess', 'learn', 'autoUpdate', 'workStyle', 'agentBrain', 'instructions'], "version": "2.3.0"}))
        self.assertEqual(self.cli('--after-install').stdout, "")

    def test_registry_order(self):
        self.assertEqual([f.key for f in wizard.FEATURES], KEYS)
        self.assertTrue(all(f.introduced == "2.3.0" for f in wizard.FEATURES[:7]))
        self.assertTrue(all(f.introduced == "2.4.0" for f in wizard.FEATURES[7:]))

    def test_validation(self):
        for data in ({"unknown": True}, {"apiKey": "x"}, {"token": "x"}, {"secret": "x"}, {"compact": True}, {"compact": 19}, {"compact": 96}, {"reviewerAccess": []}, {"learn": 1}, {"jev": "yes"}, {"workStyle": "bogus"}, {"instructions": True}):
            with self.subTest(data=data):
                result = self.cli("--preset", str(self.preset(data)))
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertFalse((self.paths.store / "wizard.json").exists())
                self.assertFalse((self.home / "commands.log").exists())

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
            result = self.cli('--preset', str(self.preset({'jev': value, 'statusBand': value, 'laneGuard': value})))
            self.assertEqual(result.returncode, 0, result.stderr)
        calls = [json.loads(line) for line in (self.home / 'commands.log').read_text().splitlines()]
        for ident in wizard.PLUGIN_IDS.values():
            self.assertIn(['plugin', 'install', ident], calls)
            self.assertIn(['plugin', 'disable', ident], calls)
            self.assertIn(['plugin', 'enable', ident], calls)

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
        self.assertEqual(list(data), ["format"] + [key for key in KEYS if key != "instructions"])
        if os.name == "posix":
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)
        self.assertEqual(wizard.validate_preset(data), {k: data[k] for k in KEYS if k != "instructions"})
        self.assertEqual(self.cli("--preset", str(path)).returncode, 0)

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
        # agent-brain is not on this PATH, so its question is skipped; the
        # instruction check is declined so no headless Claude run starts.
        questions = [f for f in wizard.FEATURES if f.key != 'agentBrain']
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
                        os.write(fd, b'n\n' if questions[index].key == 'instructions' else b'\n')
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
