#!/usr/bin/env python3
"""Opt-in registration of upstream device-control MCP servers.

`computer-use` is Open Computer Use, which reads and operates desktop apps;
`mobile-mcp` is Mobile MCP, which drives Android and iOS emulators, simulators
and phones. AgentFleet never installs or enables either by default:
`agentfleet config <setting> on` adds the server to Claude Code's user scope
with Claude Code's own `mcp` CLI, and `off` removes that registration. Status is
read from Claude Code's user configuration, without starting Claude.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil

import context_mode

SETTINGS = {
    "computer-use": {
        "server": "open-computer-use",
        "title": "Open Computer Use",
        "package": "open-computer-use@1.1.4",
    },
    "mobile-mcp": {
        "server": "mobile-mcp",
        "title": "Mobile MCP",
        "package": "@mobilenext/mobile-mcp@1.0.16",
    },
}
# Kept for callers that name the desktop server directly.
SERVER_NAME = SETTINGS["computer-use"]["server"]
PACKAGE = SETTINGS["computer-use"]["package"]
INSTALL_HINT = f"install it first with: npm install -g {PACKAGE}"


class ComputerUseError(RuntimeError):
    """A safe, user-facing failure while controlling or inspecting a server."""


def _user_config(home: Path) -> Path:
    """Claude Code keeps user-scope MCP servers in .claude.json."""
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    if configured:
        return Path(configured).expanduser().resolve(strict=False) / ".claude.json"
    return home / ".claude.json"


def _registered(home: Path, setting: str = "computer-use") -> bool:
    try:
        value = json.loads(_user_config(home).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, UnicodeError, ValueError):
        raise ComputerUseError("Claude Code's user configuration is unavailable or invalid") from None
    servers = value.get("mcpServers") if isinstance(value, dict) else None
    return isinstance(servers, dict) and SETTINGS[setting]["server"] in servers


def find_ocu() -> str | None:
    """The absolute path of the ocu command, so Claude finds it whatever its PATH."""
    found = shutil.which("ocu") or shutil.which("open-computer-use")
    return str(Path(found).resolve(strict=False)) if found else None


def _command(setting: str) -> list[str]:
    """The server command, or an error naming what to install first."""
    if setting == "computer-use":
        ocu = find_ocu()
        if not ocu:
            raise ComputerUseError(f"Open Computer Use is not installed; {INSTALL_HINT}")
        return ["--", ocu, "mcp"]
    npx = shutil.which("npx")
    if not npx:
        raise ComputerUseError("Mobile MCP runs with npx, which was not found; install Node.js first")
    # Telemetry off; the pinned version runs through npx on first use.
    return ["-e", "MOBILEMCP_DISABLE_TELEMETRY=1", "--",
            str(Path(npx).resolve(strict=False)), "-y", SETTINGS[setting]["package"]]


def status(home: Path, setting: str = "computer-use") -> str:
    if _registered(home, setting):
        return "on"
    if setting == "computer-use" and not find_ocu():
        return f"off (not installed; {INSTALL_HINT})"
    return "off"


def _run(home: Path, *args: str):
    try:
        return context_mode._run(home, *args)
    except context_mode.ContextModeError:
        raise ComputerUseError("Claude Code CLI is missing, timed out or could not be started") from None


def enable(home: Path, setting: str = "computer-use") -> bool:
    """Register the server for the user scope. False when it is already on."""
    if _registered(home, setting):
        return False
    meta = SETTINGS[setting]
    result = _run(home, "mcp", "add", "--scope", "user", meta["server"], *_command(setting))
    if result.returncode != 0 and not _registered(home, setting):
        raise ComputerUseError(f"Claude Code could not register {meta['title']}")
    return True


def disable(home: Path, setting: str = "computer-use") -> bool:
    """Remove the user-scope registration. False when it was not registered."""
    if not _registered(home, setting):
        return False
    meta = SETTINGS[setting]
    result = _run(home, "mcp", "remove", "--scope", "user", meta["server"])
    if result.returncode != 0 and _registered(home, setting):
        raise ComputerUseError(f"Claude Code could not remove {meta['title']}")
    return True
