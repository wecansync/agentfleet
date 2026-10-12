#!/usr/bin/env python3
"""packaging/update-manifest.py takes file modes from the git index, so a
regeneration on Windows keeps executables as 0o755 and writes LF output."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "packaging" / "update-manifest.py"
sys.dont_write_bytecode = True


def scratch_bundle(raw: str) -> Path:
    """A git repository with one executable and one plain tracked file."""
    repo = Path(raw)
    (repo / "packaging").mkdir()
    shutil.copy(SCRIPT, repo / "packaging" / "update-manifest.py")
    (repo / "VERSION").write_text("9.9.9\n", encoding="utf-8")
    (repo / "manifest.json").write_text(json.dumps({"version": "9.9.9", "files": []}), encoding="utf-8")
    (repo / "checksums.sha256").write_text("", encoding="utf-8")
    (repo / "bin").mkdir()
    (repo / "bin" / "tool").write_text("#!/bin/sh\necho ok\n", encoding="utf-8", newline="\n")
    (repo / "notes.txt").write_text("plain\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "bin/tool", "notes.txt", "VERSION"], cwd=repo, check=True)
    subprocess.run(["git", "update-index", "--chmod=+x", "bin/tool"], cwd=repo, check=True)
    (repo / "bin" / "tool").chmod(0o644)
    return repo


class UpdateManifestModeTests(unittest.TestCase):
    def run_script(self, repo: Path) -> None:
        proc = subprocess.run(
            [sys.executable, "-B", str(repo / "packaging" / "update-manifest.py")],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_modes_come_from_git_index(self):
        with tempfile.TemporaryDirectory(prefix="af-manifest-mode-") as raw:
            repo = scratch_bundle(raw)
            self.run_script(repo)
            modes = {item["path"]: item["mode"] for item in json.loads((repo / "manifest.json").read_text(encoding="utf-8"))["files"]}
            self.assertEqual(modes["bin/tool"], "0o755")
            self.assertEqual(modes["notes.txt"], "0o644")

    def test_file_order_is_stable(self):
        with tempfile.TemporaryDirectory(prefix="af-manifest-order-") as raw:
            repo = scratch_bundle(raw)
            (repo / "a.txt").write_text("a\n", encoding="utf-8", newline="\n")
            (repo / "B.txt").write_text("B\n", encoding="utf-8", newline="\n")
            self.run_script(repo)
            paths = [item["path"] for item in json.loads((repo / "manifest.json").read_text(encoding="utf-8"))["files"]]
            self.assertEqual(paths, sorted(paths, key=lambda item: item.encode("utf-8")))

    def test_index_files_are_written_with_lf(self):
        with tempfile.TemporaryDirectory(prefix="af-manifest-eol-") as raw:
            repo = scratch_bundle(raw)
            self.run_script(repo)
            self.assertNotIn(b"\r", (repo / "manifest.json").read_bytes())
            self.assertNotIn(b"\r", (repo / "checksums.sha256").read_bytes())


if __name__ == "__main__":
    unittest.main()
