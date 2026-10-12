#!/usr/bin/env python3
"""Regenerate manifest.json and checksums.sha256 from the working tree.

The file set is exactly what the installer's inventory preflight expects:
every file under the bundle root except .git, the repository-only
directories the installer excludes, and the two index files themselves.
Modes are normalized to 0o755 (executable) or 0o644, and the working-tree
files are chmod-ed to match so the installer's mode checks pass.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
EXCLUDED = {".ai", ".claude", ".commandcode", ".kilo", ".github", "site", "packaging", "dist"}
INDEX_FILES = {"manifest.json", "checksums.sha256"}


def index_modes() -> dict[str, int]:
    proc = subprocess.run(["git", "ls-files", "-s", "-z"], cwd=ROOT, capture_output=True, check=True)
    modes = {}
    for entry in proc.stdout.split(b"\0"):
        if entry:
            meta, path = entry.split(b"\t", 1)
            modes[path.decode("utf-8")] = 0o755 if meta.split()[0] == b"100755" else 0o644
    return modes


def main() -> int:
    tracked = index_modes()
    files = []
    for path in sorted(ROOT.rglob("*"), key=lambda item: item.relative_to(ROOT).as_posix()):
        rel_parts = path.relative_to(ROOT).parts
        if ".git" in path.parts or rel_parts[0] in EXCLUDED or path.is_dir():
            continue
        if "__pycache__" in rel_parts:
            print(f"remove generated cache first: {path}", file=sys.stderr)
            return 1
        rel = path.relative_to(ROOT).as_posix()
        parts = path.relative_to(ROOT).parts
        if len(parts) >= 3 and parts[0] == "mods" and any(
            parts[index:index + 2] == (".claude-plugin", "types") for index in range(len(parts) - 1)
        ):
            continue
        if rel in INDEX_FILES:
            continue
        if rel in tracked:
            mode = tracked[rel]
        else:
            mode = 0o755 if path.stat().st_mode & stat.S_IXUSR else 0o644
        os.chmod(path, mode)
        files.append({"path": rel, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "mode": oct(mode)})
    manifest_path = ROOT / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    manifest["files"] = files
    manifest_path.write_bytes((json.dumps(manifest, indent=2) + "\n").encode("utf-8"))
    (ROOT / "checksums.sha256").write_bytes("".join(f"{item['sha256']}  {item['path']}\n" for item in files).encode("utf-8"))
    os.chmod(manifest_path, 0o644)
    os.chmod(ROOT / "checksums.sha256", 0o644)
    print(f"manifest {manifest['version']}: {len(files)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
