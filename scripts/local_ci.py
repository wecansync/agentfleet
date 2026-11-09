#!/usr/bin/env python3
"""`agentfleet ci`: run a project's tests on this machine and post the result
to GitHub as the commit status `agentfleet/tests`.

A local stand-in for hosted CI when hosted runners are unavailable or slow. The
status says the tests passed on the machine of whoever ran the command, not on a
neutral runner. It is posted only for a clean, pushed commit that is still
checked out when the tests finish, so it always describes code GitHub has.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

CONTEXT = "agentfleet/tests"
CONFIG_FILE = ".agentfleet-ci"
GIT_TIMEOUT = 60


class CIError(RuntimeError):
    """A safe, user-facing reason the run or the status could not go ahead."""


def _git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(["git", "-C", str(root), *args], text=True, capture_output=True,
                                timeout=GIT_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise CIError("git is missing or did not answer") from None
    if result.returncode != 0:
        raise CIError(f"git {args[0]} failed")
    return result.stdout.strip()


def repo_root(start: Path) -> Path:
    try:
        return Path(_git(start, "rev-parse", "--show-toplevel"))
    except CIError:
        raise CIError("not inside a git repository") from None


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _first_command(text: str) -> str | None:
    lines = [line.strip() for line in text.splitlines()]
    return next((line for line in lines if line and not line.startswith("#")), "") or None


def detect_command(root: Path, committed: bool = False) -> str | None:
    """The project's test command: .agentfleet-ci first, then common manifests.

    With committed=True (a status will be posted), .agentfleet-ci is read from HEAD,
    so an ignored or untracked copy cannot replace the committed tests."""
    configured = root / CONFIG_FILE
    if committed:
        try:
            text = _git(root, "show", f"HEAD:{CONFIG_FILE}")
        except CIError:
            if configured.exists():
                raise CIError(f"{CONFIG_FILE} is not committed: commit it, or run with --no-status") from None
        else:
            return _first_command(text)
    elif configured.is_file():
        return _first_command(configured.read_text(encoding="utf-8"))
    scripts = _read_json(root / "package.json").get("scripts")
    test = scripts.get("test") if isinstance(scripts, dict) else None
    if isinstance(test, str) and test.strip() and "no test specified" not in test:
        if (root / "pnpm-lock.yaml").is_file():
            return "pnpm test"
        if (root / "yarn.lock").is_file():
            return "yarn test"
        if (root / "bun.lockb").is_file() or (root / "bun.lock").is_file():
            return "bun run test"
        return "npm test"
    composer_scripts = _read_json(root / "composer.json").get("scripts")
    if isinstance(composer_scripts, dict) and "test" in composer_scripts:
        return "composer test"
    if (root / "vendor" / "bin" / "phpunit").is_file():
        return "vendor/bin/phpunit"
    if (root / "artisan").is_file():
        return "php artisan test"
    makefile = root / "Makefile"
    if makefile.is_file() and any(line.startswith("test:") for line in
                                  makefile.read_text(encoding="utf-8", errors="replace").splitlines()):
        return "make test"
    if (root / "go.mod").is_file():
        return "go test ./..."
    if (root / "Cargo.toml").is_file():
        return "cargo test"
    if any((root / name).is_file() for name in ("pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini")) \
            or (root / "tests").is_dir():
        return "python3 -m pytest" if shutil.which("pytest") else "python3 -m unittest discover"
    return None


def _gh(root: Path, *args: str) -> subprocess.CompletedProcess:
    if not shutil.which("gh"):
        raise CIError("the GitHub CLI (gh) is not installed; run with --no-status, or install gh and sign in")
    try:
        return subprocess.run(["gh", *args], cwd=root, text=True, capture_output=True,
                              timeout=GIT_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise CIError("the GitHub CLI did not answer") from None


def _github_repo(root: Path) -> str:
    result = _gh(root, "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner")
    name = result.stdout.strip()
    if result.returncode != 0 or name.count("/") != 1:
        raise CIError("this repository is not on GitHub, or gh is not signed in; run with --no-status")
    return name


def _clean(root: Path) -> bool:
    return _git(root, "status", "--porcelain") == ""


def _duration(seconds: float) -> str:
    minutes, secs = divmod(int(round(seconds)), 60)
    return f"{minutes}m {secs:02d}s" if minutes else f"{secs}s"


def run(start: Path, command: str | None = None, post: bool = True) -> int:
    """Run the tests; post the status when asked. Returns the exit code to use."""
    root = repo_root(start)
    command = command or detect_command(root, committed=post)
    if not command:
        raise CIError(f"no test command found; pass --run \"<command>\" or write it in {CONFIG_FILE}")
    repo = sha = None
    if post:
        if not _clean(root):
            raise CIError("uncommitted changes: commit them first, or run with --no-status")
        sha = _git(root, "rev-parse", "HEAD")
        repo = _github_repo(root)
        if _gh(root, "api", f"repos/{repo}/commits/{sha}", "--silent").returncode != 0:
            raise CIError(f"GitHub does not have commit {sha[:12]} yet: push it first, or run with --no-status")
        _post(root, repo, sha, "pending", "Running the tests locally")
    print(f"agentfleet ci: running {command}", flush=True)
    started = time.monotonic()
    try:
        code = subprocess.run(command, shell=True, cwd=root, check=False).returncode
    except KeyboardInterrupt:
        code = 130
    elapsed = _duration(time.monotonic() - started)
    passed = code == 0
    print(f"agentfleet ci: tests {'passed' if passed else f'failed (exit {code})'} in {elapsed}", flush=True)
    if post:
        # The checkout must still be the commit that was tested.
        if _git(root, "rev-parse", "HEAD") != sha or not _clean(root):
            _post(root, repo, sha, "error", "The checkout changed during the run; run agentfleet ci again")
            raise CIError("the checkout changed during the run, so no result was posted; run agentfleet ci again")
        description = f"Passed locally in {elapsed}" if passed else f"Failed locally (exit {code}) after {elapsed}"
        _post(root, repo, sha, "success" if passed else "failure", description)
        print(f"agentfleet ci: posted {CONTEXT} = {'success' if passed else 'failure'} on {sha[:12]}")
    return 0 if passed else (code if 0 < code < 256 else 1)


def _post(root: Path, repo: str, sha: str, state: str, description: str) -> None:
    result = _gh(root, "api", "--method", "POST", f"repos/{repo}/statuses/{sha}",
                 "-f", f"state={state}", "-f", f"context={CONTEXT}", "-f", f"description={description[:140]}",
                 "--silent")
    if result.returncode != 0:
        raise CIError("GitHub refused the commit status; check that gh is signed in with write access")


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="agentfleet ci")
    parser.add_argument("--run", metavar="COMMAND")
    parser.add_argument("--no-status", action="store_true")
    args = parser.parse_args(argv)
    try:
        return run(Path.cwd(), args.run, not args.no_status)
    except CIError as exc:
        print(f"agentfleet ci: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
