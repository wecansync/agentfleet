"""Hook interpreters: what the installer pins, and what doctor reports when one is gone."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import importlib.util
import tempfile
import unittest
from unittest import mock

from tests.test_install_220 import DOCTOR, INSTALLER, PYTHON, ROOT, new_state, write_fake_claude

NODE = shutil.which("node")


def hook_command(home: Path, kind: str) -> str:
    settings = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))
    for groups in settings.get("hooks", {}).values():
        for group in groups:
            for hook in group.get("hooks", []):
                command = hook.get("command", "")
                if command.endswith(f"claude-agents-config:{kind}"):
                    return command
    raise AssertionError(f"no {kind} hook installed")


def runtime(command: str) -> str:
    return shlex.split(command.split(" # ", 1)[0])[0]


def with_runtime(command: str, new: Path) -> str:
    body, marker = command.split(" # ", 1)
    return shlex.join([str(new), *shlex.split(body)[1:]]) + " # " + marker


def set_hook_command(home: Path, kind: str, command: str) -> None:
    path = home / ".claude" / "settings.json"
    settings = json.loads(path.read_text(encoding="utf-8"))
    for groups in settings.get("hooks", {}).values():
        for group in groups:
            for hook in group.get("hooks", []):
                if hook.get("command", "").endswith(f"claude-agents-config:{kind}"):
                    hook["command"] = command
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")


@unittest.skipUnless(NODE and os.name != "nt", "needs node and symlinks")
class HookRuntimeTests(unittest.TestCase):
    def setUp(self):
        raw = tempfile.TemporaryDirectory(prefix="agentfleet hook runtime ")
        self.addCleanup(raw.cleanup)
        self.root = Path(raw.name)
        self.fake_bin = self.root / "fake-bin"
        self.fake_bin.mkdir()
        write_fake_claude(self.fake_bin)
        self.state_path = self.root / "claude-state.json"
        self.log_path = self.root / "claude-calls.jsonl"
        self.state_path.write_text(json.dumps(new_state()), encoding="utf-8")
        self.real_node = Path(NODE).resolve()
        self.home = self.root / "home"
        self.system_path = [path for path in ("/usr/bin", "/bin") if Path(path).is_dir()]

    def node_link(self, name: str) -> Path:
        # Like Homebrew's /opt/homebrew/bin/node: a stable link to a versioned binary.
        directory = self.root / name
        directory.mkdir()
        (directory / "node").symlink_to(self.real_node)
        return directory

    def env(self, node_dir: Path) -> dict[str, str]:
        return {
            "PATH": os.pathsep.join([str(self.fake_bin), str(node_dir), *self.system_path]),
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "AGENTFLEET_NONINTERACTIVE": "1",
            "AGENTFLEET_NO_CONFIGURE": "1",
            "FAKE_CLAUDE_STATE": str(self.state_path),
            "FAKE_CLAUDE_LOG": str(self.log_path),
        }

    def install(self, node_dir: Path):
        command = [PYTHON, "-B", str(INSTALLER), "--apply", "--no-wizard", "--provider", "native",
                   "--home", str(self.home), "--config-home", str(self.home / ".config"),
                   "--prefix", str(self.home / ".local")]
        result = subprocess.run(command, cwd=ROOT, env=self.env(node_dir), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def doctor(self, node_dir: Path):
        command = [PYTHON, "-B", str(DOCTOR), "--check", "--home", str(self.home),
                   "--config-home", str(self.home / ".config"), "--prefix", str(self.home / ".local"),
                   "--bundle", str(ROOT)]
        return subprocess.run(command, cwd=ROOT, env=self.env(node_dir), text=True, capture_output=True)

    def test_model_sync_pins_the_node_on_path_not_its_link_target(self):
        node_dir = self.node_link("stable-bin")
        self.install(node_dir)
        self.assertEqual(runtime(hook_command(self.home, "model-sync")), str(node_dir / "node"))

    def test_update_repins_a_node_that_no_longer_exists(self):
        old = self.node_link("old-node")
        self.install(old)
        shutil.rmtree(old)
        new = self.node_link("new-node")
        self.install(new)
        self.assertEqual(runtime(hook_command(self.home, "model-sync")), str(new / "node"))
        result = self.doctor(new)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_doctor_reports_a_missing_hook_interpreter(self):
        node_dir = self.node_link("node-bin")
        self.install(node_dir)
        missing = self.root / "gone" / "node"
        set_hook_command(self.home, "model-sync", with_runtime(hook_command(self.home, "model-sync"), missing))
        result = self.doctor(node_dir)
        self.assertNotEqual(result.returncode, 0)
        output = result.stderr + result.stdout
        self.assertIn(f"model-sync hook runs {missing}, which no longer exists", output)
        self.assertIn("agentfleet update", output)

    def test_doctor_reports_a_missing_python_with_the_install_command(self):
        node_dir = self.node_link("node-bin")
        self.install(node_dir)
        missing = self.root / "gone" / "python3"
        set_hook_command(self.home, "route", with_runtime(hook_command(self.home, "route"), missing))
        result = self.doctor(node_dir)
        self.assertNotEqual(result.returncode, 0)
        output = result.stderr + result.stdout
        self.assertIn(f"route hook runs {missing}, which no longer exists", output)
        self.assertIn("install.sh", output)

    def test_update_keeps_an_edited_hook_whose_node_is_gone_and_doctor_reports_it(self):
        old = self.node_link("old-node")
        self.install(old)
        command = hook_command(self.home, "model-sync")
        body, marker = command.split(" # ", 1)
        edited = body + " --edited-by-user # " + marker
        set_hook_command(self.home, "model-sync", edited)
        shutil.rmtree(old)
        new = self.node_link("new-node")
        self.install(new)  # must not fail: the user's edit is kept as it is
        self.assertEqual(hook_command(self.home, "model-sync"), edited)
        result = self.doctor(new)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"model-sync hook runs {old / 'node'}, which no longer exists", result.stderr + result.stdout)

    def test_doctor_ignores_a_marker_that_is_not_the_trailing_comment(self):
        node_dir = self.node_link("node-bin")
        self.install(node_dir)
        path = self.home / ".claude" / "settings.json"
        settings = json.loads(path.read_text(encoding="utf-8"))
        settings["hooks"].setdefault("Stop", []).append({"hooks": [{
            "type": "command", "command": "/missing/other-tool --message claude-agents-config:example"}]})
        path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        result = self.doctor(node_dir)
        self.assertNotIn("/missing/other-tool", result.stderr + result.stdout)

    def test_node_path_keeps_a_parent_step_after_a_link(self):
        # PATH=link/.. with link -> real/sub must still find real/node.
        real = self.root / "real"
        (real / "sub").mkdir(parents=True)
        (real / "node").symlink_to(self.real_node)
        (self.root / "link").symlink_to(real / "sub")
        spec = importlib.util.spec_from_file_location("agentfleet_install_under_test", INSTALLER)
        install = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(install)
        with mock.patch.dict(os.environ, {"PATH": str(self.root / "link" / "..")}):
            found = install.node_path()
        self.assertIsNotNone(found)
        self.assertEqual(found.resolve(), self.real_node)


if __name__ == "__main__":
    unittest.main()
