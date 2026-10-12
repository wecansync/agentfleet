#!/usr/bin/env python3
"""Synchronize a verified model context budget without racing other writers.

The provider cache is accepted only when its endpoint, account scope, schema,
and freshness match the current gateway settings. A static full window and
Claude Code's percentage override apply the same percentage to every model
without recalculation on switches. Only a verified gateway context sets the
model context cap; a suffix or display label never inflates capacity. Fail-open
is intentional for hook use: malformed payloads, stale/offline discovery, lock
contention, or malformed settings leave the existing file unchanged.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path

# Set before importing provider_catalog: CPython writes a module's .pyc
# during import, before the module body (and its own identical guard) can
# run. Without this, importing it here creates a __pycache__ directory next
# to the managed scripts -- which the installer's own bundle-inventory
# preflight then rejects as an unlisted path, and which leaves stray
# generated files in an installed home.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from provider_catalog import (  # noqa: E402
    acquire_lock,
    available_rows,
    cache_rows,
    compaction_settings,
    COMPACT_PERCENT_ENV,
    load_policy,
    load_preferences,
    NATIVE_CONTEXT_BASIS,
    release_lock,
    strip_known_suffix,
)

# Windows pipes and consoles default to a legacy encoding while provider
# payloads may contain non-ASCII text; emit stdout as UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def arg_path(argv: list[str], name: str, default: Path) -> Path:
    try:
        index = argv.index(name)
        if index + 1 < len(argv):
            return Path(argv[index + 1]).expanduser().resolve()
    except (ValueError, OSError, RuntimeError):
        pass
    return default


def gateway_model_context(model: str, catalog: dict) -> int | None:
    clean = strip_known_suffix(model.strip())
    model_info = catalog.get(model.strip())
    if not isinstance(model_info, dict):
        if clean in catalog:
            model_info = catalog[clean]
        elif f"{clean}[1m]" in catalog:
            model_info = catalog[f"{clean}[1m]"]
        else:
            model_info = next((row for runtime_id, row in catalog.items()
                               if strip_known_suffix(runtime_id) == clean), None)
    if not isinstance(model_info, dict):
        return None
    raw = model_info.get("context_length")
    if isinstance(raw, bool):
        return None
    try:
        context = int(raw)
    except (TypeError, ValueError):
        return None
    return context if context >= 100_000 else None


def update_context(
    payload: object,
    home: Path,
    config_home: Path,
    *,
    already_locked: bool = False,
    percent_override: object = None,
) -> dict | None:
    if not isinstance(payload, dict):
        return None
    # Take the shared lock before reading anything: the profile, model and
    # endpoint decide every value written below, and another writer (a
    # profile switch, the installer) must not change them in between.
    fd = None
    lock_path = None
    if not already_locked:
        fd, lock_path = acquire_lock(home, timeout=0.75)
        if fd is None:
            return None
    try:
        return _update_context_locked(payload, home, config_home, percent_override)
    finally:
        if fd is not None and lock_path is not None:
            release_lock(fd, lock_path)


def _update_context_locked(payload: dict, home: Path, config_home: Path, percent_override: object) -> dict | None:
    settings_path = home / ".claude" / "settings.json"
    cache_path = home / ".claude" / "cache" / "provider-models-cache.json"
    policy_path = config_home / "delegate-skills" / "provider-policy.json"
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    if not isinstance(settings, dict):
        return None
    model = payload.get("to_model") or payload.get("model") or settings.get("model")
    if not isinstance(model, str) or not model.strip():
        return None
    preferences = load_preferences(home)
    percent = preferences["compactPercent"] if percent_override is None else percent_override
    env = settings.get("env")
    env = env if isinstance(env, dict) else {}
    endpoint = env.get("ANTHROPIC_BASE_URL")
    native = not (isinstance(endpoint, str) and endpoint)
    if native:
        context = NATIVE_CONTEXT_BASIS
    else:
        token = env.get("ANTHROPIC_AUTH_TOKEN")
        if not isinstance(token, str) or not token:
            return None
        try:
            policy = load_policy(policy_path)
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            rows = cache_rows(cached, endpoint, token)
        except (OSError, UnicodeError, ValueError):
            rows = None
        catalog = available_rows(rows, policy) if rows is not None else {}
        context = gateway_model_context(model, catalog)
    compact = compaction_settings(context, percent)
    if compact is None:
        return None
    budget = compact["window"]
    value = str(budget)
    percent_value = str(compact["percent"])
    max_context = compact["maxContext"]

    temp = settings_path.with_name(settings_path.name + ".tmp." + str(os.getpid()) + "." + secrets.token_hex(6))
    try:
        try:
            latest = json.loads(settings_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            return None
        if not isinstance(latest, dict):
            return None
        latest_env = latest.get("env")
        if not isinstance(latest_env, dict):
            latest_env = {}
            latest["env"] = latest_env
        compact_changed = (
            latest.get("autoCompactWindow") != budget
            or latest_env.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW") != value
            or latest_env.get(COMPACT_PERCENT_ENV) != percent_value
        )
        context_changed = (not native and max_context is not None
                           and latest_env.get("CLAUDE_CODE_MAX_CONTEXT_TOKENS") != str(max_context))
        changed = compact_changed or context_changed
        if changed:
            latest["autoCompactWindow"] = budget
            latest_env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = value
            latest_env[COMPACT_PERCENT_ENV] = percent_value
            if not native and max_context is not None:
                latest_env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = str(max_context)
            data = (json.dumps(latest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            fd_temp = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                view = memoryview(data)
                while view:
                    written = os.write(fd_temp, view)
                    if written <= 0:
                        raise OSError("short settings write")
                    view = view[written:]
                os.fsync(fd_temp)
            finally:
                os.close(fd_temp)
            os.chmod(temp, 0o600)
            os.replace(temp, settings_path)
    except OSError:
        try:
            temp.unlink()
        except OSError:
            pass
        return None

    result = {"model": model.strip(), "percent": percent, "window": budget, "native": native, "changed": changed}
    event = payload.get("hook_event_name")
    # A new percent or window is read at startup, so say so on any event;
    # a model switch alone only ever changes the context cap.
    if event in {"PreModelSwitch", "PostModelSwitch", "SessionStart"} and compact_changed:
        result["systemMessage"] = (
            f"Auto-compact set to {percent}% of each model's context. Restart Claude Code to apply."
            + ("" if native or max_context is not None else " The model's context is unknown, so its limit is unchanged.")
        )
    elif event in {"PreModelSwitch", "PostModelSwitch", "SessionStart"} and context_changed:
        result["systemMessage"] = (
            f"Context limit for {model.strip()} set to {max_context} tokens; restart Claude Code to apply it. "
            f"Auto-compact stays at {percent}% of each model's context."
        )
    return result


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        argv = sys.argv[1:]
        script_home = Path(__file__).resolve().parent.parent
        home = arg_path(argv, "--home", script_home)
        config_home = arg_path(argv, "--config-home", home / ".config")
        result = update_context(payload, home, config_home)
        if result and result.get("systemMessage"):
            print(json.dumps({"systemMessage": result["systemMessage"]}, ensure_ascii=False))
    except (OSError, UnicodeError, ValueError, TypeError):
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
