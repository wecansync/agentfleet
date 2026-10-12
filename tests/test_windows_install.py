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


if __name__ == "__main__":
    unittest.main()
