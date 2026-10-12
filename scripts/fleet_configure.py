#!/usr/bin/env python3
"""One registry-driven configuration wizard; no credentials in presets or output."""
import argparse
import contextlib
from dataclasses import dataclass
import getpass
import io
import json
import os
from pathlib import Path
import sys

import provider_catalog

MODS_MESSAGE = "AgentFleet mods are not registered; run the installer with --mods to use this"
PLUGIN_IDS = {"jev": "fast-jev-compaction@agentfleet", "statusBand": "fleet-status@agentfleet", "laneGuard": "lane-guard@agentfleet"}


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def mods_registered(paths):
    meta = read_json(paths.claude / ".claude-agents-config-install.json")
    return meta.get("modsMarketplace") is True or meta.get("modsState") == "registered"


# One `claude plugin list` per wizard step instead of one per read: each call
# starts the Claude CLI, and a run reads every plugin feature several times.
_plugin_cache: list | None = None


def plugins(paths):
    global _plugin_cache
    if _plugin_cache is not None:
        return _plugin_cache
    result = provider_catalog.claude_cli(paths.home, "plugin", "list", "--json")
    if result is None or result.returncode:
        raise RuntimeError("could not read Claude plugins")
    data = json.loads(result.stdout)
    if isinstance(data, dict):
        data = data.get("plugins")
    if not isinstance(data, list):
        raise ValueError("unsupported Claude plugin list")
    _plugin_cache = data
    return data


def current(api, paths, key):
    pref = {"compact": "compactPercent", "reviewerAccess": "reviewerAccess", "learn": "learn"}
    if key in pref:
        return provider_catalog.load_preferences(paths.home)[pref[key]]
    if key == "autoUpdate":
        return api._read_auto_update_state(paths).get("enabled", True)
    if not mods_registered(paths):
        return False
    return any(isinstance(p, dict) and p.get("id") == PLUGIN_IDS[key] and p.get("enabled") is True for p in plugins(paths))


def apply(api, paths, key, value):
    if key in ("compact", "reviewerAccess", "learn"):
        setting = {"compact": "compact", "reviewerAccess": "reviewer-access", "learn": "learn"}[key]
        text = ("on" if value else "off") if key == "learn" else str(value)
        return api.cmd_config(paths, argparse.Namespace(setting=setting, value=text))
    if key == "autoUpdate":
        return api.cmd_auto_update(paths, argparse.Namespace(action="on" if value else "off", hook=False, run=None))
    if key == "jev":
        return api.cmd_jev(paths, argparse.Namespace(action="on" if value else "off"))
    installed = any(isinstance(p, dict) and p.get("id") == PLUGIN_IDS[key] for p in plugins(paths))
    result = provider_catalog.claude_cli(paths.home, "plugin", ("enable" if installed else "install") if value else "disable", PLUGIN_IDS[key])
    return 0 if result is not None and result.returncode == 0 else 1


@dataclass(frozen=True)
class Feature:
    key: str
    introduced: str
    question: str
    choices: str

    def valid(self, value):
        if self.key == "compact":
            return provider_catalog.valid_compact_percent(value)
        if self.key == "reviewerAccess":
            return isinstance(value, str) and value in provider_catalog.REVIEWER_ACCESS
        return isinstance(value, bool)

    def read(self, api, paths):
        return current(api, paths, self.key)

    def apply(self, api, paths, value):
        global _plugin_cache
        try:
            return apply(api, paths, self.key, value)
        finally:
            # The step may have enabled or disabled a plugin.
            _plugin_cache = None


FEATURES = (
    Feature("compact", "2.3.0", "Auto-compact at what percent of each model's context? (20-95, or off)", "20-95 or off"),
    Feature("jev", "2.3.0", "Use JEV compaction? It keeps messages word-for-word instead of a summary and sends conversation text to api.typesafe.ai. (y/N)", "yes/no"),
    Feature("reviewerAccess", "2.3.0", "Reviewer lanes: full (run commands, fix in FIX MODE), run (commands only), or read-only?", "full, run, read-only"),
    Feature("learn", "2.3.0", "Learn from your usage to suggest skills, plugins and hooks? It counts command and tool names only, never prompt text. (Y/n)", "yes/no"),
    Feature("statusBand", "2.3.0", "Show the AgentFleet status band (updates, outages, suggestions, JEV) above the prompt? (Y/n)", "yes/no"),
    Feature("laneGuard", "2.3.0", "Block fleet lanes from commits, pushes, deploys and destructive commands (lane-guard)? (Y/n)", "yes/no"),
    Feature("autoUpdate", "2.3.0", "Install AgentFleet updates automatically in the background? (Y/n)", "yes/no"),
)


def validate_preset(data):
    if not isinstance(data, dict):
        raise ValueError("preset must be an object")
    for key in data:
        if any(word in key.lower() for word in ("key", "token", "secret")):
            raise ValueError("presets must not contain secrets")
    if data.get("format") != "agentfleet.preset.v1":
        raise ValueError("unsupported preset format")
    known = {f.key: f for f in FEATURES}
    for key, value in data.items():
        if key == "format":
            continue
        if key not in known:
            raise ValueError("unknown preset field: " + key)
        if not known[key].valid(value):
            raise ValueError("invalid value for " + key)
    return {k: v for k, v in data.items() if k != "format"}


def has_key(paths):
    env = read_json(paths.settings).get("env", {})
    return bool(os.environ.get("TYPESAFE_API_KEY") or (isinstance(env, dict) and env.get("TYPESAFE_API_KEY")))


def ask_key(api, paths):
    if has_key(paths):
        return
    key = getpass.getpass("TypeSafe API key (input hidden, Enter to skip):")
    if not key:
        print("Set the key later with /plugin (fast-jev-compaction → Configure) or TYPESAFE_API_KEY.")
        return
    fd, lock = provider_catalog.acquire_lock(paths.home)
    if fd is None:
        raise RuntimeError("configuration lock unavailable")
    try:
        # Unlike preference reads, never replace malformed settings with an empty object.
        settings = json.loads(paths.settings.read_text(encoding="utf-8")) if paths.settings.exists() else {}
        if not isinstance(settings, dict) or not isinstance(settings.get("env", {}), dict):
            raise ValueError("invalid settings environment")
        settings.setdefault("env", {})["TYPESAFE_API_KEY"] = key
        api.atomic_write(paths.settings, api.json_bytes(settings), 0o600)
    finally:
        provider_catalog.release_lock(fd, lock)


def display(value):
    return ("on" if value else "off") if isinstance(value, bool) else str(value)


def prompt(feature, value):
    while True:
        answer = input(f"{feature.question} [{display(value)}] ").strip().lower()
        if not answer:
            return value
        if feature.key == "compact":
            try:
                answer = int(answer)
            except ValueError:
                pass
        elif feature.key != "reviewerAccess":
            answer = {"y": True, "yes": True, "on": True, "n": False, "no": False, "off": False}.get(answer, answer)
        if feature.valid(answer):
            return answer
        print("Choose " + feature.choices + ".")


def save_state(api, paths, state_path, asked):
    fd, lock = provider_catalog.acquire_lock(paths.home)
    if fd is None:
        raise RuntimeError("configuration lock unavailable")
    try:
        latest = read_json(state_path).get("asked", [])
        if isinstance(latest, list):
            asked = list(dict.fromkeys([k for k in latest if isinstance(k, str)] + asked))
        api.atomic_write(state_path, api.json_bytes({"format": "agentfleet.wizard.v1", "asked": asked, "version": api.installed_version(paths)}), 0o600)
    finally:
        provider_catalog.release_lock(fd, lock)
    return asked


def run(api, paths, args):
    global _plugin_cache
    _plugin_cache = None
    state_path = paths.store / "wizard.json"
    state = read_json(state_path)
    asked = state.get("asked", []) if state.get("format") == "agentfleet.wizard.v1" else []
    asked = [k for k in asked if isinstance(k, str)] if isinstance(asked, list) else []
    unasked = [f for f in FEATURES if f.key not in asked]
    tty = sys.stdin.isatty() and sys.stdout.isatty()
    if args.after_install and not tty:
        if unasked:
            print("New AgentFleet options are available: run agentfleet configure")
        return 0
    interactive = not (args.yes or args.preset or args.export)
    if interactive and not tty:
        print("agentfleet configure needs a terminal; use --preset FILE or --yes", file=sys.stderr)
        return 2
    values = {}
    try:
        if args.preset:
            try:
                values = validate_preset(json.loads(Path(args.preset).expanduser().read_text(encoding="utf-8")))
            except (OSError, ValueError, UnicodeError) as exc:
                print(f"agentfleet configure: {exc}", file=sys.stderr)
                return 2
        if args.export:
            values = {f.key: f.read(api, paths) for f in FEATURES}
            api.atomic_write(Path(args.export).expanduser(), api.json_bytes({"format": "agentfleet.preset.v1", **values}), 0o644)
            return 0
        selected = [f for f in FEATURES if f.key in values] if args.preset else (unasked if args.yes or args.after_install else FEATURES)
        if args.after_install and not selected:
            return 0
        skipped = False
        for feature in selected:
            key = feature.key
            if key in PLUGIN_IDS and not mods_registered(paths):
                if not skipped:
                    print(MODS_MESSAGE)
                    skipped = True
                values[key] = False
                if key not in asked:
                    asked.append(key)
                asked = save_state(api, paths, state_path, asked)
                continue
            value = values[key] if key in values else feature.read(api, paths)
            if interactive:
                value = prompt(feature, value)
                if key == "jev" and value:
                    ask_key(api, paths)
            # Keeping a value need not regenerate agents or install disabled plugins.
            if value != feature.read(api, paths):
                with contextlib.redirect_stdout(io.StringIO()):
                    code = feature.apply(api, paths, value)
                if code:
                    print("agentfleet configure: apply failed for " + key, file=sys.stderr)
                    return 1
            values[key] = value
            if key not in asked:
                asked.append(key)
            asked = save_state(api, paths, state_path, asked)
        for feature in FEATURES:
            value = values[feature.key] if feature.key in values else feature.read(api, paths)
            print(f"{feature.key}: {display(value)}")
        print("Restart Claude Code to apply.")
        return 0
    except (OSError, ValueError, RuntimeError, EOFError, KeyboardInterrupt):
        # CLI/settings failures can contain sensitive data; report only the step.
        print("agentfleet configure: failed at " + locals().get("key", "reading configuration"), file=sys.stderr)
        return 1
