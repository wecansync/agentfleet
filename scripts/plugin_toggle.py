#!/usr/bin/env python3
"""On/off toggles for upstream Claude Code plugins AgentFleet recommends.

`context7` (documentation lookup), `security-guidance` (flags insecure code as
Claude writes it) and `playwright` (drives a browser to test web pages) come
from Anthropic's official marketplace and are
installed by default on a new AgentFleet install; existing installs are asked
once in `agentfleet configure`. `egm` (Google's Elephant-Goldfish Model) is off
by default. Changes go through Claude Code's own plugin CLI; status is read
from Claude Code's installed-plugin metadata and settings without starting it.

`security-audit` (Cloudflare's security-audit skill) is a skill, not a plugin:
`on` fetches one pinned, reviewed commit from GitHub and copies the skill into
the user's skills folder with a marker file; `off` removes only a copy that has
that marker. It is on by default for a new install.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

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
    "playwright": {
        "id": "playwright@claude-plugins-official",
        "title": "Playwright",
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
    "security-audit": {
        "kind": "skill",
        "title": "Security Audit (Cloudflare)",
        "skill": "security-audit",
        "repo": "cloudflare/security-audit-skill",
        "commit": "c1c8a8c1471069fb0e188eeaff69b8e8db6564a8",
        "path": "skills/security-audit",
        "default": True,
    },
}
SKILL_MARKER = ".agentfleet-skill"
GIT_TIMEOUT = 120
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


def _is_skill(setting: str) -> bool:
    return PLUGINS[setting].get("kind") == "skill"


def _skill_dir(home: Path, setting: str) -> Path:
    return context_mode.config_dir(home) / "skills" / PLUGINS[setting]["skill"]


def _ours(home: Path, setting: str) -> bool:
    target = _skill_dir(home, setting)
    return not target.is_symlink() and (target / SKILL_MARKER).is_file()


def installed(home: Path, setting: str) -> bool:
    """Whether Claude Code records a user-scope install of the plugin, or the skill is in place."""
    if _is_skill(setting):
        return (_skill_dir(home, setting) / "SKILL.md").is_file()
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
    if _is_skill(setting):
        return installed(home, setting)
    # Claude Code enables a plugin it installs, so a missing setting counts as on.
    return installed(home, setting) and _enabled(home, setting) is not False


def status(home: Path, setting: str) -> str:
    if not installed(home, setting):
        return "off (not installed)"
    if _is_skill(setting):
        return "on" if _ours(home, setting) else "on (installed outside AgentFleet)"
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


def _git(*args: str, cwd: Path) -> str:
    env = {"PATH": os.environ.get("PATH", os.defpath), "HOME": str(cwd), "GIT_TERMINAL_PROMPT": "0",
           "GIT_CONFIG_NOSYSTEM": "1", "LANG": "C.UTF-8"}
    try:
        result = subprocess.run(["git", *args], cwd=cwd, env=env, text=True, capture_output=True,
                                timeout=GIT_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise PluginToggleError("git is missing or did not answer") from None
    if result.returncode != 0:
        raise PluginToggleError(f"git {args[0]} failed; check the network and try again")
    return result.stdout.strip()


def _install_skill(home: Path, setting: str) -> None:
    """Fetch the pinned commit and copy the skill folder into place, with a marker."""
    meta = PLUGINS[setting]
    target = _skill_dir(home, setting)
    if target.exists() or target.is_symlink():
        raise PluginToggleError(f"{target} already exists and is not a complete skill; leaving it untouched")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".agentfleet-skill-", dir=target.parent) as tmp:
        work = Path(tmp)
        checkout = work / "repo"
        checkout.mkdir()
        _git("init", "-q", cwd=checkout)
        _git("fetch", "-q", "--depth", "1", f"https://github.com/{meta['repo']}.git", meta["commit"], cwd=checkout)
        _git("-c", "advice.detachedHead=false", "-c", "core.symlinks=false", "checkout", "-q", "FETCH_HEAD", cwd=checkout)
        if _git("rev-parse", "HEAD", cwd=checkout) != meta["commit"]:
            raise PluginToggleError(f"{meta['repo']} did not return the pinned commit")
        source = checkout / meta["path"]
        if not (source / "SKILL.md").is_file() or source.is_symlink():
            raise PluginToggleError(f"{meta['repo']} has no skill at {meta['path']}")
        for folder, dirs, files in os.walk(source):
            if any((Path(folder) / name).is_symlink() for name in (*dirs, *files)):
                raise PluginToggleError(f"{meta['repo']} contains a symbolic link; refusing to copy it")
        stage = work / "stage"
        shutil.copytree(source, stage)
        (stage / SKILL_MARKER).write_text(f"{meta['repo']}@{meta['commit']}\n", encoding="utf-8")
        os.rename(stage, target)


def enable(home: Path, setting: str) -> bool:
    """Install or re-enable the plugin for the user scope. False when it is already on."""
    if is_on(home, setting):
        return False
    if _is_skill(setting):
        _install_skill(home, setting)
        return True
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
    """Disable the plugin, keeping it installed, or remove AgentFleet's copy of the skill.
    False when it is already off."""
    if not is_on(home, setting):
        return False
    if _is_skill(setting):
        if not _ours(home, setting):
            raise PluginToggleError(f"{_skill_dir(home, setting)} was not installed by AgentFleet; remove it yourself")
        shutil.rmtree(_skill_dir(home, setting))
        return True
    meta = PLUGINS[setting]
    result = _run(home, "plugin", "disable", meta["id"], "--scope", "user")
    if result.returncode != 0 and is_on(home, setting):
        raise PluginToggleError(f"Claude Code could not turn off {meta['title']}")
    return True
