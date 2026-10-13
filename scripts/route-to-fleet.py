#!/usr/bin/env python3
# claude-agents-config:managed
"""UserPromptSubmit hook: AgentFleet notices, and lane routing in fleet mode.

Runs on every prompt. In every work style it passes on the watcher's lines
(lane reports, outages) and a pending suggestion notice. Only the fleet work
style also names a lane to dispatch: the wording of a prompt says nothing
reliable about the size of the job, so in balanced and direct mode Claude
decides after reading the code (see config/agentfleet.md).
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True
_HOOK_DIR = str(Path(__file__).resolve().parent)
if _HOOK_DIR not in sys.path:
    sys.path.insert(0, _HOOK_DIR)
try:
    import fleet_watch
except ImportError:
    fleet_watch = None
try:
    import fleet_suggest
except ImportError:
    fleet_suggest = None
try:
    import provider_catalog
except ImportError:
    provider_catalog = None

# Ordered: the first lane whose pattern matches wins. Specific before general.
LANES = [
    ("fleet-security-review", r"\b(security|vulnerab|exploit|auth[nz]?|authoriz|authentic|secret|credential|token leak|injection|xss|csrf|sql ?inject|permission model|trust boundar)\w*"),
    ("fleet-tests",           r"\b(test|tests|testing|spec suite|unit test|integration test|e2e|coverage|flaky|failing test|pytest|jest|vitest|phpunit|pest)\w*"),
    ("fleet-docs",            r"\b(readme|changelog|documentation|document this|write docs|api reference|runbook|guide|docstring)\w*"),
    ("fleet-ui",             r"\b(ui|ux|component|css|styling|layout|responsive|accessib|a11y|frontend|screen|button|modal|form design|tailwind|design system)\w*"),
    ("fleet-review",          r"\b(review|critique|audit|code smell|look over|sanity.?check|check (?:my|the|this) (?:diff|change|branch|pr|pull request|code))\w*"),
    ("fleet-diagnose-static", r"\b(debug|diagnos|root ?cause|why (?:is|does|did|won'?t|isn'?t)|traceback|stack ?trace|crash|broken|failing|regression|bug in)\w*"),
    ("fleet-plan",            r"\b(plan|design|architect|approach|strategy|how should (?:i|we)|propose|options for|trade.?offs?|refactor plan)\w*"),
    ("fleet-research-web",     r"https?://|\b(?:search the web|look ?up|google it|evaluate (?:this|the) (?:tool|library|service|api|package|vendor)|worth (?:using|adopting|paying)|should we (?:use|adopt|buy|switch)|found this (?:tool|library|service|skill|product)|latest version of|current (?:docs|documentation)|docs for|release notes|changelog for|compare (?:it |them )?(?:to|with|against) (?:other|alternatives))\w*"),
    ("fleet-research-codebase", r"\b(how does|how do|explain (?:the|this|how)|understand (?:the|this|how)|walk me through|trace (?:the|how)|survey|research)\w*"),
    ("fleet-explore-narrow",  r"\b(where is|where are|find (?:the|all|every)|locate|which file|search for|grep for|list all)\w*"),
    ("fleet-triage-static",   r"\b(triage|prioriti[sz]|who owns|scope (?:of|this)|next step|what should we do about)\w*"),
    ("fleet-implement",       r"\b(implement|build|add|create|write|fix|refactor|migrate|rename|extract|wire up|hook up|support for|make it|change the)\w*"),
]

SKIP = re.compile(
    r"^(y|n|yes|no|ok|okay|sure|thanks|thank you|go|go ahead|do it|continue|resume|stop|wait|"
    r"proceed|approved?|lgtm|nice|great|perfect|got it|hmm+|\?+|/\w+.*)$",
    re.IGNORECASE,
)
ALREADY_ROUTED = re.compile(r"\bfleet-[a-z0-9-]+|@[a-z0-9-]*fleet", re.IGNORECASE)
INLINE = re.compile(
    r"\b(inline|yourself|don'?t delegate|no subagent|without delegat|just (?:tell|show|answer)|"
    r"quick question|what (?:is|are|does) (?:the|this|that)\b)",
    re.IGNORECASE,
)

try:
    payload = json.load(sys.stdin)
except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
    sys.exit(0)
if not isinstance(payload, dict):
    sys.exit(0)

CONFIG_HOME = Path(os.environ.get("CLAUDE_FLEET_HOME", Path(__file__).resolve().parent.parent)).expanduser()
CLAUDE_DIR = CONFIG_HOME / ".claude"
LOG_PATH = str(CLAUDE_DIR / "routing-log.jsonl")
LOG_MAX_BYTES = 2_000_000
PROMPT_EXCERPT = 160
# Two tiers: the redacted excerpts are the near-term tuning surface and expire
# first; the bare counts are cheap and survive longer so trends stay readable.
PROMPT_RETENTION_DAYS = 14
ENTRY_RETENTION_DAYS = 90
PURGE_STAMP = str(CLAUDE_DIR / ".routing-log-purged")
PURGE_INTERVAL_SECONDS = 86_400

# Anything that looks like a credential is masked before it can reach the log.
SECRETS = re.compile(
    r"(sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{8,}|xox[baprs]-[A-Za-z0-9-]{8,}"
    r"|github_pat_[A-Za-z0-9_]{8,}|[sr]k_(?:live|test)_[A-Za-z0-9]{8,}"
    r"|(?i:bearer)\s+\S+|(?<=://)[^/\s:@]+:[^/\s@]+(?=@)"
    r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}|AKIA[0-9A-Z]{12,}"
    r"|[A-Za-z0-9._%%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
    r"|\b(?:\d[ -]*?){13,19}\b"
    r"|(?i:(?:password|passwd|secret|token|api[_-]?key|authorization|aws_secret_access_key|mysql_pwd|pgpassword)\s*[=:]\s*)(?:(?i:bearer|basic)\s+)?\S+"
    r"|(?i:--password[= ])\S+|(?<=\s-p)\S{3,})"
)


def _purge_file(path, now):
    """Drop entries past the entry horizon and strip text past the text horizon."""
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return

    kept = []
    changed = False
    for line in lines:
        line = line.strip()
        if not line:
            changed = True
            continue
        try:
            entry = json.loads(line)
            age = (now - datetime.fromisoformat(entry["ts"])).total_seconds()
        except (ValueError, KeyError, TypeError):
            kept.append(line)          # unreadable: keep rather than destroy
            continue
        if age > ENTRY_RETENTION_DAYS * 86_400:
            changed = True
            continue
        if "prompt" in entry and age > PROMPT_RETENTION_DAYS * 86_400:
            del entry["prompt"]
            changed = True
            line = json.dumps(entry, ensure_ascii=False)
        kept.append(line)

    if not changed:
        return
    if not kept:
        try:
            os.unlink(path)
        except OSError:
            pass
        return

    tmp = path + ".tmp"
    payload = ("\n".join(kept) + "\n").encode("utf-8")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short routing-log write")
            view = view[written:]
        os.fsync(fd)
        try:
            os.fchmod(fd, 0o600)
        except AttributeError:
            pass
    finally:
        os.close(fd)
    os.replace(tmp, path)


def maybe_purge():
    """Apply retention at most once a day. Cost on other prompts is one stat call."""
    try:
        now = datetime.now().astimezone()
        try:
            if now.timestamp() - os.path.getmtime(PURGE_STAMP) < PURGE_INTERVAL_SECONDS:
                return
        except OSError:
            pass
        for path in (LOG_PATH, LOG_PATH + ".1"):
            if os.path.exists(path):
                _purge_file(path, now)
        stamp_fd = os.open(PURGE_STAMP, os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            try:
                os.fchmod(stamp_fd, 0o600)
            except AttributeError:
                pass
            os.fsync(stamp_fd)
        finally:
            os.close(stamp_fd)
        os.utime(PURGE_STAMP, None)
    except Exception:
        pass


def record(reason, prompt, lane=None):
    """Append one routing decision. Never raises: a logging fault must not break the prompt."""
    try:
        CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
        maybe_purge()
        try:
            if os.path.getsize(LOG_PATH) > LOG_MAX_BYTES:
                os.replace(LOG_PATH, LOG_PATH + ".1")
        except OSError:
            pass
        entry = {
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "reason": reason,
            "lane": lane,
            "chars": len(prompt),
        }
        # Only fleet mode's unmatched prompts are a tuning surface, so only they
        # keep text. Redact the complete excerpt before truncation so a secret
        # crossing the boundary cannot leave a partially preserved token.
        if reason == "no-match" and work_style == "fleet":
            entry["prompt"] = SECRETS.sub("[redacted]", prompt)[:PROMPT_EXCERPT]
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        fd = os.open(LOG_PATH, flags, 0o600)
        try:
            try:
                os.fchmod(fd, 0o600)
            except AttributeError:
                pass
            payload = (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
            view = memoryview(payload)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("short routing-log write")
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
    except Exception:
        pass

prompt = (payload.get("prompt") or "").strip()
preference_home = Path(os.environ.get("CLAUDE_FLEET_HOME", Path(__file__).resolve().parent.parent)).expanduser()
# The default style, also used when the preferences cannot be read: it adds no
# routing line and keeps no prompt text.
work_style = "balanced"
if provider_catalog is not None:
    try:
        work_style = provider_catalog.load_preferences(preference_home)["workStyle"]
    except Exception:
        pass
watcher_lines = []
if fleet_watch is not None:
    try:
        watcher_home = Path(os.environ.get("CLAUDE_FLEET_HOME", Path(__file__).resolve().parent.parent)).expanduser()
        watcher_lines = fleet_watch.on_prompt(payload, watcher_home)
    except Exception:
        watcher_lines = []


pending_suggestion = None


def emit_context(lines):
    lines = [line for line in lines if isinstance(line, str) and line]
    if pending_suggestion and pending_suggestion not in lines:
        lines.append(pending_suggestion)
    routing = [line for line in lines if line.startswith("Routing:")]
    suggestions = [line for line in lines if line.startswith("AgentFleet:")]
    notices = [line[:400] for line in lines if not line.startswith(("Routing:", "AgentFleet:"))]
    essential = suggestions + routing
    essential_size = sum(len(line) for line in essential) + max(0, len(essential) - 1)
    remaining = max(0, 1500 - essential_size - (1 if notices and essential else 0))
    bounded = []
    for line in notices:
        line = line.replace("\n", " ")
        extra = len(line) + (1 if bounded else 0)
        if extra > remaining:
            if remaining > (1 if bounded else 0):
                bounded.append(line[:remaining - (1 if bounded else 0)])
            break
        bounded.append(line)
        remaining -= extra
    lines = bounded + essential
    if lines:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": "\n".join(lines),
            }
        }))
        if pending_suggestion and pending_suggestion in lines and fleet_suggest is not None:
            try:
                fleet_suggest.mark_notified(payload.get("cwd") or "", suggestion_home)
            except Exception:
                pass


def leave(reason, prompt, lane=None):
    record(reason, prompt, lane)
    emit_context(watcher_lines)
    sys.exit(0)


suggestion_home = Path(os.environ.get("CLAUDE_FLEET_HOME", Path(__file__).resolve().parent.parent)).expanduser()
if fleet_suggest is not None and hasattr(fleet_suggest, "pending_notice"):
    try:
        cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else ""
        # Recorded as shown by emit_context only once it is printed.
        candidate = fleet_suggest.pending_notice(cwd, suggestion_home)
        try:
            settings = json.loads((suggestion_home / ".claude" / "settings.json").read_text(encoding="utf-8"))
            band_enabled = settings.get("enabledPlugins", {}).get("fleet-status@agentfleet") is True
        except (OSError, ValueError, AttributeError):
            band_enabled = False
        pending_suggestion = None if band_enabled else candidate
    except Exception:
        pending_suggestion = None

if not prompt:
    emit_context(watcher_lines)
    sys.exit(0)
# System-injected envelopes (task notifications, reminders) are not user requests.
if prompt.startswith("<"):
    leave("system-envelope", prompt)
if len(prompt) < 12:
    leave("too-short", prompt)
if SKIP.match(prompt):
    leave("skip-phrase", prompt)
if ALREADY_ROUTED.search(prompt):
    leave("already-named", prompt)
if INLINE.search(prompt):
    leave("inline-requested", prompt)

# A leading "add/write tests" is test work; otherwise the earliest signal in the
# prompt wins, so "add X and add tests" routes to implement, not tests.
TEST_FIRST = re.compile(r"^\W*(add|write|create|fix|update)\s+(some |a |the )?"
                        r"(unit |integration |e2e |failing |flaky )?tests?\b", re.IGNORECASE)

if TEST_FIRST.match(prompt):
    lane = "fleet-tests"
else:
    hits = []
    for index, (name, pattern) in enumerate(LANES):
        match = re.search(pattern, prompt, re.IGNORECASE)
        if match:
            hits.append((match.start(), index, name))
    if not hits:
        leave("no-match", prompt)
    else:
        lane = min(hits)[2]
original_lane = lane

# Balanced and direct leave the decision to Claude, which reads the code; the
# guessed lane is only logged. The watcher's notices still go through.
if work_style in ("direct", "balanced"):
    record(work_style, prompt, original_lane)
    emit_context(watcher_lines)
    sys.exit(0)

outage = None
alternative = None
if fleet_watch is not None:
    try:
        home = Path(os.environ.get("CLAUDE_FLEET_HOME", Path(__file__).resolve().parent.parent)).expanduser()
        lane_key = lane[len("fleet-"):] if lane.startswith("fleet-") else lane
        outage = fleet_watch.current_outages(home).get("lanes", {}).get(lane_key)
        if outage:
            alternative = fleet_watch.alternative_lane(home, lane)
            if alternative and not alternative.startswith("fleet-"):
                alternative = f"fleet-{alternative}"
    except Exception:
        outage = None
        alternative = None

shape = original_lane.replace("fleet-", "")
if outage:
    status = outage.get("status", "unknown")
    try:
        failed_at = datetime.fromisoformat(outage.get("at", "").replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%H:%M UTC")
    except (AttributeError, TypeError, ValueError):
        failed_at = "unknown time"
    if alternative:
        context = (
            f"Routing: this request has {shape} shape, but `{original_lane}` is failing ({status} since {failed_at}). "
            f"Dispatch `{alternative}` via the Agent tool instead, and say in one line that you did. "
        )
    else:
        context = (
            f"Routing: this request has {shape} shape, but `{original_lane}` is failing ({status} since {failed_at}) "
            f"and no configured alternative is available; retry later instead of dispatching it. "
        )
else:
    context = (
        f"Routing: this request has {shape} shape. "
        f"Dispatch `{lane}` via the Agent tool rather than doing it yourself, and say in one line that you did. "
    )
context += (
    f"Give it a brief carrying the goal, scope, exclusions, the project instructions it needs "
    f"(it does not load CLAUDE.md), verification commands, and what to report back. "
    f"Then verify its work yourself before reporting done. "
    f"Override only when the task is a single obvious edit, already answered by context, or a command "
    f"you need output from — say why in one line if you do."
)

record("routed", prompt, original_lane)
emit_context(watcher_lines + [context])
