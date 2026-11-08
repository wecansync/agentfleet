#!/usr/bin/env python3
"""On/off toggles for upstream Claude Code plugins AgentFleet recommends.

`context7` (documentation lookup) and `security-guidance` (flags insecure code
as Claude writes it) come from Anthropic's official marketplace and are
installed by default on a new AgentFleet install; existing installs are asked
once in `agentfleet configure`. `egm` (Google's Elephant-Goldfish Model) is off
by default. Changes go through Claude Code's own plugin CLI; status is read
from Claude Code's installed-plugin metadata and settings without starting it.
"""
from __future__ import annotations

import json
from pathlib import Path

import context_mode

PLUGINS = {
    "context7": {
        "id": "context7@claude-plugins-official",
        "title": "Context7",
        "marketplace": "claude-plugins-official",
        "repo": "anthropics/claude-plugins-official",
        "default": True,
    },
    "security-guidance": {
        "id": "security-guidance@claude-plugins-official",
        "title": "Security Guidance",
        "marketplace": "claude-plugins-official",
        "repo": "anthropics/claude-plugins-official",
        "default": True,
    },
    "egm": {
        "id": "egm@egm",
        "title": "Elephant-Goldfish Model",
        "marketplace": "egm",
        "repo": "google/elephant-goldfish-model",
        "default": False,
    },
}
DEFAULT_ON = tuple(name for name, meta in PLUGINS.items() if meta["default"])


class PluginToggleError(RuntimeError):
    """A safe, user-facing failure while controlling or inspecting a plugin."""


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, ValueError):
        raise PluginToggleError("Claude Code's plugin metadata or settings are unavailable or invalid") from None


def installed(home: Path, setting: str) -> bool:
    """Whether Claude Code records a user-scope install of the plugin."""
    value = _read_json(context_mode.config_dir(home) / "plugins" / "installed_plugins.json")
    plugins = value.get("plugins") if isinstance(value, dict) else None
    records = plugins.get(PLUGINS[setting]["id"]) if isinstance(plugins, dict) else None
    if isinstance(records, dict):
        records = [records]
    return isinstance(records, list) and any(isinstance(r, dict) and r.get("scope") == "user" for r in records)


def _enabled(home: Path, setting: str) -> bool | None:
    value = _read_json(context_mode.config_dir(home) / "settings.json")
    enabled = value.get("enabledPlugins") if isinstance(value, dict) else None
    state = enabled.get(PLUGINS[setting]["id"]) if isinstance(enabled, dict) else None
    return state if isinstance(state, bool) else None


def is_on(home: Path, setting: str) -> bool:
    # Claude Code enables a plugin it installs, so a missing setting counts as on.
    return installed(home, setting) and _enabled(home, setting) is not False


def status(home: Path, setting: str) -> str:
    if not installed(home, setting):
        return "off (not installed)"
    return "on" if is_on(home, setting) else "off (installed, disabled)"


def _run(home: Path, *args: str):
    try:
        return context_mode._run(home, *args)
    except context_mode.ContextModeError:
        raise PluginToggleError("Claude Code CLI is missing, timed out or could not be started") from None


def _is_repo(entry: dict, repo: str) -> bool:
    """True only for the exact GitHub repository, as a github source or its https URL."""
    wanted = repo.lower()
    if entry.get("source") == "github" and str(entry.get("repo", "")).lower() == wanted:
        return True
    url = str(entry.get("url", "")).lower()
    return url in {f"https://github.com/{wanted}", f"https://github.com/{wanted}.git"}


def _ensure_marketplace(home: Path, setting: str) -> None:
    meta = PLUGINS[setting]
    result = _run(home, "plugin", "marketplace", "list", "--json")
    try:
        markets = json.loads(result.stdout) if result.returncode == 0 else None
    except ValueError:
        markets = None
    if not isinstance(markets, list):
        raise PluginToggleError("Claude Code did not list its plugin marketplaces")
    entry = next((m for m in markets if isinstance(m, dict) and m.get("name") == meta["marketplace"]), None)
    if entry is not None:
        if not _is_repo(entry, meta["repo"]):
            raise PluginToggleError(f'A different "{meta["marketplace"]}" marketplace is registered; leaving it untouched')
        return
    added = _run(home, "plugin", "marketplace", "add", meta["repo"])
    if added.returncode != 0:
        raise PluginToggleError(f"Claude Code could not add the {meta['marketplace']} marketplace")


def enable(home: Path, setting: str) -> bool:
    """Install or re-enable the plugin for the user scope. False when it is already on."""
    if is_on(home, setting):
        return False
    meta = PLUGINS[setting]
    if installed(home, setting):
        result = _run(home, "plugin", "enable", meta["id"], "--scope", "user")
    else:
        _ensure_marketplace(home, setting)
        result = _run(home, "plugin", "install", meta["id"], "--scope", "user")
    if result.returncode != 0 and not is_on(home, setting):
        raise PluginToggleError(f"Claude Code could not turn on {meta['title']}")
    return True


def disable(home: Path, setting: str) -> bool:
    """Disable the plugin, keeping it installed. False when it is already off."""
    if not is_on(home, setting):
        return False
    meta = PLUGINS[setting]
    result = _run(home, "plugin", "disable", meta["id"], "--scope", "user")
    if result.returncode != 0 and is_on(home, setting):
        raise PluginToggleError(f"Claude Code could not turn off {meta['title']}")
    return True
