#!/usr/bin/env python3
"""Run each unittest module in a fresh home and a minimal child environment."""
from __future__ import annotations

import concurrent.futures
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
WINDOWS_ENV = ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP",
               "APPDATA", "LOCALAPPDATA")


def modules() -> list[str]:
    return sorted(path.stem for path in TESTS.glob("test_*.py"))


def run_module(module: str) -> tuple[str, int, str]:
    with tempfile.TemporaryDirectory(prefix="agentfleet-ci-home-") as raw:
        home = Path(raw)
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(home),
            "USERPROFILE": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        if os.name == "nt":
            env.update({name: os.environ[name] for name in WINDOWS_ENV if name in os.environ})
        else:
            env["LANG"] = "C.UTF-8"
        command = [sys.executable, "-B", "-m", "unittest", f"tests.{module}"]
        result = subprocess.run(command, cwd=ROOT, env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return module, result.returncode, result.stdout


def main() -> int:
    names = modules()
    if not names:
        print("No tests/test_*.py modules found", file=sys.stderr)
        return 1
    failures: list[tuple[str, int, str]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(names))) as pool:
        for module, code, log in pool.map(run_module, names):
            if code:
                failures.append((module, code, log))
                print(f"FAIL tests/{module}.py (exit {code})")
            else:
                print(f"PASS tests/{module}.py")
    if failures:
        print("\nFailing module logs")
        for module, code, log in failures:
            print(f"\n===== tests/{module}.py (exit {code}) =====")
            print(log, end="" if log.endswith("\n") else "\n")
        print(f"\n{len(failures)} of {len(names)} modules failed", file=sys.stderr)
        return 1
    print(f"\nAll {len(names)} test modules passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
