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
import shutil
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


def agent_brain_available():
    return shutil.which("agent-brain") is not None


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
    pref = {"compact": "compactPercent", "reviewerAccess": "reviewerAccess", "learn": "learn", "workStyle": "workStyle"}
    if key in pref:
        return provider_catalog.load_preferences(paths.home)[pref[key]]
    if key == "agentBrain":
        value = provider_catalog.load_preferences(paths.home)["agentBrain"]
        return agent_brain_available() if value is None else value
    if key == "instructions":
        return False
    if key == "autoUpdate":
        return api._read_auto_update_state(paths).get("enabled", True)
    if not mods_registered(paths):
        return False
    return any(isinstance(p, dict) and p.get("id") == PLUGIN_IDS[key] and p.get("enabled") is True for p in plugins(paths))


def apply(api, paths, key, value):
    if key in ("compact", "reviewerAccess", "learn", "workStyle", "agentBrain"):
        setting = {
            "compact": "compact", "reviewerAccess": "reviewer-access", "learn": "learn",
            "workStyle": "work-style", "agentBrain": "agent-brain",
        }[key]
        text = ("on" if value else "off") if key in ("learn", "agentBrain") else str(value)
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
        if self.key == "workStyle":
            return isinstance(value, str) and value in provider_catalog.WORK_STYLES
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
    Feature("learn", "2.3.0", "Learn from your usage to suggest skills, plugins and hooks? It counts command names, tool names, documentation sites and slash commands only, never prompt text. (Y/n)", "yes/no"),
    Feature("statusBand", "2.3.0", "Show the AgentFleet status band (updates, outages, suggestions, JEV) above the prompt? (Y/n)", "yes/no"),
    Feature("laneGuard", "2.3.0", "Block fleet lanes from commits, pushes, deploys and destructive commands (lane-guard)? (Y/n)", "yes/no"),
    Feature("autoUpdate", "2.3.0", "Install AgentFleet updates automatically in the background? (Y/n)", "yes/no"),
    Feature("workStyle", "2.4.0", "How should Claude use fleet lanes? balanced (work directly; lanes for large, parallel or risky work), direct (lanes only when you ask), or fleet (delegate by default)", "balanced, direct, fleet"),
    Feature("agentBrain", "2.4.0", "Connect agent-brain memory hooks? They add a memory-saving step to every reply and lane run. (Y/n)", "yes/no"),
    Feature("instructions", "2.4.0", "Check your instruction files (CLAUDE.md, rules) for conflicts with AgentFleet now? Uses Claude, about 10–30 seconds. (Y/n)", "yes/no"),
)


def validate_preset(data):
    if not isinstance(data, dict):
        raise ValueError("preset must be an object")
    for key in data:
        if any(word in key.lower() for word in ("key", "token", "secret")):
            raise ValueError("presets must not contain secrets")
    if data.get("format") != "agentfleet.preset.v1":
        raise ValueError("unsupported preset format")
    # The instruction check is an action, so presets cannot carry it.
    known = {f.key: f for f in FEATURES if f.key != "instructions"}
    for key, value in data.items():
        if key == "format":
            continue
        if key not in known:
            raise ValueError("unknown preset field: " + ascii(key))
        if not known[key].valid(value):
            raise ValueError("invalid value for " + key)
    return {k: v for k, v in data.items() if k != "format"}


def has_key(paths):
    env = read_json(paths.settings).get("env", {})
    return bool(os.environ.get("TYPESAFE_API_KEY") or (isinstance(env, dict) and env.get("TYPESAFE_API_KEY")))


def ask_key(api, paths):
    if has_key(paths):
        return
    print("The key is saved in ~/.claude/settings.json (env TYPESAFE_API_KEY), where commands Claude runs can read it.")
    print("For private storage, leave it empty and set it in Claude Code with /plugin → fast-jev-compaction → Configure.")
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
    registered = mods_registered(paths)
    # Without the agent-brain CLI there is nothing to ask; it is recorded as
    # asked on the first successful run so the status band does not count it.
    brain = agent_brain_available()
    unasked = [f for f in FEATURES if f.key not in asked]
    applicable = [f for f in unasked if (registered or f.key not in PLUGIN_IDS) and (brain or f.key != "agentBrain")]
    tty = sys.stdin.isatty() and sys.stdout.isatty()
    if args.after_install and not tty:
        if not brain and "agentBrain" not in asked:
            asked = save_state(api, paths, state_path, [*asked, "agentBrain"])
        # The instruction check is offered interactively but is not a new option.
        if any(f.key != "instructions" for f in applicable):
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
            values = {f.key: f.read(api, paths) for f in FEATURES if f.key != "instructions"}
            api.atomic_write(Path(args.export).expanduser(), api.json_bytes({"format": "agentfleet.preset.v1", **values}), 0o644)
            return 0
        if args.preset:
            selected = [f for f in FEATURES if f.key in values]
        elif args.after_install:
            selected = applicable
        elif args.yes:
            # --yes keeps values and never runs the instruction check.
            selected = [f for f in unasked if f.key != "instructions"]
        else:
            selected = list(FEATURES)
        # Recorded only once the run succeeds, so a failed apply writes nothing.
        pending_brain = not brain and "agentBrain" not in asked
        if not brain:
            selected = [f for f in selected if f.key != "agentBrain"]
        if args.after_install and not selected:
            if pending_brain:
                save_state(api, paths, state_path, [*asked, "agentBrain"])
            return 0
        if not registered and any(f.key in PLUGIN_IDS for f in selected):
            print(MODS_MESSAGE)
        for feature in selected:
            key = feature.key
            if key in PLUGIN_IDS and not registered:
                continue
            if key == "instructions":
                # An action, not a setting: its output must stay visible and a
                # failed check never fails the wizard.
                if interactive and prompt(feature, True):
                    try:
                        module = api.instructions_module()
                    except ImportError:
                        print("instruction check is not installed; run agentfleet update")
                    else:
                        if module.run(paths.home, project=None, interactive=True):
                            print("The instruction check did not finish; run agentfleet instructions later.")
                asked = save_state(api, paths, state_path, [*asked, key])
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
        if pending_brain:
            asked = save_state(api, paths, state_path, [*asked, "agentBrain"])
        for feature in FEATURES:
            if feature.key == "instructions":
                continue
            value = values[feature.key] if feature.key in values else feature.read(api, paths)
            print(f"{feature.key}: {display(value)}")
        print("Restart Claude Code to apply.")
        return 0
    except (OSError, ValueError, RuntimeError, EOFError, KeyboardInterrupt):
        # CLI/settings failures can contain sensitive data; report only the step.
        print("agentfleet configure: failed at " + locals().get("key", "reading configuration"), file=sys.stderr)
        return 1
