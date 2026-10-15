"""Per-lane model lists, per-lane effort and reserved models: presets and claude-fleet-setup flags."""
import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import fleet_configure as wizard


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(ROOT / path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    loader.exec_module(module)
    return module


api = load("preset_lanes_agentfleet", "bin/agentfleet")
setup = load("preset_lanes_fleet_setup", "bin/claude-fleet-setup")
SETUP = str(ROOT / "bin/claude-fleet-setup")


def lanes_fixture():
    return {"lanes": {"plan": {"model": "opus", "tier": "deep"}, "implement": {"model": "sonnet", "tier": "balanced"},
                      "review": {"model": "haiku", "tier": "fast"}}}


class ValidatePresetTests(unittest.TestCase):
    def check(self, **fields):
        return wizard.validate_preset({"format": "agentfleet.preset.v1", **fields})

    def test_accepts_valid_presets(self):
        data = {"reservedModels": ["opus", "model-x[1m]"],
                "lanes": {"plan": {"models": ["opus", "sonnet"], "effort": "max"}, "implement-alt": {"models": ["sonnet"]}, "r2": {"effort": "low"}}}
        self.assertEqual(self.check(**data), data)
        self.assertEqual(self.check(reservedModels=[]), {"reservedModels": []})
        for level in ("low", "medium", "high", "xhigh", "max"):
            self.assertIn("lanes", self.check(lanes={"plan": {"models": ["opus"], "effort": level}}))

    def test_rejects_bad_models(self):
        for models in ([], ["a"] * 5, [""], ["  "], [1], "opus", None, ["a,b"]):
            with self.subTest(models=models):
                with self.assertRaises(ValueError):
                    self.check(lanes={"plan": {"models": models}})
        for reserved in ("opus", [""], [None], [3], {"a": 1}, ["a,b"]):
            with self.subTest(reserved=reserved):
                with self.assertRaises(ValueError):
                    self.check(reservedModels=reserved)

    def test_rejects_bad_effort_and_lane_fields(self):
        for lane in ({"models": ["a"], "effort": "extreme"}, {"models": ["a"], "effort": 3}, {"models": ["a"], "effort": ""},
                     {"models": ["a"], "tier": "deep"}, {"models": ["a"], "preferred": ["b"]}, {}, [], "plan"):
            with self.subTest(lane=lane):
                with self.assertRaises(ValueError):
                    self.check(lanes={"plan": lane})
        for name in ("Plan", "-plan", "pl an", "", "plan/x"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    self.check(lanes={name: {"models": ["a"]}})
        with self.assertRaises(ValueError):
            self.check(lanes=[])


class FleetSetupFlagTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.config = Path(self.tmp.name) / "config"
        (self.home / ".claude").mkdir(parents=True)
        (self.config / "delegate-skills").mkdir(parents=True)
        self.mirrors = [self.home / ".claude" / "fleet.json", self.config / "delegate-skills" / "config.json"]
        self.write_fleet(lanes_fixture())
        # A gateway profile, so models beyond the native aliases are valid.
        token = "tok-" + "x" * 12
        (self.home / ".claude" / "settings.json").write_text(json.dumps(
            {"env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:9", "ANTHROPIC_AUTH_TOKEN": token}}))

    def write_fleet(self, fleet):
        for path in self.mirrors:
            path.write_text(json.dumps(fleet))

    def fleet(self):
        first, second = (json.loads(path.read_text()) for path in self.mirrors)
        self.assertEqual(first, second, "both mirrors carry the same map")
        return first

    def choose(self, **kwargs):
        """update_fleet_choices without the reconciliation that follows it on the command line."""
        values = {"prefer": [], "tiers": [], "exclude": None, "include": None, "reserve": None, "unreserve": None,
                  "prefer_only": None, "effort": None, **kwargs}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            setup.update_fleet_choices(self.home, self.config, values["prefer"], values["tiers"], 4.0, values["exclude"],
                                       values["include"], values["reserve"], values["unreserve"], values["prefer_only"], values["effort"])
        return out.getvalue()

    def test_reserve_and_unreserve(self):
        out = self.choose(reserve=["model-x,model-y[1m]", "model-z"])
        self.assertIn("model-x: reserved (serves only lanes that name it)", out)
        self.assertEqual(self.fleet()["reservedModels"], ["model-x", "model-y[1m]", "model-z"])
        self.choose(reserve=["model-y"])
        self.assertEqual(self.fleet()["reservedModels"], ["model-x", "model-y[1m]", "model-z"], "deduped without the suffix")
        out = self.choose(unreserve=["model-y,nothing"])
        self.assertIn("model-y: no longer reserved", out)
        self.assertIn("nothing: was not reserved", out)
        self.assertEqual(self.fleet()["reservedModels"], ["model-x", "model-z"])
        self.choose(unreserve=["model-x", "model-z"])
        self.assertNotIn("reservedModels", self.fleet())

    def test_reserve_refuses_an_excluded_model(self):
        self.choose(exclude=["model-x"])
        with self.assertRaises(SystemExit) as caught:
            self.choose(reserve=["model-x"])
        self.assertEqual(str(caught.exception), "model-x is excluded; run --include first")
        self.assertNotIn("reservedModels", self.fleet())

    def test_prefer_only_and_prefer(self):
        out = self.choose(prefer_only=["plan=model-a,model-b"])
        self.assertIn("fleet-plan: uses only model-a, model-b", out)
        plan = self.fleet()["lanes"]["plan"]
        self.assertEqual((plan["preferred"], plan["model"], plan["preferredOnly"]), (["model-a", "model-b"], "model-a", True))
        self.choose(prefer=["plan=model-c"])
        plan = self.fleet()["lanes"]["plan"]
        self.assertEqual(plan["preferred"], ["model-c"])
        self.assertNotIn("preferredOnly", plan, "--prefer lifts the restriction")
        self.choose(prefer_only=["fleet-plan=model-a"])
        self.assertIs(self.fleet()["lanes"]["plan"]["preferredOnly"], True)
        self.choose(prefer_only=["plan="])
        plan = self.fleet()["lanes"]["plan"]
        self.assertNotIn("preferred", plan)
        self.assertNotIn("preferredOnly", plan)

    def test_effort(self):
        out = self.choose(effort=["plan=XHIGH", "review=low"])
        self.assertIn("fleet-plan: effort xhigh", out)
        lanes = self.fleet()["lanes"]
        self.assertEqual((lanes["plan"]["preferredEffort"], lanes["review"]["preferredEffort"]), ("xhigh", "low"))
        self.choose(effort=["plan="])
        self.assertNotIn("preferredEffort", self.fleet()["lanes"]["plan"])
        with self.assertRaises(SystemExit) as caught:
            self.choose(effort=["plan=extreme"])
        for level in ("low", "medium", "high", "xhigh", "max"):
            self.assertIn(level, str(caught.exception))
        self.assertEqual(self.fleet()["lanes"]["review"]["preferredEffort"], "low", "nothing is written on error")

    def test_same_validations_as_prefer(self):
        with self.assertRaises(SystemExit) as caught:
            self.choose(prefer_only=["nolane=model-a"])
        self.assertIn("unknown lane: nolane", str(caught.exception))
        with self.assertRaises(SystemExit):
            self.choose(effort=["nolane=low"])
        self.choose(exclude=["model-a"])
        with self.assertRaises(SystemExit) as caught:
            self.choose(prefer_only=["plan=model-a"])
        self.assertEqual(str(caught.exception), "model-a is excluded; run --include first")
        (self.home / ".claude" / "settings.json").write_text("{}")
        with self.assertRaises(SystemExit) as caught:
            self.choose(prefer_only=["plan=model-b"])
        self.assertIn("native profile", str(caught.exception))
        self.choose(prefer_only=["plan=opus,sonnet"])
        self.assertIs(self.fleet()["lanes"]["plan"]["preferredOnly"], True)
        # Native lanes take opus, sonnet or haiku by tier, so a reservation would do nothing.
        with self.assertRaises(SystemExit) as caught:
            self.choose(reserve=["haiku"])
        self.assertIn("needs a gateway profile", str(caught.exception))
        self.assertNotIn("reservedModels", self.fleet())

    def test_command_line_without_reconcile(self):
        """The flags reach both mirrors through the script itself (native profile: no network)."""
        (self.home / ".claude" / "settings.json").write_text("{}")
        bindir = Path(self.tmp.name) / "bin"
        bindir.mkdir()
        cli = bindir / "claude"
        cli.write_text("#!" + sys.executable + "\nprint('[]')\n")
        cli.chmod(0o755)
        node = shutil.which("node")
        env = {"PATH": os.pathsep.join([str(bindir), *([str(Path(node).parent)] if node else [])]), "HOME": str(self.home),
               "XDG_CONFIG_HOME": str(self.config), "PYTHONDONTWRITEBYTECODE": "1"}
        base = [sys.executable, "-B", SETUP, "--home", str(self.home), "--config-home", str(self.config)]
        result = subprocess.run([*base, "--prefer-only", "plan=opus,sonnet", "--effort", "plan=max"],
                                env=env, text=True, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("fleet-plan: uses only opus, sonnet", result.stdout)
        fleet = self.fleet()
        self.assertEqual((fleet["lanes"]["plan"]["preferredOnly"], fleet["lanes"]["plan"]["preferredEffort"]), (True, "max"))
        bad = subprocess.run([*base, "--effort", "plan=extreme"], env=env, text=True, capture_output=True, timeout=60)
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("xhigh", bad.stderr)


class PresetApplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.paths = api.Paths(argparse.Namespace(home=str(self.home), config_home=str(self.home / "config"), prefix=None))
        self.paths.claude.mkdir()
        self.paths.settings.write_text("{}")
        (self.paths.claude / ".claude-agents-config-install.json").write_text(json.dumps({"version": "2.3.0", "modsMarketplace": True}))
        bindir = self.home / "bin"
        bindir.mkdir()
        cli = bindir / "claude"
        cli.write_text("#!" + sys.executable + "\nprint('[]')\n")
        cli.chmod(0o755)
        env = {"HOME": str(self.home), "PATH": str(bindir)}
        patcher = patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.env = env

    def preset(self, data):
        path = self.home / "preset.json"
        path.write_text(json.dumps({"format": "agentfleet.preset.v1", **data}))
        return argparse.Namespace(after_install=False, yes=False, export=None, preset=str(path))

    def run_preset(self, data, code=0):
        with patch.object(api, "run_tool", return_value=code) as tool, contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            result = wizard.run(api, self.paths, self.preset(data))
        return result, tool, err.getvalue()

    def test_apply_runs_claude_fleet_setup_once(self):
        data = {"reservedModels": ["model-x", "model-y"],
                "lanes": {"plan": {"models": ["model-a", "model-b"], "effort": "high"}, "review": {"effort": "low"}, "implement": {"models": ["model-c"]}}}
        result, tool, _ = self.run_preset(data)
        self.assertEqual(result, 0)
        tool.assert_called_once_with(self.paths, "claude-fleet-setup.py", [
            "--reserve", "model-x,model-y", "--prefer-only", "plan=model-a,model-b", "--effort", "plan=high",
            "--effort", "review=low", "--prefer-only", "implement=model-c", "--reconcile"])
        self.assertFalse((self.paths.store / "wizard.json").exists(), "these keys are never recorded as asked")

    def test_apply_after_feature_values_and_failure_keeps_them(self):
        data = {"learn": False, "lanes": {"plan": {"models": ["model-a"]}}}
        with patch.object(api, "cmd_config", return_value=0) as config:
            result, tool, err = self.run_preset(data, code=1)
        self.assertEqual(result, 1)
        self.assertEqual(config.call_args.args[1].setting, "learn")
        self.assertIn("agentfleet configure: could not apply", err)
        state = json.loads((self.paths.store / "wizard.json").read_text())
        self.assertEqual(state["asked"], ["learn"])
        self.assertEqual(wizard.read_json(self.paths.store / "wizard.json")["asked"], ["learn"])

    def test_empty_keys_run_nothing(self):
        result, tool, _ = self.run_preset({"reservedModels": [], "lanes": {}})
        self.assertEqual(result, 0)
        tool.assert_not_called()

    def test_a_missing_tool_fails_the_run(self):
        with patch.object(api, "run_tool", side_effect=SystemExit(1)), contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(wizard.run(api, self.paths, self.preset({"reservedModels": ["model-x"]})), 1)

    def test_export_round_trips(self):
        fleet = lanes_fixture()
        fleet["reservedModels"] = ["model-x"]
        fleet["lanes"]["plan"].update({"preferred": ["model-a", "model-b"], "preferredOnly": True, "preferredEffort": "xhigh"})
        fleet["lanes"]["implement"].update({"preferred": ["model-c"]})
        fleet["lanes"]["review"].update({"preferredEffort": "low"})
        self.paths.fleet.write_text(json.dumps(fleet))
        out = self.home / "export.json"
        args = argparse.Namespace(after_install=False, yes=False, export=str(out), preset=None)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(wizard.run(api, self.paths, args), 0)
        data = json.loads(out.read_text())
        self.assertEqual(data["reservedModels"], ["model-x"])
        self.assertEqual(data["lanes"], {"plan": {"models": ["model-a", "model-b"], "effort": "xhigh"}, "review": {"effort": "low"}})
        checked = wizard.validate_preset(data)
        self.assertEqual(checked["lanes"], data["lanes"])
        result, tool, _ = self.run_preset({key: value for key, value in data.items() if key != "format"})
        self.assertEqual(result, 0)
        self.assertEqual(tool.call_args.args[2], ["--reserve", "model-x", "--prefer-only", "plan=model-a,model-b", "--effort", "plan=xhigh",
                                                  "--effort", "review=low", "--reconcile"])

    def test_export_without_fleet_choices_adds_nothing(self):
        out = self.home / "export.json"
        args = argparse.Namespace(after_install=False, yes=False, export=str(out), preset=None)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(wizard.run(api, self.paths, args), 0)
        data = json.loads(out.read_text())
        self.assertNotIn("reservedModels", data)
        self.assertNotIn("lanes", data)


if __name__ == "__main__":
    unittest.main()
