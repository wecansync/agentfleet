#!/usr/bin/env python3
"""Check one synthetic benchmark task. Standard library only.

    python3 -I check-task.py <baseline-dir> <task-name>

<baseline-dir> is a copy of the task's seed files, written by make-fixtures.py
into a folder next to the repositories. The agent works in a clone, so it cannot
change the baseline, and the check does not trust the clone's own test files:

- every baseline file named test_*.py must match byte for byte, and must still exist;
- a read-only task (explain-parse-line) must leave every baseline file unchanged
  and add no files;
- a write task must change at least one non-test file or add one;
- the task's text rule (rename-config-key) must hold;
- then `python3 -m unittest` must pass in the clone.

Exit status is 0 only when all of that holds. Run it from the clone's root.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

READ_ONLY = {"explain-parse-line"}
# File text that must be present (required) or absent (forbidden) after a write task's edit.
RULES = {
    "rename-config-key": {"README.md": (["timeout_s"], ["timeout_seconds"])},
}


def fail(message: str) -> int:
    print(f"CHECK FAILED: {message}")
    return 1


def baseline_files(base: Path) -> dict[str, bytes]:
    return {p.relative_to(base).as_posix(): p.read_bytes() for p in sorted(base.rglob("*")) if p.is_file()}


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: check-task.py <baseline-dir> <task-name>", file=sys.stderr)
        return 2
    base, name = Path(argv[1]).resolve(), argv[2]
    clone = Path.cwd()
    home = tempfile.mkdtemp(prefix="af-check-")
    try:
        return _judge(base, name, clone, _env(home))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def _judge(base: Path, name: str, clone: Path, env: dict[str, str]) -> int:
    seed = baseline_files(base)
    if name in READ_ONLY:
        protected = set(seed)
    else:
        protected = {rel for rel in seed if Path(rel).name.startswith("test_")}
    for rel in sorted(protected):
        here = clone / rel
        if not here.is_file() or here.read_bytes() != seed[rel]:
            return fail(f"{rel} must match the seed; it was changed, removed or moved")
    # Every file the agent left, committed or not; ignored files (like __pycache__) are not its work.
    listed = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=clone,
                            capture_output=True, text=True, env=env)
    added = sorted(set(line for line in listed.stdout.splitlines() if line) - set(seed))
    edited = [rel for rel in seed if rel not in protected and (clone / rel).is_file()
              and (clone / rel).read_bytes() != seed[rel]]
    if name in READ_ONLY:
        if added or edited:
            return fail(f"a read-only task must not change files: {', '.join(added + edited)}")
    elif not added and not edited:
        return fail("no file was changed")
    for rel, (required, forbidden) in RULES.get(name, {}).items():
        text = (clone / rel).read_text(encoding="utf-8", errors="replace")
        for needle in required:
            if needle not in text:
                return fail(f"{rel} must mention {needle}")
        for needle in forbidden:
            if needle in text:
                return fail(f"{rel} still mentions {needle}")
    suite = subprocess.run([sys.executable, "-m", "unittest"], cwd=clone, env=env, capture_output=True, text=True)
    if suite.returncode != 0:
        tail = "\n".join(suite.stderr.splitlines()[-20:])
        return fail(f"unittest failed\n{tail}")
    print("CHECK PASSED")
    return 0


def _env(home: str) -> dict[str, str]:
    """A clean environment: no user or system git config, no prompts, no secrets."""
    return {"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8", "HOME": home,
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0", "PYTHONDONTWRITEBYTECODE": "1"}


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
