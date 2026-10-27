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
import re
import sys

import agent_brain
import context_mode
import provider_catalog

MODS_MESSAGE = "AgentFleet mods are not registered; run the installer with --mods to use this"
PLUGIN_IDS = {"jev": "fast-jev-compaction@agentfleet", "statusBand": "fleet-status@agentfleet", "laneGuard": "lane-guard@agentfleet",
              "tokenWeather": "token-weather@agentfleet", "brainMod": "brain@agentfleet",
              "sessionInfo": "session-info@agentfleet"}


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


def claude_cli_available():
    return provider_catalog.claude_cli_executable() is not None


def agent_brain_available(paths):
    return agent_brain.find_binary(paths.home) is not None


def agent_brain_applies(paths, key):
    """Whether an agent-brain question means anything on this machine now."""
    installed = agent_brain_available(paths)
    if key == "agentBrainInstall":
        return not installed
    return installed


def local_memory_state(paths, asked):
    """Where the memory question stands: "ask"; "active" when memory is
    already on; "skip" when agent-brain was declined or is off, so there is
    nothing to turn on; "later" while agent-brain's install question is still
    open or its install failed."""
    if not agent_brain_available(paths):
        return "skip" if "agentBrainInstall" in asked else "later"
    if agent_brain.memory_active(paths.home):
        # Even with agent-brain disconnected, so a preset can turn it off.
        return "active"
    return "skip" if provider_catalog.load_preferences(paths.home)["agentBrain"] is False else "ask"


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
    pref = {"compact": "compactPercent", "reviewerAccess": "reviewerAccess", "learn": "learn", "workStyle": "workStyle",
            "askFirst": "askFirst", "branchCheck": "branchCheck", "providerUsage": "providerUsage",
            "mainSessionGuard": "mainSessionGuard"}
    if key in pref:
        return provider_catalog.load_preferences(paths.home)[pref[key]]
    if key == "contextMode":
        return context_mode.status(paths.home) in {"installed, enabled", "installed, state unknown"}
    if key == "agentBrain":
        value = provider_catalog.load_preferences(paths.home)["agentBrain"]
        return agent_brain_available(paths) if value is None else value
    if key == "agentBrainInstall":
        return agent_brain_available(paths)
    if key == "agentBrainLocalMemory":
        # What agent-brain reports, not the saved choice: memory turned off
        # with agent-brain itself leaves that choice behind.
        return agent_brain.memory_active(paths.home)
    if key in ACTIONS:
        return False
    if key == "autoUpdate":
        return api._read_auto_update_state(paths).get("enabled", True)
    if not mods_registered(paths):
        return False
    return any(isinstance(p, dict) and p.get("id") == PLUGIN_IDS[key] and p.get("enabled") is True for p in plugins(paths))


def apply(api, paths, key, value):
    if key in ("compact", "reviewerAccess", "learn", "workStyle", "askFirst", "branchCheck", "providerUsage", "contextMode",
               "agentBrain", "agentBrainInstall", "agentBrainLocalMemory", "mainSessionGuard"):
        setting = {
            "compact": "compact", "reviewerAccess": "reviewer-access", "learn": "learn",
            "workStyle": "work-style", "askFirst": "ask-first", "branchCheck": "branch-check",
            "providerUsage": "provider-usage", "contextMode": "context-mode", "agentBrain": "agent-brain",
            "agentBrainInstall": "agent-brain", "agentBrainLocalMemory": "agent-brain-memory",
            "mainSessionGuard": "main-session-guard",
        }[key]
        text = ("on" if value else "off") if key in ("learn", "askFirst", "branchCheck", "providerUsage", "contextMode", "agentBrain", "agentBrainInstall", "agentBrainLocalMemory", "mainSessionGuard") else str(value)
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


# Actions the wizard offers, not settings: presets cannot carry them and the
# summary does not list them.
ACTIONS = ("instructions",)
AGENT_BRAIN_KEYS = ("agentBrainInstall", "agentBrain")
LOCAL_MEMORY_KEY = "agentBrainLocalMemory"

FEATURES = (
    # Context
    Feature("compact", "2.3.0", "Auto-compact at what percent of each model's context? (20-95, or off)", "20-95 or off"),
    Feature("jev", "2.3.0", "Use JEV compaction? It keeps messages word-for-word instead of a summary and sends conversation text to api.typesafe.ai. (y/N)", "yes/no"),
    Feature("contextMode", "2.12.0", "Install Context Mode, a third-party plugin (ELv2 license) that indexes large tool output locally and adds its own hooks? It promises no measured token savings. (y/N)", "yes/no"),
    Feature("tokenWeather", "2.5.0", "Show token-weather, a forecast of how full the context window is, above the prompt? (Y/n)", "yes/no"),
    # Lanes
    Feature("workStyle", "2.4.0", "How should Claude use fleet lanes? balanced (work directly; lanes for large, parallel or risky work), direct (lanes only when you ask), or fleet (delegate by default)", "balanced, direct, fleet"),
    Feature("askFirst", "2.5.0", "Before large, unclear or risky tasks, should Claude check the docs, investigate, and agree on the approach with you (workflow, worktrees, new-project setup)? Small, clear tasks are done straight away. (Y/n)", "yes/no"),
    Feature("branchCheck", "2.12.0", "Check the git branch before Claude's first file change in a task? (Y/n)", "yes/no"),
    Feature("reviewerAccess", "2.3.0", "Reviewer lanes: full (run commands, fix in FIX MODE), run (commands only), or read-only?", "full, run, read-only"),
    Feature("laneGuard", "2.3.0", "Block fleet lanes from commits, pushes, deploys and destructive commands (lane-guard)? (Y/n)", "yes/no"),
    Feature("mainSessionGuard", "2.15.0", "Ask you before the main session runs a command that discards work or data, or publishes: a force push, git reset --hard, a deploy, a migration, destructive SQL or a dangerous rm? Useful when you skip permission prompts; needs lane-guard. (y/N)", "yes/no"),
    # Status and reports
    Feature("statusBand", "2.3.0", "Show the AgentFleet status band (updates, outages, suggestions, JEV) above the prompt? (Y/n)", "yes/no"),
    Feature("sessionInfo", "2.13.0", "Show session info above the prompt: the current model and effort, uptime, git branch, running subagents, prompts sent and cost? (Y/n)", "yes/no"),
    Feature("brainMod", "2.12.0", "Add the /brain command? It shows the tokens and cost of this session and week, your latest project memories, and what your gateway reports. (Y/n)", "yes/no"),
    Feature("providerUsage", "2.12.0", "Show the spend and limits your gateway reports in agentfleet report, /brain and the status band? Your gateway key is sent only to the gateway's own URL. (Y/n)", "yes/no"),
    Feature("learn", "2.3.0", "Learn from your usage to suggest skills, plugins and hooks? It counts command names, tool names, documentation sites and slash commands only, never prompt text. (Y/n)", "yes/no"),
    # agent-brain, in dependency order
    Feature("agentBrainInstall", "2.6.0", "Install agent-brain, the free tool that records the tokens and cost of each session? It comes from agentbrain.wecansync.com, and AgentFleet's auto-update keeps it current. (Y/n)", "yes/no"),
    Feature("agentBrain", "2.4.0", "Connect agent-brain to Claude Code, so it records the tokens and cost of each session? (Y/n)", "yes/no"),
    Feature("agentBrainLocalMemory", "2.10.1", "Turn on agent-brain memory? Claude saves decisions and project context on this machine and reads them back in later sessions. It's free, nothing leaves this machine, and Claude takes a short save step now and then, less often when there is nothing to save. (y/N)", "yes/no"),
    # Updates and checks
    Feature("autoUpdate", "2.3.0", "Install AgentFleet updates automatically in the background? (Y/n)", "yes/no"),
    Feature("instructions", "2.4.0", "Check your instruction files (CLAUDE.md, rules) for conflicts with AgentFleet now? Uses Claude, about 10–30 seconds. (Y/n)", "yes/no"),
)


# Preset keys that are not questions: they are applied through claude-fleet-setup
# and never asked, so they are never recorded in wizard.json.
FLEET_KEYS = ("reservedModels", "lanes", "prices")
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
LANE_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")


def valid_model_list(value, limit=None):
    # A comma would split one id in two on the claude-fleet-setup command line.
    return (isinstance(value, list) and (limit is None or 0 < len(value) <= limit)
            and all(isinstance(m, str) and m.strip() and "," not in m and not m.strip().startswith("-") for m in value))


def validate_lanes(lanes):
    if not isinstance(lanes, dict):
        raise ValueError("invalid value for lanes")
    for name, lane in lanes.items():
        if not isinstance(name, str) or not LANE_NAME.fullmatch(name):
            raise ValueError("invalid lane name: " + ascii(name))
        if not isinstance(lane, dict):
            raise ValueError("invalid value for lane " + name)
        for field in lane:
            if field not in ("models", "effort"):
                raise ValueError(f"unknown field for lane {name}: " + ascii(field))
        if "models" not in lane and "effort" not in lane:
            raise ValueError(f"lane {name} needs models or effort")
        if "models" in lane and not valid_model_list(lane["models"], 4):
            raise ValueError(f"invalid models for lane {name}")
        if "effort" in lane and lane["effort"] not in EFFORT_LEVELS:
            raise ValueError(f"invalid effort for lane {name} (use {', '.join(EFFORT_LEVELS)})")


def validate_preset(data):
    if not isinstance(data, dict):
        raise ValueError("preset must be an object")
    known = {f.key: f for f in FEATURES if f.key not in ACTIONS}
    for key in data:
        if key not in known and any(word in key.lower() for word in ("key", "token", "secret")):
            raise ValueError("presets must not contain secrets")
    if data.get("format") != "agentfleet.preset.v1":
        raise ValueError("unsupported preset format")
    for key, value in data.items():
        if key == "format":
            continue
        if key == "prices":
            if not provider_catalog.valid_prices(value):
                raise ValueError("invalid value for prices")
            continue
        if key == "reservedModels":
            if not valid_model_list(value):
                raise ValueError("invalid value for reservedModels")
            continue
        if key == "lanes":
            validate_lanes(value)
            continue
        if key not in known:
            raise ValueError("unknown preset field: " + ascii(key))
        if not known[key].valid(value):
            raise ValueError("invalid value for " + key)
    return {k: (provider_catalog.normalise_prices(v) if k == "prices" else v)
            for k, v in data.items() if k != "format"}


def fleet_map(paths):
    """The newer of the two fleet mirrors, as the setup tool reads it."""
    found = [p for p in (paths.fleet, paths.legacy_fleet) if p.is_file()]
    found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return read_json(found[0]) if found else {}


def export_fleet_keys(paths):
    """reservedModels, per-lane choices, and configured prices."""
    fleet = fleet_map(paths)
    out = {}
    reserved = [m for m in fleet.get("reservedModels") or [] if isinstance(m, str) and m.strip()]
    if reserved:
        out["reservedModels"] = reserved
    lanes = {}
    for name, config in (fleet.get("lanes") or {}).items():
        if not isinstance(config, dict):
            continue
        entry = {}
        preferred = config.get("preferred")
        if config.get("preferredOnly") is True and isinstance(preferred, list) and preferred:
            entry["models"] = preferred
        if config.get("preferredEffort") in EFFORT_LEVELS:
            entry["effort"] = config["preferredEffort"]
        if entry:
            lanes[name] = entry
    if lanes:
        out["lanes"] = lanes
    prices = provider_catalog.load_preferences(paths.home).get("prices", {})
    if prices:
        out["prices"] = prices
    return out


def apply_fleet_keys(api, paths, values):
    """One claude-fleet-setup run for the preset's reserved models and lanes."""
    args = []
    if values.get("reservedModels"):
        args += ["--reserve", ",".join(values["reservedModels"])]
    for name, lane in (values.get("lanes") or {}).items():
        if "models" in lane:
            args += ["--prefer-only", f"{name}={','.join(lane['models'])}"]
        if "effort" in lane:
            args += ["--effort", f"{name}={lane['effort']}"]
    if not args:
        return 0
    try:
        return api.run_tool(paths, "claude-fleet-setup.py", [*args, "--reconcile"])
    except SystemExit as exc:
        # run_tool has already printed why when it stops on a missing tool.
        return exc.code if isinstance(exc.code, int) and exc.code else 1


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
    # Nothing answered yet: the first-run wizard, which asks every question.
    first_run = not asked
    registered = mods_registered(paths)
    unasked = [f for f in FEATURES if f.key not in asked]

    def install_pending():
        # Connecting agent-brain waits for the install question: a failed
        # install leaves both open for the next run.
        return "agentBrainInstall" not in asked and agent_brain_applies(paths, "agentBrainInstall")

    # An agent-brain question that means nothing here (installing it when it
    # is installed, connecting it when it is not) is recorded as asked once a
    # run succeeds, so the status band does not count it.
    skipped_brain = [] if install_pending() else [
        f.key for f in unasked if f.key in AGENT_BRAIN_KEYS and not agent_brain_applies(paths, f.key)]
    # The memory question likewise, and memory already on counts as a yes.
    skipped_local_memory = []
    if LOCAL_MEMORY_KEY not in asked:
        memory_state = local_memory_state(paths, asked)
        if memory_state in ("skip", "active"):
            skipped_local_memory.append(LOCAL_MEMORY_KEY)
        if memory_state == "active":
            provider_catalog.write_preferences(paths.home, {LOCAL_MEMORY_KEY: True})
    skipped_context_mode = [f.key for f in unasked if f.key == "contextMode" and not claude_cli_available()]
    applicable = [f for f in unasked if (registered or f.key not in PLUGIN_IDS)
                  and f.key not in skipped_brain and f.key not in skipped_local_memory and f.key not in skipped_context_mode]
    tty = sys.stdin.isatty() and sys.stdout.isatty()
    if args.after_install and not tty:
        if skipped_brain or skipped_local_memory:
            asked = save_state(api, paths, state_path, [*asked, *skipped_brain, *skipped_local_memory])
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
            values = {f.key: f.read(api, paths) for f in FEATURES if f.key not in ACTIONS}
            values.update({key: value for key, value in export_fleet_keys(paths).items() if key != "prices"})
            prices = provider_catalog.load_preferences(paths.home).get("prices", {})
            if prices:
                values["prices"] = prices
            else:
                values.pop("prices", None)
            api.atomic_write(Path(args.export).expanduser(), api.json_bytes({"format": "agentfleet.preset.v1", **values}), 0o644)
            return 0
        if args.preset:
            selected = [f for f in FEATURES if f.key in values]
        elif args.after_install:
            selected = applicable
        elif args.yes:
            # --yes keeps values, so it installs nothing and runs no action.
            selected = [f for f in unasked if f.key not in ACTIONS]
        else:
            selected = list(FEATURES)
        if args.after_install and not selected:
            if skipped_brain or skipped_local_memory:
                save_state(api, paths, state_path, [*asked, *skipped_brain, *skipped_local_memory])
            return 0
        if not registered and any(f.key in PLUGIN_IDS for f in selected):
            print(MODS_MESSAGE)
        groups = (
            ("Context", {"compact", "jev", "contextMode", "tokenWeather"}),
            ("Lanes", {"workStyle", "askFirst", "branchCheck", "reviewerAccess", "laneGuard", "mainSessionGuard"}),
            ("Status and reports", {"statusBand", "sessionInfo", "brainMod", "providerUsage", "learn"}),
            ("agent-brain", {"agentBrainInstall", "agentBrain", "agentBrainLocalMemory"}),
            ("Updates and checks", {"autoUpdate", "instructions"}),
        )
        group_names = {key: title for title, keys in groups for key in keys}
        printed_groups = set()
        answered = set()
        for feature in selected:
            key = feature.key
            if (key in PLUGIN_IDS and not registered) or key in answered or key in skipped_context_mode:
                continue
            if interactive and key in group_names and group_names[key] not in printed_groups:
                print(group_names[key])
                printed_groups.add(group_names[key])
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
            if key in AGENT_BRAIN_KEYS and not agent_brain_applies(paths, key):
                if key == "agentBrain" and key in values:
                    # A preset's choice is saved so installing agent-brain
                    # later does not connect it against that choice.
                    provider_catalog.write_preferences(paths.home, {"agentBrain": values[key]})
                if not install_pending():
                    asked = save_state(api, paths, state_path, [*asked, key])
                continue
            if key == LOCAL_MEMORY_KEY:
                memory_state = local_memory_state(paths, asked)
                if memory_state in ("skip", "later"):
                    if memory_state == "skip":
                        asked = save_state(api, paths, state_path, [*asked, key])
                    continue
                active = memory_state == "active"
                if key in values:
                    want = values[key]
                elif active:
                    want = True
                else:
                    # Memory needs a yes: Enter and --yes keep it off, as
                    # agent-brain's own [y/N] consent prompt does.
                    want = interactive and prompt(feature, False)
                if want != active:
                    # The config command turns memory on or off and refreshes
                    # the lanes. On failure it said why; the question stays
                    # open for the next run.
                    if feature.apply(api, paths, want):
                        continue
                else:
                    provider_catalog.write_preferences(paths.home, {key: want})
                values[key] = want
                asked = save_state(api, paths, state_path, asked if key in asked else [*asked, key])
                continue
            if key == "agentBrainInstall":
                want = values[key] if key in values else interactive and prompt(feature, True)
                if want:
                    print("Installing agent-brain from agentbrain.wecansync.com ...")
                    with contextlib.redirect_stdout(io.StringIO()):
                        code = feature.apply(api, paths, True)
                    if code:
                        # Not recorded, so the next run offers it again.
                        print("agentfleet configure: agent-brain was not installed; try again with: agentfleet config agent-brain on",
                              file=sys.stderr)
                        continue
                    print("agent-brain is installed and connected to Claude Code.")
                    if values.get("agentBrain") is False:
                        # A preset that installs it unconnected: the
                        # agentBrain step disconnects it.
                        asked = save_state(api, paths, state_path, [*asked, key])
                        continue
                    # Installing it is the answer to connecting it.
                    answered.add("agentBrain")
                    values["agentBrain"] = True
                    asked = save_state(api, paths, state_path, [*asked, key, "agentBrain"])
                    continue
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
        if args.preset and "prices" in values:
            provider_catalog.write_preferences(paths.home, {"prices": values["prices"]})
        if args.preset and any(key in values for key in ("reservedModels", "lanes")):
            fleet_values = {key: values[key] for key in ("reservedModels", "lanes") if key in values}
            code = apply_fleet_keys(api, paths, fleet_values)
            if code:
                print("agentfleet configure: could not apply reservedModels or lanes from the preset", file=sys.stderr)
                return 1
        if args.preset and "prices" in values:
            values["prices"] = provider_catalog.load_preferences(paths.home)["prices"]
        # While installing agent-brain is still open, so is connecting it.
        still_skipped = [] if install_pending() else [
            key for key in AGENT_BRAIN_KEYS if key not in asked and not agent_brain_applies(paths, key)]
        if still_skipped:
            asked = save_state(api, paths, state_path, [*asked, *still_skipped])
        for feature in FEATURES:
            if feature.key in ACTIONS or feature.key == "agentBrainInstall":
                continue
            value = values[feature.key] if feature.key in values else feature.read(api, paths)
            print(f"{feature.key}: {display(value)}")
        if args.preset and "prices" in values:
            count = len(values["prices"])
            print(f"prices: {count} model{'s' if count != 1 else ''}")
        if interactive and (first_run or not args.after_install):
            # Where the settings this wizard does not ask about are changed.
            print("Lane models: /fleet-setup inside Claude Code")
            print("Provider and profiles: agentfleet use")
            print("Model prices: agentfleet config prices")
        print("Restart Claude Code to apply.")
        return 0
    except (OSError, ValueError, RuntimeError, EOFError, KeyboardInterrupt):
        # CLI/settings failures can contain sensitive data; report only the step.
        print("agentfleet configure: failed at " + locals().get("key", "reading configuration"), file=sys.stderr)
        return 1
