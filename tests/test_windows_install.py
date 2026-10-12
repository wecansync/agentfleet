#!/usr/bin/env python3
"""Installer checks found on Windows: PATH membership of the command folder and reinstall preferences."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

ROOT = Path(__file__).resolve().parent.parent


def load_installer():
    spec = importlib.util.spec_from_file_location("agentfleet_install_windows_install", ROOT / "bin/install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(os.name == "nt", "Windows PATH comparison")
class DirOnPathTests(unittest.TestCase):
    def setUp(self):
        self.install = load_installer()
        self.bin_dir = Path(r"C:\Users\Someone\.local\bin")

    def test_case_different_entry_counts_as_on_path(self):
        self.assertTrue(self.install.dir_on_path(self.bin_dir, r"C:\Windows;c:\users\someone\.local\bin"))

    def test_trailing_backslash_entry_counts_as_on_path(self):
        self.assertTrue(self.install.dir_on_path(self.bin_dir, "C:\\Windows;C:\\Users\\Someone\\.local\\bin\\"))

    def test_missing_folder_is_not_on_path(self):
        self.assertFalse(self.install.dir_on_path(self.bin_dir, r"C:\Windows;C:\Users\Someone\bin"))

    def test_empty_entries_are_ignored(self):
        self.assertFalse(self.install.dir_on_path(self.bin_dir, ";;C:\\Windows;;"))


class LearnSeedTests(unittest.TestCase):
    def test_reinstall_at_current_version_does_not_seed_learning_off(self):
        home = Path(tempfile.mkdtemp(prefix="af learn "))
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(home), "USERPROFILE": str(home), "XDG_CONFIG_HOME": str(home / ".config"), "PYTHONDONTWRITEBYTECODE": "1", "AGENTFLEET_MODS": "0", "AGENTFLEET_NO_CONFIGURE": "1"}
        command = [sys.executable, "-B", str(ROOT / "bin/install.py"), "--apply", "--home", str(home), "--provider", "native", "--no-wizard", "--no-mods", "--no-configure"]
        self.assertEqual(subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True).returncode, 0)
        conf = home / ".claude" / "agentfleet" / "config.json"
        if conf.is_file():
            conf.unlink()
        self.assertEqual(subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True).returncode, 0)
        self.assertFalse(conf.is_file() and "learn" in json.loads(conf.read_text(encoding="utf-8")))


@unittest.skipUnless(os.name == "nt", "runs the generated agentfleet.cmd wrapper")
class CmdWrapperSelfReplaceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="af cmd wrap "))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.home, self.config, self.store = self.root / "home", self.root / "config", self.root / "store"
        (self.home / ".claude").mkdir(parents=True)
        (self.home / ".claude/settings.json").write_bytes(b'{\n  "theme": "light-daltonized"\n}\n')
        version = (ROOT / "VERSION").read_text().strip()
        (self.store / "releases").mkdir(parents=True)
        (self.store / "current").write_text(version + "\n")
        shutil.copytree(ROOT, self.store / "releases" / version, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        self.env = {
            "PATH": os.environ.get("PATH", ""), "HOME": str(self.home), "XDG_CONFIG_HOME": str(self.config),
            "PYTHONDONTWRITEBYTECODE": "1", "AGENTFLEET_MODS": "0", "AGENTFLEET_NO_CONFIGURE": "1",
            "AGENTFLEET_HOME": str(self.store), "USERPROFILE": str(self.home),
            **{name: os.environ[name] for name in ("SYSTEMROOT", "COMSPEC", "PATHEXT", "TEMP", "TMP") if name in os.environ},
        }
        self.wrapper = self.home / ".local" / "bin" / "agentfleet.cmd"
        install = subprocess.run(
            [sys.executable, "-B", str(ROOT / "bin/install.py"), "--apply", "--home", str(self.home), "--provider", "native", "--no-wizard", "--no-mods", "--no-configure"],
            cwd=ROOT, env=self.env, text=True, capture_output=True,
        )
        self.assertEqual(install.returncode, 0, install.stdout + install.stderr)
        self.assertTrue(self.wrapper.is_file())

    def run_wrapper(self, *args):
        return subprocess.run(["cmd.exe", "/c", str(self.wrapper), *args], env=self.env, text=True, capture_output=True, timeout=120)

    def test_rollback_rewriting_the_wrapper_exits_cleanly(self):
        second = subprocess.run(
            [sys.executable, "-B", str(ROOT / "bin/install.py"), "--apply", "--home", str(self.home), "--provider", "native", "--no-wizard", "--no-mods", "--no-configure"],
            cwd=ROOT, env=self.env, text=True, capture_output=True,
        )
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        result = self.run_wrapper("rollback")
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertNotIn("batch file cannot be found", output.lower())

    def test_uninstall_deleting_the_wrapper_exits_cleanly(self):
        result = self.run_wrapper("uninstall")
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertNotIn("batch file cannot be found", output.lower())
        self.assertFalse(self.wrapper.exists(), "uninstall removes the wrapper it is running from")

    def test_failing_command_exit_code_propagates(self):
        self.assertNotIn("NoDefaultCurrentDirectoryInExePath", self.env, "the child env must not mask a current-directory lookup")
        result = self.run_wrapper("config", "compact", "10")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)


@unittest.skipUnless(os.name == "nt", "runs the generated wrappers against planted files")
class WrapperCurrentDirectoryDecoyTests(unittest.TestCase):
    """Decoys named like the launched program, in the working directory, must never run."""

    def setUp(self):
        self.installer = load_installer()
        self.root = Path(tempfile.mkdtemp(prefix="af decoy "))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.cwd = self.root / "cwd"
        self.cwd.mkdir()
        probe = self.root / "probe.py"
        probe.write_text("import sys\nprint('RAN', sys.argv[1:])\n", encoding="utf-8")
        self.node_probe = self.root / "probe.mjs"
        self.node_probe.write_text("console.log('RAN ' + process.argv.slice(2).join(','))\n", encoding="utf-8")
        self.probe = probe
        for name in ("python", "py", "node"):
            (self.cwd / f"{name}.cmd").write_text("@echo off\r\necho PLANTED-FROM-CWD\r\n", encoding="utf-8")
            (self.cwd / f"{name}.exe").write_bytes(b"not a program")
        # Child env without NoDefaultCurrentDirectoryInExePath, so only the wrapper's own resolution can mask the decoys.
        self.env = {k: v for k, v in os.environ.items() if k.upper() != "NODEFAULTCURRENTDIRECTORYINEXEPATH"}
        node = shutil.which("node")
        self.assertIsNotNone(node, "these tests need node on PATH")
        self.env["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), str(Path(node).parent), os.path.join(os.environ.get("SYSTEMROOT", r"C:\Windows"), "System32")])

    def write_wrapper(self, name, runtime, script, pinned=None):
        target = self.root / f"{name}.cmd"
        if pinned is None:
            body = self.installer.cmd_dispatch(script, runtime)
        else:
            with unittest.mock.patch.object(self.installer.sys, "executable", pinned):
                body = self.installer.cmd_dispatch(script, runtime)
        target.write_bytes(body)
        return target

    def run_wrapper(self, wrapper, *args):
        return subprocess.run(["cmd.exe", "/c", str(wrapper), *args], cwd=self.cwd, env=self.env, text=True, capture_output=True, timeout=60)

    def assert_real_program_ran(self, result):
        output = result.stdout + result.stderr
        self.assertIn("RAN", output, output)
        self.assertIn("a", output.split("RAN", 1)[1], output)
        self.assertNotIn("PLANTED-FROM-CWD", output)
        self.assertNotIn("not compatible", output, "a planted .exe in the working directory must not be launched")

    def test_python_wrapper_with_missing_pin_never_runs_a_decoy(self):
        wrapper = self.write_wrapper("py-fallback", "python", self.probe, pinned=r"C:\nonexistent\python.exe")
        self.assert_real_program_ran(self.run_wrapper(wrapper, "a"))

    def test_node_wrapper_never_runs_a_decoy(self):
        wrapper = self.write_wrapper("node-wrap", "node", self.node_probe)
        self.assert_real_program_ran(self.run_wrapper(wrapper, "a"))


if __name__ == "__main__":
    unittest.main()
