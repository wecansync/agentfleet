#!/usr/bin/env python3
"""Incremental AgentFleet subagent reports, outage notices, and private usage counts."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import shlex
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

sys.dont_write_bytecode = True
_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)
try:
    from provider_catalog import catalog_prices, effective_prices, load_preferences, price_for
except ImportError:  # An incomplete installation must not break a hook.
    catalog_prices = None
    effective_prices = None
    load_preferences = None
    price_for = None
try:
    import provider_usage
except ImportError:
    provider_usage = None

STORE_FORMAT = "agentfleet.watch-state.v1"
OUTAGE_FORMAT = "agentfleet.outages.v1"
USAGE_FORMAT = "agentfleet.usage.v1"
MAIN_USAGE_FORMAT = "agentfleet.main-usage.v1"
REPORT_MAX_BYTES = 256 * 1024
SCAN_MAX_BYTES = 8 * 1024 * 1024
SCAN_MAX_SECONDS = 1.2
STATE_RETENTION = timedelta(days=7)
USAGE_RETENTION = timedelta(days=30)
MAX_CONTEXT_LINE = 400
MAX_CONTEXT_TOTAL = 1500

# Keep this in sync with route-to-fleet.py. Complete commands are redacted before
# truncation so a credential cannot straddle the persisted boundary.
_RAW_SECRETS = re.compile(
    r"(?:"
    r"sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{8,}|xox[baprs]-[A-Za-z0-9-]{8,}"
    r"|github_pat_[A-Za-z0-9_]{8,}|[sr]k_(?:live|test)_[A-Za-z0-9]{8,}"
    r"|(?<=Bearer )\S+|(?<=bearer )\S+"
    r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}|AKIA[0-9A-Z]{12,}"
    r"|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
    r"|\b(?:\d[ -]*?){13,19}\b"
    r"|(?<=--password=)\S+|(?<=--password )\S+"
    r"|(?<=\s-p)\S+"
    r"|(?<=MYSQL_PWD=)\S+|(?<=PGPASSWORD=)\S+|(?<=AWS_SECRET_ACCESS_KEY=)\S+"
    r"|(?<![A-Za-z0-9_-])(?i:(?:password|passwd|secret|token|api[_-]?key|authorization)\s*[=:]\s*)\S+"
    r")"
)


class _SecretsRegex:
    def __init__(self, pattern: re.Pattern):
        self._pattern = pattern
        self.pattern = pattern.pattern

    def search(self, text: str):
        if re.search(r"[A-Za-z][A-Za-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@", text):
            return True
        return self._pattern.search(text)

    def sub(self, repl: str, text: str) -> str:
        text = re.sub(
            r"([A-Za-z][A-Za-z0-9+.-]*://[^/\s:@]+:)[^/\s@]+(@)",
            r"\g<1>[redacted]\2",
            text,
        )
        return self._pattern.sub(repl, text)


SECRETS = _SecretsRegex(_RAW_SECRETS)

GIT_HISTORY = {
    "commit", "push", "merge", "rebase", "reset", "restore", "checkout", "switch",
    "clean", "cherry-pick", "revert", "am", "tag", "stash", "update-ref", "filter-branch",
}
GH_WRITES = {
    "pr": {"create", "merge", "close", "edit", "comment", "review", "ready", "reopen"},
    "release": None,
    "repo": {"create", "delete", "edit", "rename", "archive", "fork"},
    "workflow": {"run", "enable", "disable"},
    "issue": {"create", "close", "edit", "comment", "delete", "reopen"},
}
DB_CLIENTS = {"mysql", "mariadb", "psql", "sqlite3", "sqlcmd", "clickhouse-client", "duckdb"}
FALLBACKS = {
    "docs": ("ui", "implement"),
    "tests": ("implement",),
    "ui": ("implement",),
    "implement": ("implement-alt", "implement-deep"),
    "plan": ("plan-alt",),
    "plan-alt": ("plan",),
    "review": ("review-alt", "review-deep"),
    "review-alt": ("review", "review-deep"),
    "review-deep": ("review", "review-alt"),
    "security-review": ("review-deep",),
    "diagnose-static": ("review",),
    "explore-narrow": ("research-codebase",),
    "research-codebase": ("explore-narrow",),
    "triage-static": ("plan",),
    "research-web": (),
}
WRAPPERS = {"sudo", "command", "env", "time", "nohup", "exec"}


# Stop reasons that end a lane's turn; a record without one is mid-message.
TERMINAL_STOP_REASONS = {"end_turn", "stop_sequence", "max_tokens", "refusal"}
OUTAGE_MINUTES = 15
# A 429 is usually an exhausted quota, which lasts longer than an overload.
QUOTA_OUTAGE_MINUTES = 60
# Server errors from two or more vendors within this many minutes mean the
# gateway itself is failing, not one model.
GATEWAY_WINDOW_MINUTES = 10
# The lane-guard mod marks a lane it switched to another model this way; keep
# the format in step with mods/lane-guard/hooks/failover.ts.
FAILOVER_SUFFIX = re.compile(r"\s*\(failover: ([^()\s]{1,100})\)\s*$")
# The lane-guard mod's pattern for a model id. Ids are matched exactly, so they
# are checked against this rather than passed through the secret redaction,
# which can rewrite part of an id ("claude-risk-…") and stop it matching.
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+\[\]-]{0,99}")
LANE_GUARD_PLUGIN = "lane-guard@agentfleet"
# A transcript idle this long ended without a final message (killed, turn limit).
IDLE_DONE_SECONDS = 30 * 60
# An agent first seen this long after its last write finished before this watcher ran.
HISTORY_SECONDS = 60 * 60


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utcnow()).astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def _clean_text(value: object, limit: int | None = None) -> str:
    if not isinstance(value, str):
        return ""
    value = "".join(ch for ch in value if unicodedata.category(ch) not in {"Cc", "Cf"})
    value = SECRETS.sub("[redacted]", value)
    value = " ".join(value.split())
    return value[:limit] if limit is not None else value


def _home_default() -> Path:
    return Path(os.environ.get("CLAUDE_FLEET_HOME", Path(__file__).resolve().parent.parent)).expanduser()


def _store(home: Path) -> Path:
    return home / ".claude" / "agentfleet"


def _ensure_private_dir(path: Path, tighten_existing: bool = False) -> None:
    existed = path.exists()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not existed or tighten_existing:
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass


def _atomic_write(path: Path, data: bytes) -> None:
    _ensure_private_dir(path.parent, tighten_existing=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(temp, flags, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short AgentFleet store write")
            view = view[written:]
        os.fsync(fd)
        try:
            os.fchmod(fd, 0o600)
        except (AttributeError, OSError):
            pass
    except BaseException:
        os.close(fd)
        temp.unlink(missing_ok=True)
        raise
    else:
        os.close(fd)
    try:
        os.replace(temp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def _load_json(path: Path, default: object) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def _load_state(path: Path) -> dict:
    value = _load_json(path, {})
    if not isinstance(value, dict) or value.get("format") != STORE_FORMAT:
        return {"format": STORE_FORMAT, "sessions": {}}
    sessions = value.get("sessions")
    return {"format": STORE_FORMAT, "sessions": sessions if isinstance(sessions, dict) else {}}


def _timestamp_from_record(record: dict) -> str | None:
    value = record.get("timestamp")
    parsed = _parse_time(value)
    return _iso(parsed) if parsed else None


def _clean_lane(value: object) -> str:
    if not isinstance(value, str):
        return ""
    lane = value[6:] if value.startswith("fleet-") else value
    return lane if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", lane) else ""


def _resolve_file(value: object, cwd: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        path = Path(value.strip()).expanduser()
        if not path.is_absolute():
            base = Path(cwd).expanduser() if isinstance(cwd, str) and cwd else Path.cwd()
            path = base / path
        return str(path.resolve(strict=False))
    except (OSError, RuntimeError, ValueError):
        return None


def _mask_heredocs(command: str) -> str:
    """Remove heredoc bodies from shell-command classification, retaining the original for SQL."""
    lines = command.splitlines(keepends=True)
    if not lines:
        return command
    out: list[str] = []
    waiting: list[tuple[str, bool]] = []
    body = False
    for line in lines:
        if body:
            candidate = line.rstrip("\r\n")
            delimiter, strip_tabs = waiting[0]
            compare = candidate.lstrip("\t") if strip_tabs else candidate
            out.append("\n" if line.endswith(("\n", "\r")) else "")
            if compare == delimiter:
                waiting.pop(0)
                if not waiting:
                    body = False
            continue
        out.append(line)
        # A conservative delimiter recognizer is enough here; shell syntax
        # classification must not treat heredoc SQL as a second command.
        found = re.findall(r"<<(-?)\s*(?:'([^']+)'|\"([^\"]+)\"|([A-Za-z_][A-Za-z0-9_]*))", line)
        for strip_tabs, single, double, bare in found:
            waiting.append((single or double or bare, bool(strip_tabs)))
        if waiting:
            body = True
    return "".join(out)


def _extract_balanced(command: str, start: int, opener: str, closer: str) -> tuple[str, int]:
    depth = 1
    quote = ""
    escaped = False
    index = start
    while index < len(command):
        char = command[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if char == "\\" and quote != "'":
            escaped = True
            index += 1
            continue
        if quote:
            if char == quote:
                quote = ""
            elif quote == '"' and command.startswith("$(", index):
                nested, end = _extract_balanced(command, index + 2, "(", ")")
                index = end
                continue
            index += 1
            continue
        if char in "'\"":
            quote = char
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return command[start:index], index + 1
        elif char == "`":
            end = index + 1
            while end < len(command):
                if command[end] == "`" and (end == 0 or command[end - 1] != "\\"):
                    break
                end += 1
            index = min(end + 1, len(command))
            continue
        index += 1
    return command[start:], len(command)


def _split_shell(command: str) -> tuple[list[str], list[str]]:
    command = _mask_heredocs(command)
    segments: list[str] = []
    nested: list[str] = []
    current: list[str] = []
    quote = ""
    escaped = False
    index = 0
    while index < len(command):
        char = command[index]
        if escaped:
            current.append(char)
            escaped = False
            index += 1
            continue
        if char == "\\" and quote != "'":
            current.append(char)
            escaped = True
            index += 1
            continue
        if quote == "'":
            current.append(char)
            if char == "'":
                quote = ""
            index += 1
            continue
        if quote == '"':
            if command.startswith("$(", index):
                inner, end = _extract_balanced(command, index + 2, "(", ")")
                nested.append(inner)
                current.append(" ")
                index = end
                continue
            if char == "`":
                end = index + 1
                inner: list[str] = []
                while end < len(command):
                    if command[end] == "`" and command[end - 1] != "\\":
                        break
                    inner.append(command[end])
                    end += 1
                nested.append("".join(inner))
                current.append(" ")
                index = min(end + 1, len(command))
                continue
            current.append(char)
            if char == '"':
                quote = ""
            index += 1
            continue
        if char in "'\"":
            quote = char
            current.append(char)
            index += 1
            continue
        if command.startswith("$(", index):
            inner, end = _extract_balanced(command, index + 2, "(", ")")
            nested.append(inner)
            current.append(" ")
            index = end
            continue
        if char == "`":
            end = index + 1
            inner = []
            while end < len(command):
                if command[end] == "`" and command[end - 1] != "\\":
                    break
                inner.append(command[end])
                end += 1
            nested.append("".join(inner))
            current.append(" ")
            index = min(end + 1, len(command))
            continue
        if char in ";|&\n":
            if current:
                segments.append("".join(current))
                current = []
            if char in "|&" and index + 1 < len(command) and command[index + 1] == char:
                index += 1
            index += 1
            continue
        current.append(char)
        index += 1
    if current:
        segments.append("".join(current))
    return segments, nested


SHELL_KEYWORDS = {"do", "then", "else", "elif", "if", "while", "until", "time", "fi", "done"}


def _words(segment: str) -> list[str]:
    try:
        raw = shlex.split(segment, posix=True, comments=False)
    except (ValueError, TypeError):
        raw = re.findall(r"[^\s]+", segment)
    words = []
    for w in raw:
        cleaned = w
        if not w.startswith("${") and not w.startswith("$("):
            cleaned = re.sub(r"^[({!]+", "", cleaned)
            cleaned = re.sub(r"[)}]+$", "", cleaned)
        if cleaned:
            words.append(cleaned)
    return words


def _skip_wrapper(words: list[str]) -> list[str]:
    idx = 0
    changed = True
    while idx < len(words) and changed:
        changed = False
        token = words[idx]

        if token in SHELL_KEYWORDS:
            idx += 1
            changed = True
            continue

        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", token):
            idx += 1
            changed = True
            continue

        base = Path(token.replace("\\", "/")).name.lower()
        if base.endswith((".exe", ".cmd", ".bat")):
            base = base.rsplit(".", 1)[0]

        if base in {"sudo", "doas"}:
            idx += 1
            val_opts = {"-u", "-g", "-h", "-p", "-c", "-a", "-C", "-D", "-R", "-t", "-U", "-r", "-T"}
            while idx < len(words) and words[idx].startswith("-"):
                opt = words[idx]
                idx += 1
                if opt == "--":
                    break
                if opt in val_opts and idx < len(words) and not words[idx].startswith("-"):
                    idx += 1
            changed = True
            continue

        if base == "env":
            idx += 1
            while idx < len(words):
                opt = words[idx]
                if opt in {"-i", "-0"}:
                    idx += 1
                elif opt in {"-u", "--unset", "-C", "--chdir"}:
                    idx += 2
                elif opt.startswith("-") and opt != "--":
                    idx += 1
                elif opt == "--":
                    idx += 1
                    break
                elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", opt):
                    idx += 1
                else:
                    break
            changed = True
            continue

        if base in {"command", "nohup"}:
            idx += 1
            while idx < len(words) and words[idx].startswith("-") and words[idx] != "--":
                idx += 1
            if idx < len(words) and words[idx] == "--":
                idx += 1
            changed = True
            continue

        if base == "exec":
            idx += 1
            while idx < len(words) and words[idx].startswith("-"):
                opt = words[idx]
                idx += 1
                if opt == "--":
                    break
                if opt == "-a" and idx < len(words) and not words[idx].startswith("-"):
                    idx += 1
            changed = True
            continue

        if base == "time":
            idx += 1
            while idx < len(words) and words[idx].startswith("-"):
                opt = words[idx]
                idx += 1
                if opt == "--":
                    break
                if opt == "-o" and idx < len(words) and not words[idx].startswith("-"):
                    idx += 1
            changed = True
            continue

        if base == "nice":
            idx += 1
            while idx < len(words) and words[idx].startswith("-"):
                opt = words[idx]
                idx += 1
                if opt == "--":
                    break
                if opt == "-n" and idx < len(words):
                    idx += 1
            changed = True
            continue

        if base == "ionice":
            idx += 1
            while idx < len(words) and words[idx].startswith("-"):
                opt = words[idx]
                idx += 1
                if opt == "--":
                    break
                if opt in {"-c", "-n", "-p"} and idx < len(words) and not words[idx].startswith("-"):
                    idx += 1
            changed = True
            continue

        if base == "stdbuf":
            idx += 1
            while idx < len(words) and words[idx].startswith("-"):
                opt = words[idx]
                idx += 1
                if opt == "--":
                    break
                if opt in {"-i", "-o", "-e"} and idx < len(words) and not words[idx].startswith("-"):
                    idx += 1
            changed = True
            continue

        if base == "timeout":
            idx += 1
            while idx < len(words) and words[idx].startswith("-"):
                opt = words[idx]
                idx += 1
                if opt == "--":
                    break
                if opt in {"-s", "-k", "--signal", "--kill-after"} and idx < len(words) and not words[idx].startswith("-"):
                    idx += 1
            if idx < len(words) and not words[idx].startswith("-"):
                idx += 1
            changed = True
            continue

        if base == "xargs":
            idx += 1
            val_opts = {
                "-I", "-n", "-L", "-P", "-s", "-E", "-a", "-d", "-e",
                "--max-args", "--max-procs", "--max-lines", "--arg-file", "--delimiter"
            }
            while idx < len(words) and words[idx].startswith("-"):
                opt = words[idx]
                idx += 1
                if opt == "--":
                    break
                if opt in val_opts and idx < len(words) and not words[idx].startswith("-"):
                    idx += 1
            changed = True
            continue

        if base == "find":
            exec_idx = -1
            for j in range(idx + 1, len(words)):
                if words[j] in {"-exec", "-execdir"}:
                    exec_idx = j + 1
                    break
            if exec_idx != -1 and exec_idx < len(words):
                idx = exec_idx
                changed = True
                continue

        if base == "bundle" and idx + 1 < len(words) and words[idx + 1] == "exec":
            idx += 2
            while idx < len(words) and words[idx].startswith("-"):
                idx += 1
            changed = True
            continue

        if base in {"uv", "poetry", "pipenv"} and idx + 1 < len(words) and words[idx + 1] == "run":
            idx += 2
            while idx < len(words) and words[idx].startswith("-"):
                opt = words[idx]
                idx += 1
                if opt == "--":
                    break
            changed = True
            continue

        if base in {"npx", "bunx"}:
            idx += 1
            while idx < len(words) and words[idx].startswith("-"):
                opt = words[idx]
                idx += 1
                if opt == "--":
                    break
                if opt in {"-p", "--package"} and idx < len(words) and not words[idx].startswith("-"):
                    idx += 1
            changed = True
            continue

        if base == "npm" and idx + 1 < len(words) and words[idx + 1] == "exec":
            idx += 2
            while idx < len(words) and words[idx].startswith("-") and words[idx] != "--":
                idx += 1
            if idx < len(words) and words[idx] == "--":
                idx += 1
            changed = True
            continue

        if base == "pnpm" and idx + 1 < len(words) and words[idx + 1] in {"exec", "dlx"}:
            idx += 2
            while idx < len(words) and words[idx].startswith("-") and words[idx] != "--":
                idx += 1
            if idx < len(words) and words[idx] == "--":
                idx += 1
            changed = True
            continue

        if base == "yarn" and idx + 1 < len(words) and words[idx + 1] == "dlx":
            idx += 2
            while idx < len(words) and words[idx].startswith("-") and words[idx] != "--":
                idx += 1
            if idx < len(words) and words[idx] == "--":
                idx += 1
            changed = True
            continue

        if base in {"docker", "docker-compose"}:
            cur = idx + 1
            if base == "docker" and cur < len(words) and words[cur] == "compose":
                cur += 1
            if cur < len(words) and words[cur] in {"exec", "run"}:
                cur += 1
                val_opts = {
                    "-e", "-u", "-w", "-v", "--env", "--user", "--workdir", "--volume",
                    "--entrypoint", "-p", "--publish", "--name"
                }
                while cur < len(words) and words[cur].startswith("-"):
                    opt = words[cur]
                    cur += 1
                    if opt == "--":
                        break
                    if opt in val_opts and cur < len(words) and not words[cur].startswith("-"):
                        cur += 1
                if cur < len(words) and not words[cur].startswith("-"):
                    cur += 1
                    idx = cur
                    changed = True
                    continue

        if base == "kubectl" and idx + 1 < len(words) and words[idx + 1] == "exec":
            dash_idx = -1
            for j in range(idx + 2, len(words)):
                if words[j] == "--":
                    dash_idx = j + 1
                    break
            if dash_idx != -1 and dash_idx < len(words):
                idx = dash_idx
                changed = True
                continue

        if base == "sail":
            idx += 1
            changed = True
            continue

    return words[idx:]


def _program(words: list[str]) -> str:
    if not words:
        return ""
    if words[0] == ".":
        # The shell's source builtin; Path(".").name would be empty.
        return "."
    name = Path(words[0].replace("\\", "/")).name.lower()
    return re.sub(r"\.(exe|cmd|bat)$", "", name)


def _extract_subcommand(args: list[str]) -> tuple[str, list[str]]:
    val_opts = {
        "-n", "--namespace", "--context", "--kubeconfig", "-s", "--server",
        "--cluster", "--user", "-chdir", "--kube-context", "-H", "--host",
    }
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            i += 1
            break
        if a in val_opts:
            i += 2
        elif a.startswith(("-n=", "--namespace=", "--context=", "--kubeconfig=", "-chdir=", "--kube-context=", "-H=", "--host=")):
            i += 1
        elif a.startswith("-"):
            i += 1
        else:
            break
    if i >= len(args):
        return "", []
    return args[i].lower(), args[i + 1:]


def _git_parts(words: list[str]) -> tuple[str, list[str]]:
    index = 1
    val_opts = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env"}
    while index < len(words):
        value = words[index]
        if value == "--":
            index += 1
            break
        if value in val_opts:
            index += 2
        elif value.startswith(("-C", "-c", "--git-dir=", "--work-tree=", "--namespace=", "--super-prefix=", "--config-env=")):
            index += 1
        elif value.startswith("-"):
            index += 1
        else:
            break
    return (words[index].lower(), words[index + 1:]) if index < len(words) else ("", [])


def _gh_write(words: list[str]) -> bool:
    if len(words) < 2:
        return False
    group = words[1].lower()
    args = [value.lower() for value in words[2:]]
    if group == "release":
        return bool(args and args[0] not in {"list", "view", "download"})
    if group == "run":
        return bool(args and args[0] in {"rerun", "cancel", "delete"})
    actions = GH_WRITES.get(group)
    if isinstance(actions, set) and args and args[0] in actions:
        return True
    if group == "api":
        method = ""
        has_field = False
        i = 0
        while i < len(args):
            a = args[i]
            if a in {"-x", "--method"} and i + 1 < len(args):
                method = args[i + 1].upper()
                i += 2
                continue
            elif a.startswith("-x") and len(a) > 2:
                method = a[2:].upper()
            elif a.startswith("--method="):
                method = a.partition("=")[2].upper()
            elif a in {"-f", "-F", "--field", "--raw-field", "--input"} or a.startswith(("-f", "-F", "--field=", "--raw-field=", "--input=")):
                has_field = True
            i += 1
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            return True
        if not method and has_field:
            return True
        if method and method != "GET" and has_field:
            return True
    return False


def _migration(words: list[str], program: str) -> bool:
    args = [part.lower() for part in words[1:]]
    artisan_args = None
    if program == "artisan":
        artisan_args = args
    elif program == "php" and len(args) >= 1 and args[0] == "artisan":
        artisan_args = args[1:]
    if artisan_args:
        action = artisan_args[0]
        if action.startswith("migrate"):
            if action == "migrate:status" or "--pretend" in artisan_args:
                pass
            else:
                return True
        elif action == "db:wipe":
            return True

    if program in {"rails", "rake"}:
        if args and (args[0] == "db:migrate:status" or args[0] == "db:version"):
            return False
        return bool(args and re.match(r"^db:(migrate|drop|reset|rollback|setup|schema:load)", args[0]))
    if program == "prisma":
        return len(args) >= 2 and ((args[0] == "migrate" and args[1] in {"dev", "deploy", "reset"}) or args[:2] == ["db", "push"])
    if program == "alembic":
        return bool(args and args[0] in {"upgrade", "downgrade"})
    if program == "manage.py":
        return bool(args and args[0] in {"migrate", "flush"})
    if program in {"python", "python3", "pypy", "pypy3"} and args and Path(args[0].replace("\\", "/")).name == "manage.py":
        return len(args) >= 2 and args[1] in {"migrate", "flush"}
    if program == "knex":
        return bool(args and args[0].startswith("migrate:"))
    if program in {"sequelize", "sequelize-cli"}:
        return bool(args and (args[0].startswith("db:migrate") or (len(args) >= 2 and args[0] == "db" and args[1].startswith("migrate"))))
    if program == "diesel":
        return len(args) >= 2 and args[0] == "migration" and args[1] in {"run", "revert", "redo"}
    if program == "goose":
        return bool(args and args[0] in {"up", "down", "reset"})
    if program == "flyway":
        return bool(args and args[0] in {"migrate", "clean"})
    if program == "dropdb":
        return True
    if program == "mysqladmin":
        return "drop" in args
    if program == "redis-cli":
        return any(value in {"flushall", "flushdb"} for value in args)
    return False


def _dangerous_rm(words: list[str], program: str) -> bool:
    if program != "rm":
        return False
    recursive = False
    targets: list[str] = []
    options = True
    for token in words[1:]:
        if options and token == "--":
            options = False
            continue
        if options and token.startswith("-"):
            if token in {"--recursive", "--force"}:
                recursive = True
            elif token.startswith("--"):
                continue
            elif any(char in token[1:] for char in "rRf"):
                recursive = True
            continue
        targets.append(token)
    if not recursive:
        return False
    return any(
        target in {"/", "/*", "~", "~/", "~/*", "$HOME", "${HOME}", "..", ".git", "*"}
        or target.startswith("../")
        for target in targets
    )


def _remote_target(words: list[str], program: str) -> bool:
    if program not in {"rsync", "scp"}:
        return False
    for value in words[1:]:
        if re.match(r"^[A-Za-z]:[\\/]", value):
            continue
        colon = value.find(":")
        if colon > 0 and "/" not in value[:colon] and "\\" not in value[:colon] and not re.match(r"^[A-Za-z]:", value):
            return True
    return False


def _segment_reason(segment: str) -> str | None:
    words = _skip_wrapper(_words(segment))
    if not words:
        return None
    program = _program(words)
    args = words[1:]
    if program == "git":
        subcommand, sargs = _git_parts(words)
        if subcommand in GIT_HISTORY or subcommand in {"pull", "bisect"}:
            if subcommand == "bisect":
                if sargs and sargs[0] in {"log", "visualize", "view"}:
                    pass
                else:
                    return "git-history"
            elif subcommand == "tag":
                if any(a in {"-d", "-D", "-f", "--delete", "--force"} for a in sargs):
                    return "git-history"
                if not sargs or any(a in {"-l", "--list"} or a.startswith(("-l", "--list=", "--sort", "--points-at", "--contains")) or re.match(r"^-n\d*$", a) for a in sargs):
                    pass
                else:
                    return "git-history"
            elif subcommand == "stash":
                if sargs and sargs[0] in {"list", "show"}:
                    pass
                else:
                    return "git-history"
            else:
                return "git-history"
        if subcommand == "branch" and any(
            arg in {"-d", "-D", "-m", "-M", "--delete", "--move", "-f", "--force"}
            or (arg.startswith("-") and not arg.startswith("--") and any(ch in arg[1:] for ch in "dDmMf"))
            for arg in sargs
        ):
            return "git-history"
        if subcommand == "worktree" and sargs and sargs[0] in {"remove", "prune"}:
            return "git-history"
    elif program == "gh" and _gh_write(words):
        return "github-write"

    if program in {"npm", "pnpm", "yarn", "bun"} and len(words) > 1 and words[1].lower() == "publish":
        return "publish-deploy"
    if program == "twine" and len(words) > 1 and words[1].lower() == "upload":
        return "publish-deploy"
    if program == "cargo" and len(words) > 1 and words[1].lower() == "publish":
        return "publish-deploy"
    if program == "gem" and len(words) > 1 and words[1].lower() == "push":
        return "publish-deploy"
    if program == "docker":
        subcmd, _ = _extract_subcommand(args)
        if subcmd == "push":
            return "publish-deploy"
    if program == "kubectl":
        subcmd, _ = _extract_subcommand(args)
        if subcmd in {"apply", "delete", "rollout", "scale", "patch", "replace"}:
            if subcmd == "apply" and any(a == "--dry-run" or a.startswith("--dry-run=") for a in args):
                pass
            else:
                return "publish-deploy"
    if program == "helm":
        subcmd, _ = _extract_subcommand(args)
        if subcmd in {"install", "upgrade", "uninstall", "rollback"}:
            return "publish-deploy"
        if subcmd in {"template", "list", "status"}:
            pass
    if program == "terraform":
        subcmd, _ = _extract_subcommand(args)
        if subcmd in {"apply", "destroy"}:
            return "publish-deploy"
        if subcmd == "plan":
            pass
    if program == "vercel" and ("--prod" in words[1:] or (len(words) > 1 and words[1].lower() == "deploy")):
        return "publish-deploy"
    if program == "firebase" and len(words) > 1 and words[1].lower() == "deploy":
        return "publish-deploy"
    if program in {"fly", "flyctl"} and len(words) > 1 and words[1].lower() == "deploy":
        return "publish-deploy"
    if words[0].lower().endswith("deploy.sh"):
        return "publish-deploy"
    if program in {"bash", "sh", "zsh", "source", "."} and any(a.lower().endswith("deploy.sh") or "/deploy.sh" in a.lower() for a in args):
        return "publish-deploy"
    if _remote_target(words, program):
        return "publish-deploy"

    if _migration(words, program):
        return "migration"
    if _dangerous_rm(words, program):
        return "rm-dangerous"
    return None


def _db_client(segment: str) -> bool:
    words = _skip_wrapper(_words(segment))
    if not words:
        return False
    program = _program(words)
    if program in DB_CLIENTS:
        return True
    return (program == "cockroach" and len(words) > 1 and words[1].lower() == "sql") or (
        program == "wp" and len(words) > 2 and words[1:3] == ["db", "query"]
    )


def _sql_write(command: str) -> bool:
    if re.search(r"\bDROP\s+(?:TABLE|DATABASE|SCHEMA|INDEX|VIEW|MATERIALIZED\s+VIEW|FUNCTION|PROCEDURE|TRIGGER|ROLE|USER|SEQUENCE|TYPE|EXTENSION)\b", command, re.IGNORECASE):
        return True
    if re.search(r"\bTRUNCATE\b", command, re.IGNORECASE):
        return True
    if re.search(r"\bALTER\s+TABLE\b[^;]*?\bDROP\b", command, re.IGNORECASE | re.DOTALL):
        return True
    for statement in command.split(";"):
        if re.search(r"\bDELETE\s+FROM\s+[\w.`\"\[\]-]+\b", statement, re.IGNORECASE) and not re.search(r"\bWHERE\b", statement, re.IGNORECASE):
            return True
        if re.search(r"\bUPDATE\s+[\w.`\"\[\]-]+\s+SET\b", statement, re.IGNORECASE) and not re.search(r"\bWHERE\b", statement, re.IGNORECASE):
            return True
    return False


def _all_segments(command: str, depth: int = 0) -> list[str]:
    if depth > 8:
        return []
    segments, nested = _split_shell(command)
    result = list(segments)
    for segment in segments:
        words = _skip_wrapper(_words(segment))
        program = _program(words)
        if program in {"bash", "sh", "zsh"}:
            for index, value in enumerate(words[:-1]):
                if value == "-c":
                    result.extend(_all_segments(words[index + 1], depth + 1))
                    break
        elif program in {"powershell", "pwsh", "powershell.exe", "pwsh.exe"}:
            # -Command (or any prefix of it) takes the rest of the line.
            for index, value in enumerate(words[:-1]):
                if re.fullmatch(r"[-/]c(o(m(m(a(n(d)?)?)?)?)?)?", value, re.IGNORECASE):
                    result.extend(_all_segments(" ".join(words[index + 1:]), depth + 1))
                    break
    for inner in nested:
        result.extend(_all_segments(inner, depth + 1))
    return result


def forbidden_reason(command: str) -> str | None:
    """Return the first shared lane-guard rule matched by a shell command."""
    try:
        if not isinstance(command, str) or not command:
            return None
        command = re.sub(r"\\\r?\n", "", command)
        segments = _all_segments(command)
        for segment in segments:
            if _db_client(segment):
                words = _skip_wrapper(_words(segment))
                if "<" in segment or any(a in {"-f", "--file"} or a.startswith("--file=") for a in words):
                    return "db-write"
                if _sql_write(segment):
                    return "db-write"
            reason = _segment_reason(segment)
            if reason:
                return reason
    except Exception:
        return None
    return None


def _head_for_segment(segment: str) -> str | None:
    words = _skip_wrapper(_words(segment))
    if not words:
        return None
    program = _program(words)
    words[0] = Path(words[0].replace("\\", "/")).name
    selected = [words[0]]
    # Options before -m (python3 -B -m pytest) do not change the module run.
    options = 1
    while options < len(words) and words[options].startswith("-") and words[options] != "-m":
        options += 1
    if program in {"python", "python3"} and options + 1 < len(words) and words[options] == "-m":
        selected.extend(("-m", words[options + 1]))
    elif program in {"npm", "pnpm", "yarn", "bun"} and len(words) > 2 and words[1] == "run":
        selected.extend(words[1:3])
    elif program == "php" and len(words) > 2 and words[1] == "artisan":
        selected.extend(words[1:3])
    else:
        for value in words[1:]:
            if not value.startswith("-"):
                selected.append(value)
                break
    head = " ".join(selected)
    if len(head) > 60 or SECRETS.search(head):
        return None
    return _clean_text(head, 60) if head else None


def _iter_jsonl(path: Path, offset: int, budget: list[int], deadline: float, callback,
                contains: bytes | None = None, strict: bool = False) -> tuple[int, int]:
    remaining = SCAN_MAX_BYTES - budget[0]
    if remaining <= 0:
        return offset, 0
    try:
        with path.open("rb") as handle:
            handle.seek(max(0, offset))
            data = handle.read(remaining)
    except OSError:
        if strict:
            raise
        return offset, 0
    budget[0] += len(data)
    complete_end = data.rfind(b"\n") + 1
    if complete_end <= 0:
        if len(data) >= SCAN_MAX_BYTES:
            # One line larger than a whole scan would stall every later scan:
            # skip it; the rest of it fails to parse and is ignored too.
            return offset + len(data), len(data)
        # Keep the offset at the partial line; the bounded read still counts.
        return offset, 0
    processed = 0
    for raw_line in data[:complete_end].splitlines(keepends=True):
        if time.monotonic() >= deadline:
            break
        processed += len(raw_line)
        raw_line = raw_line.rstrip(b"\r\n")
        if not raw_line.strip():
            continue
        if contains and (b'"type"' not in raw_line or contains not in raw_line):
            continue
        try:
            record = json.loads(raw_line.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(record, dict):
            try:
                callback(record)
            except Exception:
                if strict:
                    raise
    return offset + processed, len(data)


def _new_agent_state(agent_id: str, lane: str, description: str) -> dict:
    return {
        "agentId": agent_id,
        "lane": lane,
        "description": description,
        "offset": 0,
        "size": -1,
        "files": [],
        "forbidden": [],
        "apiError": None,
        "isApiError": False,
        "apiMessage": "",
        "toolCalls": 0,
        "usageIn": 0,
        "usageOut": 0,
        "usageId": "",
        "usageIdIn": 0,
        "usageIdOut": 0,
        "usageCacheRead": 0,
        "usageCacheWrite": 0,
        "usageIdCacheRead": 0,
        "usageIdCacheWrite": 0,
        "outcome": None,
        "lastStopReason": None,
        "firstAt": None,
        "lastAt": None,
        "model": "",
        "failoverModel": "",
        "explicitModel": False,
        "lastAssistantHasToolUse": False,
        "lastAssistantSeen": False,
        "done": False,
        "reported": False,
    }


def _outcome_from_text(text: str) -> str | None:
    match = re.search(
        r"(?im)^[ \t]*(?:(?:[-*+]\s+)|(?:>\s*)|(?:#{1,6}\s*))*"
        r"(?:\*\*|__)?outcome(?:\*\*|__)?[ \t]*:(?:\*\*|__)?[ \t]*"
        r"(?:\*\*|__)?(done|partial|blocked|failed)(?![\w-])",
        text,
    )
    return match.group(1).lower() if match else None


def _agent_record(state: dict, record: dict, cwd: object) -> None:
    if record.get("type") != "assistant":
        return
    state["lastAssistantSeen"] = True
    message = record.get("message")
    if not isinstance(message, dict):
        return
    timestamp = _timestamp_from_record(record)
    if timestamp:
        if not state.get("firstAt"):
            state["firstAt"] = timestamp
        state["lastAt"] = timestamp
    model = message.get("model")
    synthetic = model == "<synthetic>"
    if isinstance(model, str) and model and not synthetic and not state.get("model"):
        state["model"] = _clean_text(model, 100)
    message_id = message.get("id")
    usage = message.get("usage")
    if isinstance(message_id, str) and isinstance(usage, dict):
        # One API message spans consecutive records (thinking, text, tool_use)
        # and only the later ones carry its final counts: keep the largest
        # per message, and fold it into the totals when the next one starts.
        if message_id != state.get("usageId"):
            state["usageIn"] = _as_int(state.get("usageIn")) + _as_int(state.get("usageIdIn"))
            state["usageOut"] = _as_int(state.get("usageOut")) + _as_int(state.get("usageIdOut"))
            state["usageCacheRead"] = _as_int(state.get("usageCacheRead")) + _as_int(state.get("usageIdCacheRead"))
            state["usageCacheWrite"] = _as_int(state.get("usageCacheWrite")) + _as_int(state.get("usageIdCacheWrite"))
            state["usageId"] = message_id
            state["usageIdIn"] = state["usageIdOut"] = 0
            state["usageIdCacheRead"] = state["usageIdCacheWrite"] = 0
        state["usageIdIn"] = max(_as_int(state.get("usageIdIn")), max(0, _as_int(usage.get("input_tokens"))))
        state["usageIdOut"] = max(_as_int(state.get("usageIdOut")), max(0, _as_int(usage.get("output_tokens"))))
        state["usageIdCacheRead"] = max(
            _as_int(state.get("usageIdCacheRead")), max(0, _as_int(usage.get("cache_read_input_tokens")))
        )
        state["usageIdCacheWrite"] = max(
            _as_int(state.get("usageIdCacheWrite")), max(0, _as_int(usage.get("cache_creation_input_tokens")))
        )
    stop_reason = message.get("stop_reason")
    state["lastStopReason"] = stop_reason if isinstance(stop_reason, str) else None
    content = message.get("content")
    content = content if isinstance(content, list) else []
    tool_uses = [block for block in content if isinstance(block, dict) and block.get("type") == "tool_use"]
    text_blocks = [block for block in content if isinstance(block, dict) and block.get("type") == "text"]
    state["lastAssistantHasToolUse"] = bool(tool_uses)
    if not synthetic:
        if tool_uses:
            state["outcome"] = None
        elif text_blocks:
            text = "\n".join(block.get("text", "") for block in text_blocks if isinstance(block.get("text"), str))
            state["outcome"] = _outcome_from_text(text)
        state["isApiError"] = False
        state["apiError"] = None
        state["apiMessage"] = ""
    if synthetic:
        state["lastAssistantHasToolUse"] = False
        state["isApiError"] = True
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            text = block.get("text")
            match = re.search(r"API\s+Error:\s*(\d{3})\s*:?\s*(.*)", text, re.IGNORECASE | re.DOTALL) if isinstance(text, str) else None
            if match:
                status = int(match.group(1))
                if _is_outage_status(status):
                    state["apiError"] = status
                else:
                    state["isApiError"] = False
                state["apiMessage"] = _clean_text(match.group(2), 160)
                break
    for block in tool_uses:
        state["toolCalls"] = max(0, _as_int(state.get("toolCalls"))) + 1
        name = block.get("name")
        inputs = block.get("input")
        if not isinstance(inputs, dict):
            inputs = {}
        if name in {"Edit", "Write", "MultiEdit"}:
            candidate = inputs.get("file_path")
            if name == "MultiEdit" and isinstance(inputs.get("edits"), list):
                candidates = [candidate] + [edit.get("file_path") for edit in inputs["edits"] if isinstance(edit, dict)]
            else:
                candidates = [candidate]
        elif name == "NotebookEdit":
            candidates = [inputs.get("notebook_path")]
        else:
            candidates = []
        files = set(state.get("files", []))
        for candidate in candidates:
            resolved = _resolve_file(candidate, cwd)
            if resolved:
                files.add(resolved)
        state["files"] = sorted(files)[:50]
        if name == "Bash":
            command = inputs.get("command")
            if isinstance(command, str):
                reason = forbidden_reason(command)
                if reason:
                    rows = state.get("forbidden") if isinstance(state.get("forbidden"), list) else []
                    if len(rows) < 10:
                        rows.append({"rule": reason, "command": _clean_text(command, 160)})
                    state["forbidden"] = rows


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return 0


def _model_key(value: object) -> str:
    """A model id as fleet.json and the outages file spell it, or "" when the
    value is not one."""
    if not isinstance(value, str):
        return ""
    value = value.strip()
    return value if MODEL_ID.fullmatch(value) else ""


def _load_meta(path: Path) -> tuple[str, str, str, bool] | None:
    """Return the lane, the shown description, the model lane-guard switched
    the lane to (empty when it runs on its own model), and whether the caller
    chose the model itself (the Agent tool's model override), in which case
    the run says nothing about the lane's own models."""
    value = _load_json(path, None)
    if not isinstance(value, dict):
        return None
    agent_type = value.get("agentType")
    if not isinstance(agent_type, str) or not agent_type.startswith("fleet-"):
        return None
    lane = _clean_lane(agent_type)
    if not lane:
        return None
    raw = value.get("description", "")
    # Read the marker before truncating, which could cut it off.
    match = FAILOVER_SUFFIX.search(raw) if isinstance(raw, str) else None
    failover = _model_key(match.group(1)) if match else ""
    description = _clean_text(raw, 80)
    chosen = value.get("model")
    explicit = not failover and isinstance(chosen, str) and bool(chosen.strip())
    return lane, description, failover, explicit


def _model_id(value: object) -> str:
    if isinstance(value, dict):
        value = value.get("model") or value.get("primary") or value.get("id")
    return value.strip() if isinstance(value, str) else ""


def _is_outage_status(status: int) -> bool:
    return status == 429 or 500 <= status <= 599


def _outage_window(status: int) -> timedelta:
    return timedelta(minutes=QUOTA_OUTAGE_MINUTES if status == 429 else OUTAGE_MINUTES)


def lane_models(row: object) -> list[str]:
    """A lane's own model, then its fallbacks, in order and without repeats."""
    if not isinstance(row, dict):
        return []
    models: list[str] = []
    fallbacks = row.get("fallbacks") if isinstance(row.get("fallbacks"), list) else []
    for value in [row.get("model"), *fallbacks]:
        model = _model_id(value)
        if model and model not in models:
            models.append(model)
    return models


def effective_model(row: object, model_outages: dict) -> str | None:
    """The model a lane runs on now: the first of its models without a
    current outage. The lane-guard mod applies the same rule at spawn."""
    for model in lane_models(row):
        if model not in model_outages:
            return model
    return None


def _lane_rows(home: Path, cwd: object) -> dict[str, dict]:
    """The fleet map's lanes, with this project's pins (`agentfleet project
    pin` writes <project>/.claude/fleet.local.json) laid over their rows the
    way the agent generator merges them; the lane-guard mod reads the same
    files."""
    config = _load_json(home / ".claude" / "fleet.json", {})
    rows = config.get("lanes") if isinstance(config, dict) else None
    lanes = {name: row for name, row in rows.items()
             if isinstance(name, str) and isinstance(row, dict)} if isinstance(rows, dict) else {}
    if isinstance(cwd, str) and cwd:
        local = _load_json(Path(cwd) / ".claude" / "fleet.local.json", {})
        pinned = local.get("lanes") if isinstance(local, dict) and local.get("version") == "delegate-fleet.local.v1" else None
        if isinstance(pinned, dict):
            for name, row in pinned.items():
                if isinstance(name, str) and isinstance(row, dict):
                    lanes[name] = {**lanes.get(name, {}), **row}
    return lanes


def failover_enabled(home: Path) -> bool:
    """Whether the lane-guard mod, which switches a lane's model, is enabled."""
    settings = _load_json(home / ".claude" / "settings.json", {})
    plugins = settings.get("enabledPlugins") if isinstance(settings, dict) else None
    return isinstance(plugins, dict) and plugins.get(LANE_GUARD_PLUGIN) is True


def _lane_model_for_report(lane: object, home: Path | None = None) -> str:
    if home is None:
        return ""
    config = _load_json(home / ".claude" / "fleet.json", {})
    rows = config.get("lanes", {}) if isinstance(config, dict) else {}
    row = rows.get(_clean_lane(lane), {}) if isinstance(rows, dict) else {}
    model = row.get("model") if isinstance(row, dict) else None
    if isinstance(model, dict):
        model = model.get("model") or model.get("primary") or model.get("id")
    if not model and isinstance(row, dict):
        model = row.get("primaryModel") or row.get("primary")
    return _clean_text(model, 100)


def _report_payload(session_id: str, state: dict, home: Path | None = None, configured: str = "") -> dict:
    usage = state.get("usage") if isinstance(state.get("usage"), dict) else {}
    started = _parse_time(state.get("firstAt"))
    ended = _parse_time(state.get("lastAt"))
    duration = max(0, int((ended - started).total_seconds())) if started and ended else 0
    # States written before usage totals existed kept a per-message dict.
    legacy = [item for item in usage.values() if isinstance(item, dict)]
    input_tokens = (_as_int(state.get("usageIn")) + _as_int(state.get("usageIdIn"))
                    + sum(max(0, _as_int(item.get("input"))) for item in legacy))
    output_tokens = (_as_int(state.get("usageOut")) + _as_int(state.get("usageIdOut"))
                     + sum(max(0, _as_int(item.get("output"))) for item in legacy))
    cache_read_tokens = (_as_int(state.get("usageCacheRead")) + _as_int(state.get("usageIdCacheRead"))
                         + sum(max(0, _as_int(item.get("cacheRead"))) for item in legacy))
    cache_write_tokens = (_as_int(state.get("usageCacheWrite")) + _as_int(state.get("usageIdCacheWrite"))
                          + sum(max(0, _as_int(item.get("cacheWrite"))) for item in legacy))
    return {
        "at": _iso(),
        "session": session_id,
        "agentId": _clean_text(state.get("agentId", ""), 80),
        "lane": _clean_lane(state.get("lane")),
        "model": _clean_text(state.get("model", ""), 100) or _lane_model_for_report(state.get("lane"), home),
        # The model the lane was set to run on, as fleet.json names it; a
        # gateway may record another name (codex-luna as gpt-6-luna), and
        # prices are set under this one.
        "configuredModel": _clean_text(configured, 100),
        "description": _clean_text(state.get("description", ""), 80),
        "files": sorted(set(state.get("files", [])))[:50],
        "forbidden": [
            {"rule": _clean_text(item.get("rule", ""), 40), "command": _clean_text(item.get("command", ""), 160)}
            for item in state.get("forbidden", [])[:10] if isinstance(item, dict)
        ],
        "apiError": _as_int(state.get("apiError")) or None,
        "toolCalls": max(0, _as_int(state.get("toolCalls"))),
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "cacheReadTokens": cache_read_tokens,
        "cacheWriteTokens": cache_write_tokens,
        "outcome": state.get("outcome") if state.get("outcome") in {"done", "partial", "blocked", "failed"} else None,
        "cutOff": bool(state.get("lastAssistantHasToolUse") and not state.get("isApiError")),
        "durationSeconds": duration,
    }


def _append_report(home: Path, row: dict) -> bool:
    path = _store(home) / "lane-reports.jsonl"
    try:
        _ensure_private_dir(path.parent)
        line = (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        try:
            previous = path.read_bytes()
        except OSError:
            previous = b""
        if len(previous) + len(line) > REPORT_MAX_BYTES:
            rotated = path.with_name(path.name + ".1")
            if path.exists():
                os.replace(path, rotated)
                try:
                    os.chmod(rotated, 0o600)
                except OSError:
                    pass
            previous = b""
        _atomic_write(path, previous + line)
        return True
    except (OSError, ValueError, TypeError):
        return False


def _current_rows(rows: object, key) -> dict[str, dict]:
    current: dict[str, dict] = {}
    now = _utcnow()
    if isinstance(rows, dict):
        for name, row in rows.items():
            if not isinstance(name, str) or not isinstance(row, dict):
                continue
            until = _parse_time(row.get("until"))
            status = _as_int(row.get("status"))
            name = key(name)
            if name and until and until > now and status:
                current[name] = row
    return current


def _vendor(model: str) -> str:
    """The vendor of a model id: "codex-sol[1m]" is codex, "claude-opus-5.5" is claude."""
    base = re.sub(r"\[[^\]]*\]$", "", model.strip())
    return base.split("-", 1)[0].lower()


def _gateway_outage(failures: dict[str, dict], now: datetime) -> dict | None:
    """The gateway is failing when server errors (not quota 429s) from two or
    more vendors were recorded within the last GATEWAY_WINDOW_MINUTES."""
    recent: dict[str, dict] = {}
    for model, entry in failures.items():
        status = _as_int(entry.get("status"))
        at = _parse_time(entry.get("at"))
        until = _parse_time(entry.get("until"))
        if not 500 <= status <= 599 or at is None or until is None or until <= now:
            continue
        if now - at > timedelta(minutes=GATEWAY_WINDOW_MINUTES):
            continue
        recent[model] = {"at": at, "until": until, "status": status}
    if len({_vendor(model) for model in recent} - {""}) < 2:
        return None
    ordered = sorted(recent, key=lambda model: recent[model]["at"])
    first = recent[ordered[0]]
    return {
        "at": _iso(first["at"]),
        "until": _iso(max(entry["until"] for entry in recent.values())),
        "status": first["status"],
        "models": ordered,
    }


def _current_gateway(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    at = _parse_time(value.get("at"))
    until = _parse_time(value.get("until"))
    if at is None or until is None or until <= _utcnow():
        return None
    models = value.get("models")
    names = [model for model in models if isinstance(model, str) and model] if isinstance(models, list) else []
    return {"at": _iso(at), "until": _iso(until), "status": _as_int(value.get("status")), "models": names}


def current_outages(home: Path) -> dict:
    """Return the outages file with entries whose retry deadline has passed
    removed. ``lanes`` lists lanes with no working model (dispatch another
    lane); ``models`` lists failed models (lane-guard switches lanes off them);
    ``gateway``, when present, means the gateway itself is failing."""
    value = _load_json(_store(home) / "outages.json", {})
    value = value if isinstance(value, dict) else {}
    outages = {
        "format": OUTAGE_FORMAT,
        "lanes": _current_rows(value.get("lanes"), _clean_lane),
        "models": _current_rows(value.get("models"), _model_key),
    }
    gateway = _current_gateway(value.get("gateway"))
    if gateway:
        outages["gateway"] = gateway
    return outages


def _write_outages(home: Path, lanes: dict, models: dict | None = None, gateway: dict | None = None) -> None:
    path = _store(home) / "outages.json"
    payload: dict = {"format": OUTAGE_FORMAT, "lanes": lanes, "models": models or {}}
    if gateway:
        payload["gateway"] = gateway
    _atomic_write(path, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def _outage_for(home: Path, lane: str) -> dict | None:
    return current_outages(home).get("lanes", {}).get(_clean_lane(lane))


def alternative_lane(home: Path, lane: str, outages: dict | None = None,
                     models: dict | None = None) -> str | None:
    """Choose a configured, non-outage lane on a different model. ``outages``
    (lane -> entry) and ``models`` (model -> entry) replace the stored sets
    while a scan is recording new ones."""
    primary = _clean_lane(lane)
    if not primary:
        return None
    fleet_path = home / ".claude" / "fleet.json"
    config = _load_json(fleet_path, {})
    lane_rows = config.get("lanes", {}) if isinstance(config, dict) else {}
    if not isinstance(lane_rows, dict):
        return None
    candidates = [
        name for name, row in lane_rows.items()
        if isinstance(name, str) and name != primary and isinstance(row, dict) and _clean_lane(row.get("altOf")) == primary
    ]
    candidates.extend(candidate for candidate in FALLBACKS.get(primary, ()) if candidate not in candidates)
    if primary.startswith("implement-") and "implement" not in candidates:
        candidates.append("implement")
    stored = current_outages(home) if outages is None or models is None else {}
    outage_lanes = outages if outages is not None else stored.get("lanes", {})
    model_outages = models if models is not None else stored.get("models", {})
    failover = failover_enabled(home)
    failing_model = _model_id(lane_rows.get(primary, {}).get("model")) if isinstance(lane_rows.get(primary), dict) else ""
    for candidate in candidates:
        row = lane_rows.get(candidate)
        if not isinstance(row, dict):
            continue
        if candidate in outage_lanes:
            continue
        # With lane-guard a lane whose own model is down still works on a
        # fallback; without it, a lane on a failed model is no alternative.
        model = effective_model(row, model_outages) if failover else _model_id(row.get("model"))
        if not model or model in model_outages:
            continue
        if failing_model and model == failing_model:
            continue
        return candidate
    return None


def _display_path(path: str, cwd: object) -> str:
    try:
        base = Path(cwd).resolve(strict=False) if isinstance(cwd, str) and cwd else None
        candidate = Path(path).resolve(strict=False)
        if base is not None:
            try:
                return candidate.relative_to(base).as_posix()
            except ValueError:
                pass
        return candidate.as_posix()
    except (OSError, RuntimeError, ValueError):
        return _clean_text(path, 180)


def _lane_line(row: dict, cwd: object) -> str | None:
    files = row.get("files") if isinstance(row.get("files"), list) else []
    forbidden = row.get("forbidden") if isinstance(row.get("forbidden"), list) else []
    api_error = row.get("apiError")
    if not files and not forbidden and not api_error and not row.get("isApiError"):
        return None
    lane = _clean_lane(row.get("lane"))
    agent = _clean_text(row.get("agentId", ""), 80)
    description = _clean_text(row.get("description", ""), 80)
    parts = [f"AgentFleet lane report: fleet-{lane} (agent {agent}"]
    if description:
        parts[-1] += f', "{description}"'
    parts[-1] += ")"
    if files:
        shown = [_display_path(item, cwd) for item in files[:8]]
        suffix = f", (+{len(files) - 8} more)" if len(files) > 8 else ""
        parts.append(f"changed {len(files)} files: {', '.join(shown)}{suffix}.")
    if forbidden:
        item = forbidden[0]
        if isinstance(item, dict):
            parts.append(f"Ran forbidden command [{_clean_text(item.get('rule'), 40)}]: {_clean_text(item.get('command'), 160)}.")
    if api_error:
        message = _clean_text(row.get("apiMessage", ""), 100)
        parts.append(f"API Error {api_error}{': ' + message if message else ''}.")
    elif row.get("isApiError"):
        message = _clean_text(row.get("apiMessage", ""), 100)
        parts.append(f"API error{': ' + message if message else ''}.")
    return _clean_text(" ".join(parts), MAX_CONTEXT_LINE)


def _outage_line(lane: str, row: dict, alternative: str | None) -> str:
    at = _parse_time(row.get("at"))
    until = _parse_time(row.get("until"))
    at_text = at.strftime("%H:%M UTC") if at else "unknown time"
    until_text = until.strftime("%H:%M UTC") if until else "unknown time"
    model = _clean_text(row.get("model", "unknown model"), 100) or "unknown model"
    status = _as_int(row.get("status"))
    if alternative:
        dispatch = f"Until {until_text}, dispatch fleet-{alternative} instead, once; if that fails too, do the work yourself."
    else:
        dispatch = f"Until {until_text}, do the work yourself."
    return _clean_text(f"AgentFleet outage: fleet-{lane} ({model}) failed with {status} at {at_text}. {dispatch}", MAX_CONTEXT_LINE)


def _failover_line(lane: str, model: str, row: dict, switch_to: str) -> str:
    at = _parse_time(row.get("at"))
    until = _parse_time(row.get("until"))
    at_text = at.strftime("%H:%M UTC") if at else "unknown time"
    until_text = until.strftime("%H:%M UTC") if until else "unknown time"
    status = _as_int(row.get("status"))
    return _clean_text(
        f"AgentFleet outage: fleet-{lane}'s model {model} failed with {status} at {at_text}. "
        f"Until {until_text}, fleet-{lane} runs on {switch_to} instead; re-dispatch the failed task to fleet-{lane} once. "
        f"If it fails again, do the work yourself.",
        MAX_CONTEXT_LINE,
    )


def _gateway_line(gateway: dict) -> str:
    since = _parse_time(gateway.get("at"))
    until = _parse_time(gateway.get("until"))
    since_text = since.strftime("%H:%M UTC") if since else "unknown time"
    until_text = until.strftime("%H:%M UTC") if until else "unknown time"
    names = [_clean_text(model, 100) for model in gateway.get("models", [])[:4]]
    listed = ", ".join(name for name in names if name) or "unknown models"
    return _clean_text(
        f"AgentFleet outage: the gateway is failing for several models ({listed}) since {since_text}. "
        f"Until {until_text}, do the work yourself instead of dispatching fleet lanes, and tell the user.",
        MAX_CONTEXT_LINE,
    )


def _bounded_context(lines: list[str]) -> list[str]:
    result: list[str] = []
    used = 0
    for line in lines:
        line = _clean_text(line, MAX_CONTEXT_LINE)
        if not line:
            continue
        extra = len(line) + (1 if result else 0)
        if used + extra > MAX_CONTEXT_TOTAL:
            remaining = MAX_CONTEXT_TOTAL - used - (1 if result else 0)
            if remaining > 0:
                result.append(line[:remaining])
            break
        result.append(line)
        used += extra
    return result


def _forbidden_item(command: str) -> dict | None:
    reason = forbidden_reason(command)
    if not reason:
        return None
    return {"rule": reason, "command": _clean_text(command, 160)}


def project_dir(payload: dict) -> str:
    """The folder a hook's session belongs to.

    The payload's cwd follows the shell's `cd`; CLAUDE_PROJECT_DIR stays where
    Claude Code started, so a session's usage and suggestions keep one project.
    """
    started = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if started and os.path.isabs(started):
        return started
    cwd = payload.get("cwd")
    return cwd if isinstance(cwd, str) else ""


def _usage_key_path(cwd_string: str) -> str:
    try:
        return str(Path(cwd_string).expanduser().resolve())
    except (OSError, RuntimeError, ValueError):
        return cwd_string


# Claude Code logs a slash command as <command-name>/compact</command-name>.
COMMAND_TAG = re.compile(r"<command-name>\s*(/[^<\s]{1,39})\s*</command-name>")


def _usage_path(home: Path, cwd_string: str) -> Path:
    # The resolved path, so a symlinked checkout shares fleet_suggest's key.
    key = hashlib.sha256(_usage_key_path(cwd_string).encode("utf-8", errors="replace")).hexdigest()[:16]
    return _store(home) / "usage" / f"{key}.json"


def _usage_load(path: Path, project: str) -> dict:
    raw = _load_json(path, {})
    if not isinstance(raw, dict) or raw.get("format") != USAGE_FORMAT:
        raw = {}
    result = {
        "format": USAGE_FORMAT,
        "project": project,
        "updated": _iso(),
    }
    for category in ("bash", "tools", "domains", "commands"):
        values = raw.get(category, {}) if isinstance(raw, dict) else {}
        result[category] = values if isinstance(values, dict) else {}
    return result


def _usage_increment(data: dict, category: str, value: str, session_id: str, now: str) -> None:
    value = _clean_text(value, 60 if category == "bash" else 253 if category == "domains" else 40)
    if not value:
        return
    entries = data[category]
    row = entries.get(value)
    if not isinstance(row, dict):
        row = {"count": 0, "sessions": 0, "last": now, "lastSession": ""}
    row["count"] = max(0, _as_int(row.get("count"))) + 1
    if row.get("lastSession") != session_id:
        row["sessions"] = max(0, _as_int(row.get("sessions"))) + 1
    row["lastSession"] = session_id
    row["last"] = now
    entries[value] = row


def _usage_record(data: dict, record: dict, session_id: str, now: str) -> None:
    kind = record.get("type")
    message = record.get("message")
    if not isinstance(message, dict):
        return
    if kind == "assistant":
        content = message.get("content")
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block.get("name")
            if isinstance(name, str):
                _usage_increment(data, "tools", name, session_id, now)
            inputs = block.get("input") if isinstance(block.get("input"), dict) else {}
            if name == "Bash" and isinstance(inputs.get("command"), str):
                for segment in _all_segments(inputs["command"]):
                    head = _head_for_segment(segment)
                    if head:
                        _usage_increment(data, "bash", head, session_id, now)
            elif name == "WebFetch" and isinstance(inputs.get("url"), str):
                try:
                    hostname = urlsplit(inputs["url"]).hostname
                    if hostname:
                        _usage_increment(data, "domains", hostname.lower(), session_id, now)
                except ValueError:
                    pass
    elif kind == "user":
        content = message.get("content")
        if isinstance(content, list):
            content = " ".join(block.get("text", "") for block in content
                               if isinstance(block, dict) and isinstance(block.get("text"), str))
        if isinstance(content, str):
            text = content.strip()
            tagged = COMMAND_TAG.search(text)
            if tagged:
                _usage_increment(data, "commands", tagged.group(1)[:40], session_id, now)
            elif text.startswith("/"):
                name = text.split(None, 1)[0][:40]
                _usage_increment(data, "commands", name, session_id, now)


def _prune_usage(data: dict, now: datetime) -> None:
    cutoff = now - USAGE_RETENTION
    for category in ("bash", "tools", "domains", "commands"):
        entries = data.get(category, {})
        if not isinstance(entries, dict):
            data[category] = {}
            continue
        kept = {}
        for key, value in entries.items():
            last = _parse_time(value.get("last")) if isinstance(value, dict) else None
            if last and last >= cutoff:
                kept[key] = value
        data[category] = dict(sorted(kept.items(), key=lambda item: (_parse_time(item[1].get("last")) or datetime.min.replace(tzinfo=timezone.utc), item[0]), reverse=True)[:200])


def _learn_main(home: Path, session: dict, payload: dict, session_id: str, cwd_string: str,
                budget: list[int], deadline: float) -> None:
    if load_preferences is None:
        return
    try:
        preferences = load_preferences(home)
    except Exception:
        return
    if not isinstance(preferences, dict) or preferences.get("learn") is not True:
        return
    transcript = payload.get("transcript_path")
    if not isinstance(transcript, str) or not transcript:
        return
    try:
        path = Path(transcript).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return
    main_state = session.get("main")
    if not isinstance(main_state, dict) or main_state.get("path") != str(path):
        main_state = {"path": str(path), "offset": 0}
    data = _usage_load(_usage_path(home, cwd_string), _usage_key_path(cwd_string))
    now_dt = _utcnow()
    now = _iso(now_dt)

    def consume(record: dict) -> None:
        _usage_record(data, record, session_id, now)

    offset, _ = _iter_jsonl(path, max(0, _as_int(main_state.get("offset"))), budget, deadline, consume)
    main_state["offset"] = offset
    session["main"] = main_state
    data["updated"] = now
    _prune_usage(data, now_dt)
    usage_path = _usage_path(home, cwd_string)
    _atomic_write(usage_path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def _main_usage_empty() -> dict:
    return {"format": MAIN_USAGE_FORMAT, "days": {}, "transcripts": {}}


def _main_usage_bucket(days: dict, day: str, model: str) -> dict:
    models = days.get(day)
    if not isinstance(models, dict):
        models = {}
        days[day] = models
    bucket = models.get(model)
    if not isinstance(bucket, dict):
        bucket = {key: 0 for key in ("inputTokens", "outputTokens", "cacheReadTokens", "cacheWriteTokens", "messages")}
        models[model] = bucket
    return bucket


def _scan_main_usage(home: Path, transcript: Path, budget: list[int], deadline: float) -> None:
    """Incrementally account main-session assistant tokens without affecting prompts."""
    path = _store(home) / "main-usage.json"
    try:
        resolved = transcript.resolve(strict=False)
        transcript_key = hashlib.sha256(str(resolved).encode("utf-8", errors="replace")).hexdigest()[:16]
        try:
            original = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            original = _main_usage_empty()
        except (OSError, UnicodeError, json.JSONDecodeError):
            return
        if not isinstance(original, dict):
            original = _main_usage_empty()
        elif original.get("format") != MAIN_USAGE_FORMAT:
            if original.get("format") in {None, ""}:
                original = _main_usage_empty()
            else:
                return
        days = original.get("days")
        transcripts = original.get("transcripts")
        if not isinstance(days, dict) or not isinstance(transcripts, dict):
            return
        if not all(isinstance(day, str) and isinstance(models, dict) for day, models in days.items()):
            return
        if not all(isinstance(key, str) and isinstance(value, dict) for key, value in transcripts.items()):
            return
        before = json.dumps(original, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        original_transcript = transcripts.get(transcript_key)
        existing = original_transcript if isinstance(original_transcript, dict) else {}
        counted_raw = existing.get("counted") if isinstance(existing.get("counted"), dict) else {}
        token_fields = ("inputTokens", "outputTokens", "cacheReadTokens", "cacheWriteTokens")
        entry = {
            "offset": max(0, _as_int(existing.get("offset"))),
            "updated": existing.get("updated") if isinstance(existing.get("updated"), str) else "",
            "id": existing.get("id") if isinstance(existing.get("id"), str) else "",
            "counted": {key: max(0, _as_int(counted_raw.get(key))) for key in token_fields},
            "day": existing.get("day") if isinstance(existing.get("day"), str) else "",
            "model": existing.get("model") if isinstance(existing.get("model"), str) else "",
        }
        try:
            size = resolved.stat().st_size
        except OSError:
            size = -1
        offset = entry["offset"]
        if size >= offset >= 0:
            def consume(record: dict) -> None:
                if record.get("type") != "assistant" or record.get("isSidechain") is True:
                    return
                message = record.get("message")
                if not isinstance(message, dict):
                    return
                model = message.get("model")
                usage = message.get("usage")
                message_id = message.get("id")
                if (not isinstance(model, str) or not model or model == "<synthetic>"
                        or not isinstance(usage, dict) or not isinstance(message_id, str) or not message_id):
                    return
                message_id = _clean_text(message_id, 200)
                model = _clean_text(model, 100)
                same_message = message_id == entry["id"] and bool(entry["day"]) and bool(entry["model"])
                if same_message:
                    day = entry["day"]
                    model = entry["model"]
                    counted = entry["counted"]
                    bucket = _main_usage_bucket(days, day, model)
                    for field, source in zip(token_fields, (
                        "input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"
                    )):
                        value = max(0, _as_int(usage.get(source)))
                        previous = max(0, _as_int(counted.get(field)))
                        bucket[field] = max(0, _as_int(bucket.get(field))) + max(0, value - previous)
                        counted[field] = max(previous, value)
                    return
                timestamp = _parse_time(record.get("timestamp"))
                if timestamp is None:
                    return
                day = timestamp.date().isoformat()
                values = {
                    field: max(0, _as_int(usage.get(source)))
                    for field, source in zip(token_fields, (
                        "input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"
                    ))
                }
                bucket = _main_usage_bucket(days, day, model)
                for field, value in values.items():
                    bucket[field] = max(0, _as_int(bucket.get(field))) + value
                bucket["messages"] = max(0, _as_int(bucket.get("messages"))) + 1
                entry["id"] = message_id
                entry["counted"] = values
                entry["day"] = day
                entry["model"] = model

            new_offset, _ = _iter_jsonl(
                resolved, offset, budget, deadline, consume, contains=b'assistant', strict=True
            )
            if new_offset > offset:
                entry["offset"] = new_offset
                entry["updated"] = _iso()
                transcripts[transcript_key] = entry
        now = _utcnow()
        cutoff_day = (now.date() - timedelta(days=35)).isoformat()
        for day in list(days):
            if not isinstance(day, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) or day < cutoff_day:
                days.pop(day, None)
        cutoff = now - timedelta(days=35)
        for key, value in list(transcripts.items()):
            updated = _parse_time(value.get("updated")) if isinstance(value, dict) else None
            if updated is None or updated < cutoff:
                transcripts.pop(key, None)
        if len(transcripts) > 500:
            oldest = sorted(
                transcripts,
                key=lambda key: _parse_time(transcripts[key].get("updated"))
                if isinstance(transcripts.get(key), dict) else None,
            )
            for key in oldest[:len(transcripts) - 500]:
                transcripts.pop(key, None)
        after = json.dumps(original, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if after != before:
            _atomic_write(path, (json.dumps(original, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    except Exception:
        # Accounting is best-effort; a failed scan never damages the previous store or a prompt.
        return


def main_usage(home: Path, since: datetime) -> dict:
    """Sum stored main-session usage on or after ``since``'s UTC date."""
    try:
        if not isinstance(since, datetime):
            return {}
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        since_day = since.astimezone(timezone.utc).date().isoformat()
        value = _load_json(_store(home) / "main-usage.json", {})
        if not isinstance(value, dict) or value.get("format") != MAIN_USAGE_FORMAT:
            return {}
        days = value.get("days")
        if not isinstance(days, dict):
            return {}
        totals: dict[str, dict] = {}
        fields = ("inputTokens", "outputTokens", "cacheReadTokens", "cacheWriteTokens", "messages")
        for day, models in days.items():
            if not isinstance(day, str) or day < since_day or not isinstance(models, dict):
                continue
            for model, bucket in models.items():
                if not isinstance(model, str) or not model or not isinstance(bucket, dict):
                    continue
                total = totals.setdefault(model, {field: 0 for field in fields})
                for field in fields:
                    total[field] += max(0, _as_int(bucket.get(field)))
        return totals
    except Exception:
        return {}


def _system_envelope(payload: dict) -> bool:
    prompt = payload.get("prompt")
    return isinstance(prompt, str) and prompt.startswith("<")


def _scan_session(payload: dict, home: Path, state: dict, session_id: str, cwd: object) -> list[str]:
    transcript_value = payload.get("transcript_path")
    if not isinstance(transcript_value, str) or not transcript_value:
        return []
    try:
        transcript = Path(transcript_value).expanduser().resolve(strict=False)
        subagents = transcript.with_suffix("") / "subagents"
        files = sorted(subagents.glob("agent-*.jsonl"))
    except (OSError, RuntimeError, ValueError):
        return []
    sessions = state["sessions"]
    session = sessions.get(session_id)
    if not isinstance(session, dict):
        session = {"updated": _iso(), "agents": {}, "main": {"path": str(transcript), "offset": 0}}
        sessions[session_id] = session
    agents = session.get("agents")
    if not isinstance(agents, dict):
        agents = {}
        session["agents"] = agents
    budget = [0]
    deadline = time.monotonic() + SCAN_MAX_SECONDS
    lines: list[str] = []
    stored_outages = current_outages(home)
    old_outages = stored_outages.get("lanes", {})
    outage_data = dict(old_outages)
    old_models = stored_outages.get("models", {})
    model_data = dict(old_models)
    lane_failures: dict[str, dict] = {}
    lane_rows = _lane_rows(home, cwd)
    for file_path in files:
        if time.monotonic() >= deadline or budget[0] >= SCAN_MAX_BYTES:
            break
        meta_path = file_path.with_name(file_path.name.removesuffix(".jsonl") + ".meta.json")
        meta = _load_meta(meta_path)
        if not meta:
            continue
        lane, description, failover_model, explicit_model = meta
        agent_id = file_path.name[len("agent-"):-len(".jsonl")]
        agent_id = _clean_text(agent_id, 80)
        if not agent_id:
            continue
        agent_path = str(file_path.resolve(strict=False))
        agent = agents.get(agent_path)
        is_new = not isinstance(agent, dict)
        if is_new:
            agent = _new_agent_state(agent_id, lane, description)
            agents[agent_path] = agent
        else:
            agent["lane"] = lane
            agent["description"] = description
        agent["failoverModel"] = failover_model
        agent["explicitModel"] = explicit_model
        try:
            current_size = file_path.stat().st_size
        except OSError:
            continue
        prior_size = _as_int(agent.get("size"))
        offset = max(0, _as_int(agent.get("offset")))
        if current_size < offset:
            offset = 0
            agent.update(_new_agent_state(agent_id, lane, description))
            agent["failoverModel"] = failover_model
            agent["explicitModel"] = explicit_model
        try:
            idle_seconds = time.time() - file_path.stat().st_mtime
        except OSError:
            idle_seconds = 0.0
        if is_new and idle_seconds > HISTORY_SECONDS:
            # Finished long before this watcher first saw it (the first prompt
            # after an update, or a session resumed after its state expired):
            # record it as handled without reports, notices, or outages.
            agent["offset"] = current_size
            agent["size"] = current_size
            agent["done"] = agent["reported"] = True
            continue
        new_offset, _ = _iter_jsonl(file_path, offset, budget, deadline,
                                   lambda row, target=agent: _agent_record(target, row, cwd))
        agent["offset"] = new_offset
        agent["size"] = current_size
        consumed = new_offset >= current_size
        finished = agent.get("lastStopReason") in TERMINAL_STOP_REASONS or bool(agent.get("isApiError"))
        # Done only once the whole file is read and the lane's last message
        # ended its turn (or failed). Records before the last block of a
        # message carry no stop reason, so a pause mid-message is not an end.
        # A lane that stopped without a final message (killed, turn limit)
        # is done once its transcript has been idle for a long time.
        if consumed and agent.get("lastAssistantSeen") and (finished or idle_seconds > IDLE_DONE_SECONDS):
            agent["done"] = True
        if agent.get("done") and not agent.get("reported"):
            # The model the lane was configured to run on: the one lane-guard
            # switched it to, else the lane's own. The API's name for it
            # (row["model"]) can differ from fleet.json's. A model the caller
            # chose itself is not the lane's, so none is recorded then.
            lane_row = lane_rows.get(_clean_lane(agent.get("lane")))
            configured = "" if agent.get("explicitModel") else (
                _model_key(agent.get("failoverModel"))
                or (_model_id(lane_row.get("model")) if isinstance(lane_row, dict) else ""))
            row = _report_payload(session_id, agent, home, configured)
            if _append_report(home, row):
                agent["reported"] = True
                lane_name = row["lane"]
                error = row["apiError"]
                ended = _parse_time(agent.get("lastAt")) or _utcnow()
                model = (_model_key(agent.get("failoverModel"))
                         or (_model_id(lane_row.get("model")) if isinstance(lane_row, dict) else "")
                         or row["model"])
                if agent.get("explicitModel"):
                    # The caller picked the model, so neither a failure nor a
                    # success tells us anything about the lane's own models.
                    pass
                elif isinstance(error, int) and _is_outage_status(error):
                    until = ended + _outage_window(error)
                    entry = {
                        "model": model,
                        "status": error,
                        "message": _clean_text(agent.get("apiMessage", ""), 160),
                        "at": _iso(ended),
                        "until": _iso(until),
                    }
                    if until > _utcnow():
                        known = _parse_time((model_data.get(model) or {}).get("at")) if model else None
                        if model and (known is None or ended >= known):
                            model_data[model] = {**{k: v for k, v in entry.items() if k != "model"}, "lane": lane_name}
                        prior = _parse_time((lane_failures.get(lane_name) or {}).get("at"))
                        if prior is None or ended >= prior:
                            lane_failures[lane_name] = entry
                elif not agent.get("isApiError"):
                    # Only a run that ended after the failure proves the lane
                    # (and the model it ran on) works again.
                    failed_at = _parse_time((outage_data.get(lane_name) or {}).get("at"))
                    if failed_at is not None and ended > failed_at:
                        outage_data.pop(lane_name, None)
                    pending_at = _parse_time((lane_failures.get(lane_name) or {}).get("at"))
                    if pending_at is not None and ended > pending_at:
                        lane_failures.pop(lane_name, None)
                    model_failed_at = _parse_time((model_data.get(model) or {}).get("at")) if model else None
                    if model_failed_at is not None and ended > model_failed_at:
                        model_data.pop(model, None)
                lane_notice = _lane_line({**row, "apiMessage": agent.get("apiMessage", ""), "isApiError": agent.get("isApiError", False)}, cwd)
                if lane_notice:
                    lines.append(lane_notice)
    # A failed lane keeps working when lane-guard can switch it to another of
    # its models; only a lane with none left is an outage that sends work to
    # another lane.
    # Several vendors failing at once is a gateway outage: one line replaces
    # this scan's per-lane lines, and dispatching lanes stops until it clears.
    gateway = stored_outages.get("gateway")
    gateway_started = False
    failing = _gateway_outage(model_data, _utcnow())
    if failing and gateway is None:
        gateway = failing
        gateway_started = True
    elif failing:
        # Already announced: extend it rather than repeat the notice.
        merged = list(gateway.get("models", []))
        merged += [model for model in failing["models"] if model not in merged]
        ends = [_parse_time(gateway.get("until")), _parse_time(failing["until"])]
        gateway = {**gateway, "until": _iso(max(end for end in ends if end is not None)), "models": merged}
    line_mark = len(lines)
    failover = failover_enabled(home) if lane_failures else False
    for lane_name, entry in lane_failures.items():
        model = entry["model"]
        switch_to = effective_model(lane_rows.get(lane_name), model_data) if failover else None
        if switch_to and model in model_data:
            model_data[model]["switchedTo"] = switch_to
            previous = old_models.get(model)
            if not isinstance(previous, dict) or previous.get("at") != entry["at"]:
                lines.append(_failover_line(lane_name, model, entry, switch_to))
            continue
        known = _parse_time((outage_data.get(lane_name) or {}).get("at"))
        ended = _parse_time(entry.get("at"))
        if known is None or (ended is not None and ended >= known):
            outage_data[lane_name] = entry
    # Alternatives are chosen once every failure of this scan is known, so two
    # lanes failing together never recommend each other; the choice is stored
    # for the fleet-status mod to show.
    for lane_name, outage in outage_data.items():
        if lane_name not in old_outages or "alternative" not in outage:
            outage["alternative"] = alternative_lane(home, lane_name, outage_data, model_data)
            if lane_name not in old_outages:
                lines.append(_outage_line(lane_name, outage, outage["alternative"]))
    if gateway_started:
        del lines[line_mark:]
        lines.append(_gateway_line(gateway))
    live = current_outages(home)
    if outage_data != live.get("lanes", {}) or model_data != live.get("models", {}) or gateway != live.get("gateway"):
        _write_outages(home, outage_data, model_data, gateway)

    _learn_main(home, session, payload, session_id, project_dir(payload), budget, deadline)
    # After the lanes, with its own byte budget: catching up a long session's
    # history must not delay lane reports and outage notices.
    _scan_main_usage(home, transcript, [0], deadline)
    return _bounded_context(lines)


def _start_usage_refresh(home: Path, session_id: str) -> None:
    """Ask the gateway for its usage figures in the background when they are
    due (provider_usage.refresh_due); the prompt never waits on the network."""
    if provider_usage is None:
        return
    try:
        # Drop another gateway's or key's figures and prices (or any, on the
        # Claude subscription profile) so the mods never show them as current.
        provider_usage.discard_foreign(home)
        if provider_usage.gateway(home) is None:
            return
        if provider_usage.refresh_due(home, session_id or None):
            provider_usage.spawn_refresh(home, session_id or None)
    except Exception:
        pass


PUBLISHED_PRICES_FORMAT = "agentfleet.published-prices.v1"


def _publish_prices(home: Path) -> None:
    """Copy the prices the current gateway publishes to a small file the brain
    mod reads, so /brain estimates with the same prices as `agentfleet report`.
    The file changes only when the prices do; a profile without published
    prices empties it. It names the gateway and key hash it belongs to, so
    provider_usage.discard_foreign can remove it after a profile switch."""
    if catalog_prices is None or provider_usage is None:
        return
    try:
        current = provider_usage.scope(home)
        if current is None:
            return
        path = _store(home) / provider_usage.PUBLISHED_PRICES_NAME
        payload = {"format": PUBLISHED_PRICES_FORMAT, "endpoint": current[0], "account": current[1],
                   "prices": catalog_prices(home)}
        data = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
        try:
            if path.read_bytes() == data:
                return
        except OSError:
            if not payload["prices"]:
                return
        _atomic_write(path, data)
    except Exception:
        pass


def on_prompt(payload: dict, home: Path) -> list[str]:
    """Update bounded local state and return context notices; never break a prompt."""
    try:
        if not isinstance(payload, dict):
            return []
        claude_dir = home / ".claude"
        store = _store(home)
        _ensure_private_dir(claude_dir, tighten_existing=True)
        _ensure_private_dir(store, tighten_existing=True)
        # A hook collision is a no-op rather than a wait; the next prompt resumes offsets.
        lock = store / ".watch.lock"
        try:
            lock_fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > 10:
                    lock.unlink()
                    lock_fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                else:
                    return []
            except OSError:
                return []
        try:
            try:
                os.fchmod(lock_fd, 0o600)
            except (AttributeError, OSError):
                pass
            state_path = store / "watch-state.json"
            state = _load_state(state_path)
            sessions = state["sessions"]
            now = _utcnow()
            for key, value in list(sessions.items()):
                updated = _parse_time(value.get("updated")) if isinstance(value, dict) else None
                if not updated or now - updated > STATE_RETENTION:
                    sessions.pop(key, None)
            session_id = payload.get("session_id")
            session_id = _clean_text(session_id, 200) if isinstance(session_id, str) else ""
            lines: list[str] = []
            if session_id:
                lines = _scan_session(payload, home, state, session_id, payload.get("cwd"))
                session = sessions.get(session_id)
                if isinstance(session, dict):
                    session["updated"] = _iso(now)
            _atomic_write(state_path, (json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
            _start_usage_refresh(home, session_id)
            _publish_prices(home)
            return _bounded_context(lines)
        finally:
            os.close(lock_fd)
            try:
                lock.unlink()
            except OSError:
                pass
    except Exception:
        return []


_OUTCOME_NAMES = ("done", "partial", "blocked", "failed", "cutOff", "apiError", "none")
_TOKEN_FIELDS = ("inputTokens", "outputTokens", "cacheReadTokens", "cacheWriteTokens")


def _new_report_stats() -> dict:
    return {
        "runs": 0,
        "failures": 0,
        "inputTokens": 0,
        "outputTokens": 0,
        "cacheReadTokens": 0,
        "cacheWriteTokens": 0,
        "outcomes": {name: 0 for name in _OUTCOME_NAMES},
        "toolCalls": 0,
        "durationSeconds": 0,
        "_toolCallSamples": [],
        "_durationSamples": [],
    }


def _record_outcome(row: dict) -> str:
    if row.get("apiError") is not None:
        return "apiError"
    outcome = row.get("outcome")
    if isinstance(outcome, str) and outcome in {"done", "partial", "blocked", "failed"}:
        return outcome
    if row.get("cutOff") is True:
        return "cutOff"
    return "none"


def _add_report_row(stats: dict, row: dict) -> None:
    stats["runs"] += 1
    if row.get("apiError") is not None:
        stats["failures"] += 1
    stats["outcomes"][_record_outcome(row)] += 1
    for field in _TOKEN_FIELDS:
        stats[field] += max(0, _as_int(row.get(field)))
    tool_calls = max(0, _as_int(row.get("toolCalls")))
    duration = max(0, _as_int(row.get("durationSeconds")))
    stats["toolCalls"] += tool_calls
    stats["durationSeconds"] += duration
    if "toolCalls" in row and row.get("toolCalls") is not None:
        stats["_toolCallSamples"].append(tool_calls)
    if "durationSeconds" in row and row.get("durationSeconds") is not None:
        stats["_durationSamples"].append(duration)


def _finish_report_stats(stats: dict) -> dict:
    tool_samples = stats.pop("_toolCallSamples", [])
    duration_samples = stats.pop("_durationSamples", [])
    stats["avgToolCalls"] = round(sum(tool_samples) / len(tool_samples), 1) if tool_samples else None
    stats["avgMinutes"] = round(sum(duration_samples) / len(duration_samples) / 60, 1) if duration_samples else None
    return stats


def _estimate_cost(tokens: dict, price: dict) -> float:
    """Match mods/brain/hooks/brain.ts estimateCost's dollars-per-million formula."""
    return (
        max(0, _as_int(tokens.get("inputTokens"))) * float(price.get("input", 0))
        + max(0, _as_int(tokens.get("outputTokens"))) * float(price.get("output", 0))
        + max(0, _as_int(tokens.get("cacheReadTokens"))) * float(price.get("cacheRead", 0))
        + max(0, _as_int(tokens.get("cacheWriteTokens"))) * float(price.get("cacheWrite", 0))
    ) / 1_000_000


def report(home: Path, days: int = 7) -> dict:
    """Aggregate recent lane and main-session usage, estimates, and outages."""
    day_count = max(0, _as_int(days))
    now = _utcnow()
    cutoff = now - timedelta(days=day_count)
    try:
        preferences = load_preferences(home) if load_preferences is not None else {}
        if effective_prices is not None:
            prices = effective_prices(home, preferences)
        else:
            prices = preferences.get("prices", {}) if isinstance(preferences, dict) else {}
        prices = prices if isinstance(prices, dict) else {}
    except Exception:
        preferences = {}
        prices = {}
    has_prices = bool(prices)

    lanes: dict[str, dict] = {}
    models: dict[str, dict] = {}
    lane_costs: dict[str, float] = {}
    model_costs: dict[str, float] = {}
    unpriced: set[str] = set()
    cache_not_priced: set[str] = set()
    all_rows = _new_report_stats()

    def add_cost(target: dict, costs: dict, names: list, row: dict) -> None:
        """Price ``row`` at the first of ``names`` that has a price: the
        model the lane was configured with, then the one the gateway
        recorded. A miss is listed under the first name known."""
        if not has_prices:
            return
        known = [name for name in names if name]
        price = None
        for candidate in known:
            price = price_for(prices, candidate) if price_for is not None else None
            if price is not None:
                break
        name = known[0] if known else "unknown"
        tokens = {field: max(0, _as_int(row.get(field))) for field in _TOKEN_FIELDS}
        if price is None:
            if any(tokens.values()):
                unpriced.add(name)
            costs[target["_costKey"]] = costs.get(target["_costKey"], 0.0)
            return
        costs[target["_costKey"]] = costs.get(target["_costKey"], 0.0) + _estimate_cost(tokens, price)
        if ((tokens["cacheReadTokens"] and "cacheRead" not in price)
                or (tokens["cacheWriteTokens"] and "cacheWrite" not in price)):
            cache_not_priced.add(name)

    for path in (_store(home) / "lane-reports.jsonl", _store(home) / "lane-reports.jsonl.1"):
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(row, dict):
                        continue
                    at = _parse_time(row.get("at"))
                    lane = _clean_lane(row.get("lane"))
                    if not at or at < cutoff or not lane:
                        continue

                    lane_stats = lanes.setdefault(lane, {
                        **_new_report_stats(), "forbidden": 0, "filesChanged": 0, "models": [],
                    })
                    model_name = _clean_text(row.get("model", ""), 100) or "unknown"
                    model_stats = models.setdefault(model_name, {
                        **_new_report_stats(), "lanes": [],
                    })
                    _add_report_row(lane_stats, row)
                    _add_report_row(model_stats, row)
                    _add_report_row(all_rows, row)
                    lane_stats["forbidden"] += len(row.get("forbidden", [])) if isinstance(row.get("forbidden"), list) else 0
                    lane_stats["filesChanged"] += len(row.get("files", [])) if isinstance(row.get("files"), list) else 0
                    if lane not in model_stats["lanes"]:
                        model_stats["lanes"].append(lane)
                    if model_name != "unknown" and model_name not in lane_stats["models"]:
                        lane_stats["models"].append(model_name)
                    if has_prices:
                        lane_stats.setdefault("_costKey", lane)
                        model_stats.setdefault("_costKey", model_name)
                        configured = _clean_text(row.get("configuredModel", ""), 100)
                        add_cost(lane_stats, lane_costs, [configured, model_name], row)
                        add_cost(model_stats, model_costs, [configured, model_name], row)
        except OSError:
            continue

    for stats in lanes.values():
        _finish_report_stats(stats)
        stats["estimatedCost"] = round(lane_costs.get(stats.get("_costKey"), 0.0), 4) if has_prices else None
        stats.pop("_costKey", None)
    for name, stats in models.items():
        _finish_report_stats(stats)
        stats["lanes"].sort()
        stats["estimatedCost"] = round(model_costs.get(name, 0.0), 4) if has_prices else None
        stats.pop("_costKey", None)

    totals = _finish_report_stats(all_rows)
    totals.update({
        "forbidden": sum(stats["forbidden"] for stats in lanes.values()),
        "filesChanged": sum(stats["filesChanged"] for stats in lanes.values()),
        "estimatedCost": round(sum(lane_costs.values()), 4) if has_prices else None,
    })

    main: dict[str, dict] = {}
    main_cost = 0.0
    for raw_model, usage in main_usage(home, now - timedelta(days=day_count)).items():
        model_name = _clean_text(raw_model, 100) or "unknown"
        bucket = main.setdefault(model_name, {field: 0 for field in (*_TOKEN_FIELDS, "messages")})
        for field in (*_TOKEN_FIELDS, "messages"):
            bucket[field] += max(0, _as_int(usage.get(field))) if isinstance(usage, dict) else 0
    for name, usage in main.items():
        if has_prices:
            price = price_for(prices, name) if price_for is not None else None
            if price is None:
                if any(usage[field] for field in _TOKEN_FIELDS):
                    unpriced.add(name)
                usage["estimatedCost"] = 0.0
            else:
                estimated = _estimate_cost(usage, price)
                usage["estimatedCost"] = round(estimated, 4)
                main_cost += estimated
                if ((usage["cacheReadTokens"] and "cacheRead" not in price)
                        or (usage["cacheWriteTokens"] and "cacheWrite" not in price)):
                    cache_not_priced.add(name)
        else:
            usage["estimatedCost"] = None

    # Sum lane-level model costs once, preserving unrounded values until the final total.
    lane_total_cost = sum(lane_costs.values()) if has_prices else 0.0
    cost = None
    if has_prices:
        cost = {
            "estimate": True,
            "total": round(lane_total_cost + main_cost, 4),
            "lanes": round(lane_total_cost, 4),
            "main": round(main_cost, 4),
            "unpriced": sorted(unpriced),
            "cacheNotPriced": sorted(cache_not_priced),
        }

    return {
        "days": day_count,
        "lanes": lanes,
        "models": models,
        "main": main,
        "totals": totals,
        "outages": current_outages(home),
        "cost": cost,
        "gateway": _gateway_usage(home, preferences),
    }


def _gateway_usage(home: Path, preferences: dict) -> dict | None:
    """What the current gateway itself reported about the key's spend and
    limits (provider_usage.py), or None when it reports nothing, the setting
    is off, or the saved figures belong to another gateway or key."""
    if provider_usage is None or not isinstance(preferences, dict) or preferences.get("providerUsage") is False:
        return None
    try:
        stored = provider_usage.load(home)
    except Exception:
        return None
    if not isinstance(stored, dict) or not stored.get("reader") or not isinstance(stored.get("usage"), dict):
        return None
    # `agentfleet report` runs outside any session, so it shows the session
    # the prompt hook refreshed most recently.
    sessions = stored.get("sessions") if isinstance(stored.get("sessions"), dict) else {}
    rows = [row for row in sessions.values() if isinstance(row, dict) and _parse_time(row.get("fetchedAt"))]
    latest = max(rows, key=lambda row: _parse_time(row["fetchedAt"])) if rows else None
    return {"reader": stored["reader"], "fetchedAt": stored.get("fetchedAt"), **stored["usage"],
            "latestSession": latest}


def _compact_number(value: object) -> str:
    """Format counts and averages compactly without unstable locale rules."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "-"
    number = float(value)
    if not math.isfinite(number):
        return "-"
    absolute = abs(number)
    if absolute >= 1_000_000_000:
        scaled, suffix = number / 1_000_000_000, "B"
    elif absolute >= 1_000_000:
        scaled, suffix = number / 1_000_000, "M"
    elif absolute >= 1_000:
        scaled, suffix = number / 1_000, "k"
    else:
        if number.is_integer():
            return str(int(number))
        return f"{number:.1f}".rstrip("0").rstrip(".")
    return f"{scaled:.1f}".rstrip("0").rstrip(".") + suffix


def _money(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "-"
    number = float(value)
    if not math.isfinite(number):
        return "-"
    return f"${number:.2f}"


def _stats_table_row(name: str, stats: dict, label: str, with_cost: bool) -> str:
    outcomes = stats.get("outcomes", {}) if isinstance(stats.get("outcomes"), dict) else {}
    shown_name = name if len(name) <= 20 else name[:19] + "…"
    columns = [
        f"{shown_name:<20}",
        f"{_compact_number(stats.get('runs', 0)):>4}",
        *[f"{_compact_number(outcomes.get(key, 0)):>4}" for key in _OUTCOME_NAMES[:4]],
        f"{_compact_number(outcomes.get('cutOff', 0)):>4}",
        f"{_compact_number(outcomes.get('apiError', 0)):>4}",
        f"{_compact_number(outcomes.get('none', 0)):>4}",
        f"{_compact_number(stats.get('avgToolCalls')):>6}",
        f"{_compact_number(stats.get('avgMinutes')):>6}",
        f"{_compact_number(stats.get('inputTokens', 0)):>7}",
        f"{_compact_number(stats.get('outputTokens', 0)):>7}",
        f"{_compact_number(_as_int(stats.get('cacheReadTokens')) + _as_int(stats.get('cacheWriteTokens'))):>7}",
    ]
    if with_cost:
        columns.append(f"{_money(stats.get('estimatedCost')):>8}")
    return " ".join(columns)


def _stats_table_header(label: str, with_cost: bool) -> str:
    columns = [
        f"{label:<20}", f"{'Runs':>4}", f"{'Done':>4}", f"{'Part':>4}", f"{'Blkd':>4}",
        f"{'Fail':>4}", f"{'Cut':>4}", f"{'Err':>4}", f"{'None':>4}", f"{'Tools':>6}",
        f"{'Min':>6}", f"{'Input':>7}", f"{'Output':>7}", f"{'Cache':>7}",
    ]
    if with_cost:
        columns.append(f"{'Cost':>8}")
    return " ".join(columns)


def _usd(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return None
    return float(value)


def _reset_text(value: object) -> str:
    moment = _parse_time(value)
    return moment.strftime("%Y-%m-%d %H:%M UTC") if moment else ""


def _gateway_lines(gateway: dict) -> list[str]:
    """The figures the gateway itself reported, labelled so, after the
    estimate: they come from the gateway's own records, not from tokens."""
    reader = _clean_text(gateway.get("reader", ""), 30) or "gateway"
    fetched = _reset_text(gateway.get("fetchedAt"))
    lines = ["", f"Reported by your gateway ({reader}{', as of ' + fetched if fetched else ''})"]
    session = gateway.get("latestSession") if isinstance(gateway.get("latestSession"), dict) else {}
    usd, list_usd = _usd(session.get("usd")), _usd(session.get("listUsd"))
    if usd is not None or list_usd is not None:
        text = _money(usd) if usd is not None else f"{_money(list_usd)} at list price"
        extras = []
        requests = _as_int(session.get("requests"))
        if requests > 0:
            extras.append(f"{_compact_number(requests)} request{'s' if requests != 1 else ''}")
        unpriced = _as_int(session.get("unpriced"))
        if unpriced > 0:
            extras.append(f"{_compact_number(unpriced)} unpriced")
        lines.append(f"{'Last session':<12} {text}" + (f" ({'; '.join(extras)})" if extras else ""))
    spend = gateway.get("spend") if isinstance(gateway.get("spend"), dict) else {}
    for key, label in (("today", "Today"), ("week", "This week"), ("month", "This month"),
                       ("period", "This period"), ("total", "All time")):
        entry = spend.get(key)
        if not isinstance(entry, dict):
            continue
        usd, list_usd = _usd(entry.get("usd")), _usd(entry.get("listUsd"))
        if usd is None and list_usd is None:
            continue
        if key == "period" and entry.get("label"):
            label = f"Period ({_clean_text(entry['label'], 20)})"
        text = _money(usd) if usd is not None else f"{_money(list_usd)} at list price"
        extras = []
        if usd is not None and list_usd is not None:
            extras.append(f"list price {_money(list_usd)}")
        unpriced = _as_int(entry.get("unpriced"))
        if unpriced > 0:
            extras.append(f"{_compact_number(unpriced)} request{'s' if unpriced != 1 else ''} unpriced")
        lines.append(f"{label:<12} {text}" + (f" ({'; '.join(extras)})" if extras else ""))
    budget = gateway.get("budget") if isinstance(gateway.get("budget"), dict) else None
    if budget and _usd(budget.get("limitUsd")):
        percent = _usd(budget.get("percent"))
        reset = _reset_text(budget.get("resetAt"))
        used = _usd(budget.get("usedUsd"))
        text = f"{percent:.0f}% used" if percent is not None else "set"
        if used is not None:
            text += f" ({_money(used)} of {_money(budget['limitUsd'])})"
        lines.append(f"{'Budget':<12} {text}" + (f", resets {reset}" if reset else ""))
    for limit in gateway.get("limits") if isinstance(gateway.get("limits"), list) else []:
        if not isinstance(limit, dict) or _usd(limit.get("percent")) is None:
            continue
        reset = _reset_text(limit.get("resetAt"))
        lines.append(f"{'Limit':<12} {_clean_text(limit.get('name', ''), 60) or 'unnamed'} "
                     f"{limit['percent']:.0f}% used" + (f", resets {reset}" if reset else ""))
    for item in gateway.get("exhausted") if isinstance(gateway.get("exhausted"), list) else []:
        if not isinstance(item, dict):
            continue
        reset = _reset_text(item.get("resetAt"))
        lines.append(f"{'Exhausted':<12} all {_clean_text(item.get('provider', ''), 60) or 'unknown'} accounts"
                     + (f" until {reset}" if reset else ""))
    return lines


def format_report(data: dict) -> str:
    """Render a compact, stable text report from ``report`` output."""
    data = data if isinstance(data, dict) else {}
    day_count = max(0, _as_int(data.get("days", 7)))
    lanes = data.get("lanes", {}) if isinstance(data.get("lanes"), dict) else {}
    models = data.get("models", {}) if isinstance(data.get("models"), dict) else {}
    main = data.get("main", {}) if isinstance(data.get("main"), dict) else {}
    cost = data.get("cost") if isinstance(data.get("cost"), dict) else None
    with_cost = cost is not None
    lines = [f"AgentFleet report, last {day_count} days"]

    if lanes:
        lines.extend(["", _stats_table_header("Lanes", with_cost)])
        for lane, stats in sorted(lanes.items()):
            if isinstance(stats, dict):
                lines.append(_stats_table_row(lane, stats, "Lanes", with_cost))
        totals = data.get("totals", {}) if isinstance(data.get("totals"), dict) else {}
        lines.append(_stats_table_row("TOTAL", totals, "Lanes", with_cost))
        forbidden = [
            f"fleet-{lane} {_compact_number(stats.get('forbidden', 0))}"
            for lane, stats in sorted(lanes.items())
            if isinstance(stats, dict) and _as_int(stats.get("forbidden")) > 0
        ]
        if forbidden:
            lines.append("Forbidden commands: " + ", ".join(forbidden))

    if models:
        lines.extend(["", "Models (as the gateway recorded them)", _stats_table_header("Model", with_cost)])
        for model, stats in sorted(models.items()):
            if isinstance(stats, dict):
                lines.append(_stats_table_row(model, stats, "Model", with_cost))

    if main:
        lines.extend(["", "Main conversation", f"{'Model':<20} {'Msgs':>6} {'Input':>7} {'Output':>7} {'Cache':>7}" + (f" {'Cost':>8}" if with_cost else "")])
        for model, usage in sorted(main.items()):
            if not isinstance(usage, dict):
                continue
            shown_model = model if len(model) <= 20 else model[:19] + "…"
            columns = [
                f"{shown_model:<20}", f"{_compact_number(usage.get('messages', 0)):>6}",
                f"{_compact_number(usage.get('inputTokens', 0)):>7}",
                f"{_compact_number(usage.get('outputTokens', 0)):>7}",
                f"{_compact_number(_as_int(usage.get('cacheReadTokens')) + _as_int(usage.get('cacheWriteTokens'))):>7}",
            ]
            if with_cost:
                columns.append(f"{_money(usage.get('estimatedCost')):>8}")
            lines.append(" ".join(columns))

    lines.append("")
    if with_cost:
        lines.append(
            f"Estimated cost: {_money(cost.get('total'))} (lanes {_money(cost.get('lanes'))}, "
            f"main {_money(cost.get('main'))}) at the model prices set or published by your gateway; "
            "an estimate, not a bill."
        )
        notices = []
        if cost.get("unpriced"):
            notices.append("No price for: " + ", ".join(str(name) for name in cost["unpriced"]) + ".")
        if cost.get("cacheNotPriced"):
            notices.append("Cache not priced for: " + ", ".join(str(name) for name in cost["cacheNotPriced"]) + ".")
        if notices:
            lines.append(" ".join(notices))
    else:
        lines.append("Estimated cost: not shown; set model prices with agentfleet config prices.")

    gateway = data.get("gateway") if isinstance(data.get("gateway"), dict) else None
    if gateway:
        lines.extend(_gateway_lines(gateway))

    outages = data.get("outages", {}).get("lanes", {}) if isinstance(data.get("outages"), dict) else {}
    for lane, value in sorted(outages.items()) if isinstance(outages, dict) else []:
        if isinstance(value, dict):
            lines.append(f"OUTAGE fleet-{lane}: {value.get('status')} until {value.get('until')}")
    return "\n".join(lines)


def _main() -> int:
    parser = argparse.ArgumentParser(description="AgentFleet subagent activity report")
    subparsers = parser.add_subparsers(dest="action", required=True)
    report_parser = subparsers.add_parser("report", help="show recent lane activity")
    report_parser.add_argument("--home", type=Path, default=_home_default())
    report_parser.add_argument("--days", type=int, default=7)
    report_parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    data = report(args.home.expanduser(), max(0, args.days))
    print(json.dumps(data, ensure_ascii=False, indent=2) if args.as_json else format_report(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
