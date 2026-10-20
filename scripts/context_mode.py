#!/usr/bin/env python3
"""Opt-in management of the upstream Context Mode Claude Code plugin.

No Context Mode code or plugin data is managed by AgentFleet. This module uses
Claude Code's plugin CLI for changes and reads Claude's user-scope settings and
installed-plugin metadata for status.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess

PLUGIN_ID = "context-mode@context-mode"
MARKETPLACE_NAME = "context-mode"
MARKETPLACE_SOURCE = "mksglu/context-mode"
CLI_TIMEOUT = 60

# A plugin marketplace command needs user configuration and PATH, not provider
# credentials or other arbitrary environment values.
_CLI_ENV_NAMES = {
    "PATH", "PATHEXT", "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA",
    "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR", "USER", "USERNAME", "LOGNAME",
    "SHELL", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
    "XDG_CACHE_HOME", "XDG_RUNTIME_DIR", "CLAUDE_CONFIG_DIR",
}


class ContextModeError(RuntimeError):
    """A safe, user-facing failure while controlling or inspecting the plugin."""


def config_dir(home: Path) -> Path:
    """Resolve the Claude settings root selected by Claude Code."""
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    if configured:
        return Path(configured).expanduser().resolve(strict=False)
    return home / ".claude"


def _cli_environment(home: Path) -> dict[str, str]:
    env = {name: value for name, value in os.environ.items() if name.upper() in _CLI_ENV_NAMES}
    try:
        real_home = Path.home().resolve(strict=False)
    except RuntimeError:
        real_home = home.resolve(strict=False)
    if home.resolve(strict=False) != real_home:
        env["HOME"] = str(home)
        if os.name == "nt":
            env["USERPROFILE"] = str(home)
    return env


def _run(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run claude with argument separation, sanitized env and bounded time."""
    env = _cli_environment(home)
    # Resolve only against PATH entries supplied to the child, excluding the
    # Windows current directory, as the other AgentFleet paths do.
    executable = shutil.which("claude", path=env.get("PATH", ""))
    if not executable or not Path(executable).is_absolute():
        raise ContextModeError("Claude Code CLI is missing or not available on PATH")
    trusted = {
        Path(entry).resolve(strict=False)
        for entry in env.get("PATH", "").split(os.pathsep)
        if entry and Path(entry).is_absolute()
    }
    if Path(executable).parent.resolve(strict=False) not in trusted:
        raise ContextModeError("Claude Code CLI is missing or not available on PATH")
    if (os.name == "nt" and Path(executable).suffix.lower() in {".cmd", ".bat"}
            and any(char in '&|<>^%!"' for arg in args for char in arg)):
        raise ContextModeError("Claude Code CLI cannot safely pass the requested plugin arguments")
    try:
        return subprocess.run(
            [str(Path(executable).resolve(strict=False)), *args],
            text=True, encoding="utf-8", errors="replace", capture_output=True,
            check=False, timeout=CLI_TIMEOUT, env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise ContextModeError("Claude Code plugin command timed out") from exc
    except OSError as exc:
        # Never include OS text, which can contain local paths or other details.
        raise ContextModeError("Claude Code plugin command could not be started") from exc


def _node_version(executable: Path, env: dict[str, str]) -> tuple[int, int, int] | None:
    try:
        result = subprocess.run(
            [str(executable), "--version"],
            text=True, encoding="utf-8", errors="replace", capture_output=True,
            check=False, timeout=5, env=env, stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:[-+][A-Za-z0-9.-]+)?\s*", result.stdout.strip())
    if result.returncode != 0 or not match:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def pinned_node(home: Path) -> Path | None:
    """The Node.js an installed Context Mode runs.

    Upstream ships a bare `node`; the installed copy replaces it with the
    absolute path of the Node that first started it, so that path, not the
    one on PATH, is what its tools and hooks need."""
    record = _user_record(home)
    install_path = record.get("installPath") if record else None
    if not isinstance(install_path, str) or not install_path:
        return None
    try:
        plugin = json.loads((Path(install_path) / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    servers = plugin.get("mcpServers") if isinstance(plugin, dict) else None
    server = servers.get("context-mode") if isinstance(servers, dict) else None
    command = server.get("command") if isinstance(server, dict) else None
    if not isinstance(command, str) or not Path(command).is_absolute():
        return None
    # Only ever run something that is plainly a Node binary.
    if Path(command).name.lower() not in {"node", "node.exe"}:
        return None
    return Path(command)


def node_problem(home: Path) -> str | None:
    """Why Context Mode's Node.js cannot run it, or None when it can (22.5 or later)."""
    env = _cli_environment(home)
    pinned = pinned_node(home)
    if pinned is not None:
        if not pinned.is_file():
            return f"Context Mode runs Node.js from {pinned}, which no longer exists, so its tools and hooks will fail."
        executable = pinned
    else:
        found = shutil.which("node", path=env.get("PATH", ""))
        trusted = {
            Path(entry).resolve(strict=False)
            for entry in env.get("PATH", "").split(os.pathsep)
            if entry and Path(entry).is_absolute()
        }
        if not found or not Path(found).is_absolute() or Path(found).parent.resolve(strict=False) not in trusted:
            return "Node.js was not found on PATH; Context Mode needs Node.js 22.5 or later."
        executable = Path(found)
    version = _node_version(executable, env)
    if version is None:
        return f"Context Mode needs Node.js 22.5 or later; {executable} did not report a version."
    if version < (22, 5, 0):
        return f"Context Mode needs Node.js 22.5 or later; {executable} is {'.'.join(map(str, version))}."
    return None


def _marketplace_list(home: Path) -> list[dict]:
    result = _run(home, "plugin", "marketplace", "list", "--json")
    if result.returncode != 0:
        raise ContextModeError("Claude Code could not list plugin marketplaces")
    try:
        value = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise ContextModeError("Claude Code returned unsupported marketplace data") from None
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ContextModeError("Claude Code returned unsupported marketplace data")
    return value


def _marketplace_is_upstream(item: dict) -> bool:
    source = item.get("source")
    if isinstance(source, dict):
        repo = source.get("repo") or source.get("repository")
        return source.get("source") == "github" and repo == MARKETPLACE_SOURCE
    # Claude CLI output/schema may represent a GitHub source as the string.
    return source == "github" and item.get("repo") == MARKETPLACE_SOURCE


def _find_upstream_marketplace(home: Path) -> bool:
    item = next((entry for entry in _marketplace_list(home) if entry.get("name") == MARKETPLACE_NAME), None)
    if item is None:
        return False
    if not _marketplace_is_upstream(item):
        raise ContextModeError("A different Context Mode marketplace is already registered; leaving it untouched")
    return True


def _user_record(home: Path) -> dict | None:
    """Claude Code's record of the user-scope install, or None when not installed."""
    path = config_dir(home) / "plugins" / "installed_plugins.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, ValueError):
        raise ContextModeError("Claude Code's installed-plugin metadata is unavailable or invalid") from None
    plugins = value.get("plugins") if isinstance(value, dict) else None
    if not isinstance(plugins, dict):
        raise ContextModeError("Claude Code's installed-plugin metadata is unsupported")
    records = plugins.get(PLUGIN_ID)
    if isinstance(records, dict):
        records = [records]
    if not isinstance(records, list):
        return None
    return next((record for record in records if isinstance(record, dict) and record.get("scope") == "user"), None)


def _installed_metadata(home: Path) -> bool:
    return _user_record(home) is not None


def _enabled_settings(home: Path) -> bool | None:
    path = config_dir(home) / "settings.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, ValueError):
        raise ContextModeError("Claude Code settings are unavailable or invalid") from None
    if not isinstance(value, dict):
        raise ContextModeError("Claude Code settings are unsupported")
    enabled = value.get("enabledPlugins")
    if enabled is None:
        return None
    if not isinstance(enabled, dict):
        raise ContextModeError("Claude Code plugin settings are unsupported")
    state = enabled.get(PLUGIN_ID)
    return state if isinstance(state, bool) else None


def status(home: Path) -> str:
    """Return installed/enabled state without invoking Claude or guessing."""
    installed = _installed_metadata(home)
    enabled = _enabled_settings(home)
    if not installed:
        return "not installed"
    if enabled is True:
        return "installed, enabled"
    if enabled is False:
        return "installed, disabled"
    return "installed, state unknown"


def _ensure_marketplace(home: Path) -> None:
    if _find_upstream_marketplace(home):
        return
    result = _run(home, "plugin", "marketplace", "add", MARKETPLACE_SOURCE)
    if result.returncode != 0:
        raise ContextModeError("Claude Code could not add the upstream Context Mode marketplace")
    # Validate the result before installing anything; do not proceed if the CLI
    # registered a name that now points at a different source.
    if not _find_upstream_marketplace(home):
        raise ContextModeError("Claude Code did not register the upstream Context Mode marketplace")


def enable(home: Path) -> bool:
    """Register upstream if needed, then enable or install the user plugin.

    Returns False when it was already installed and enabled. Claude Code's
    `plugin enable` fails on an enabled plugin, so the state is checked first,
    and again after a failure in case it changed in between."""
    if _installed_metadata(home):
        if _enabled_settings(home) is True:
            return False
        _ensure_marketplace(home)
        result = _run(home, "plugin", "enable", PLUGIN_ID, "--scope", "user")
        if result.returncode != 0 and _enabled_settings(home) is not True:
            raise ContextModeError("Claude Code could not enable Context Mode")
        return True
    _ensure_marketplace(home)
    result = _run(home, "plugin", "install", PLUGIN_ID, "--scope", "user")
    if result.returncode != 0:
        raise ContextModeError("Claude Code could not install Context Mode")
    return True


def disable(home: Path) -> bool:
    """Disable the plugin without uninstalling it or deleting user data.

    Returns False when it is not installed or already disabled."""
    if not _installed_metadata(home) or _enabled_settings(home) is False:
        return False
    result = _run(home, "plugin", "disable", PLUGIN_ID, "--scope", "user")
    if result.returncode != 0 and _enabled_settings(home) is not False:
        raise ContextModeError("Claude Code could not disable Context Mode")
    return True
