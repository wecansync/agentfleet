#!/usr/bin/env python3
"""Read and cache private usage summaries from supported model gateways."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlsplit

sys.dont_write_bytecode = True
_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

try:
    import provider_catalog
except ImportError:  # An incomplete installation must not break a hook.
    provider_catalog = None

FORMAT = "agentfleet.provider-usage.v1"
STORE_NAME = "provider-usage.json"
PUBLISHED_PRICES_NAME = "published-prices.json"
MAX_RESPONSE_BYTES = 1024 * 1024
OVERALL_TIMEOUT = 12.0
# A gateway's first answer after a quiet spell can take several seconds.
REQUEST_TIMEOUT = 8.0
# The background reader ends after this many seconds, whatever it waits on.
WATCHDOG_SECONDS = OVERALL_TIMEOUT + REQUEST_TIMEOUT
REFRESH_INTERVAL = 300
SESSION_INTERVAL = 30
RUNNING_INTERVAL = 60
UNKNOWN_RETRY_INTERVAL = 24 * 60 * 60
_SESSION_ID = re.compile(r"^[A-Za-z0-9-]{8,80}$")
_READERS = ("omniroute", "openrouter", "litellm")
# Returned when a gateway could not be reached or was briefly failing, as
# opposed to answering that it has no such report. Only a real answer may
# decide that a gateway reports no usage.
_UNREACHABLE = object()
_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524})


def _store_path(home: Path) -> Path:
    return Path(home) / ".claude" / "agentfleet" / STORE_NAME


def _path_has_symlink(path: Path) -> bool:
    """Reject any symlink in the path used to access the store."""
    return any(candidate.is_symlink() for candidate in (path, *path.parents))


def _now_iso(now: float | None = None) -> str:
    value = time.time() if now is None else now
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _timestamp(value: object) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _normalise_time(value: object) -> str | None:
    timestamp = _timestamp(value)
    if timestamp is None:
        return None
    try:
        return _now_iso(timestamp)
    except (ValueError, OverflowError, OSError):
        return None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        result = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(result) or result < 0:
        return None
    return result


def _count(value: object) -> int | None:
    number = _number(value)
    if number is None or not number.is_integer():
        return None
    try:
        return int(number)
    except (OverflowError, ValueError):
        return None


def _text(value: object, token: str | None = None, default: str | None = None) -> str | None:
    if not isinstance(value, str):
        return default
    printable = "".join(character for character in value if character.isprintable()).strip()
    if token and token in printable:
        return default
    return printable[:60] or default


def _first_number(*values: object) -> float | None:
    for value in values:
        number = _number(value)
        if number is not None:
            return number
    return None


def _token_total(value: object) -> int | None:
    if isinstance(value, dict):
        return _count(value.get("total"))
    return _count(value)


def _spend(
    usd: object = None,
    list_usd: object = None,
    unpriced: object = None,
    requests: object = None,
    tokens: object = None,
    reset_at: object = None,
    label: object = None,
    token: str | None = None,
) -> dict:
    return {
        "usd": _number(usd),
        "listUsd": _number(list_usd),
        "unpriced": _count(unpriced),
        "requests": _count(requests),
        "tokens": _token_total(tokens),
        "resetAt": _normalise_time(reset_at),
        "label": _text(label, token),
    }


def _budget(
    limit_usd: object,
    used_usd: object,
    remaining_usd: object,
    percent: object,
    reset_at: object,
    period: object,
    token: str | None = None,
) -> dict:
    return {
        "limitUsd": _number(limit_usd),
        "usedUsd": _number(used_usd),
        "remainingUsd": _number(remaining_usd),
        "percent": _number(percent),
        "resetAt": _normalise_time(reset_at),
        "period": _text(period, token),
    }


def _percent(used: float | None, limit: float | None) -> float | None:
    if used is None or limit is None or limit <= 0:
        return None
    result = used / limit * 100.0
    return result if math.isfinite(result) and result >= 0 else None


def _omni_exhausted(rows: object, token: str | None, now: float) -> list[dict]:
    if not isinstance(rows, list):
        return []
    grouped: dict[str, list[list[object]]] = {}
    for row in rows[:500]:
        if not isinstance(row, dict):
            continue
        provider = _text(row.get("provider"), token)
        quotas = row.get("quotas")
        if not provider or not isinstance(quotas, dict) or not quotas:
            continue
        quota_states: list[object] = []
        for quota in list(quotas.values())[:500]:
            if not isinstance(quota, dict):
                quota_states.append(False)
                continue
            used = _number(quota.get("usedPercentage"))
            remaining = _number(quota.get("remainingPercentage"))
            quota_states.append((used is not None and used >= 100) or (remaining is not None and remaining <= 0))
        if not quota_states:
            continue
        reset_times = [
            (_timestamp(quota.get("resetAt")), _normalise_time(quota.get("resetAt")))
            for quota in list(quotas.values())[:500]
            if isinstance(quota, dict)
        ]
        future_resets = [(stamp, iso) for stamp, iso in reset_times if stamp is not None and stamp > now and iso]
        grouped.setdefault(provider, []).append([all(state is True for state in quota_states), future_resets])

    exhausted: list[dict] = []
    for provider, accounts in grouped.items():
        if not accounts or not all(account[0] is True for account in accounts):
            continue
        resets = [entry for account in accounts for entry in account[1]]
        earliest = min(resets, key=lambda entry: entry[0])[1] if resets else None
        exhausted.append({"provider": provider, "resetAt": earliest})
    return exhausted[:100]


def _omni_limits(rows: object, token: str | None) -> list[dict]:
    if not isinstance(rows, list):
        return []
    limits: list[dict] = []
    for row in rows[:500]:
        if not isinstance(row, dict):
            continue
        limit = _number(row.get("limit"))
        used = _number(row.get("used"))
        if limit is None or used is None or limit <= 0 or used <= 0:
            continue
        percent = _percent(used, limit)
        if percent is None:
            continue
        # OmniRoute scopes a limit as {"type": "provider", "value": "claude"}
        # or {"type": "global"}; name it by what it covers, e.g. "claude daily tokens".
        scope = row.get("scope")
        if isinstance(scope, dict):
            covers = "all providers" if scope.get("type") == "global" else _text(scope.get("value"), token)
        else:
            covers = _text(scope, token) or _text(row.get("provider"), token)
        name_parts = [
            covers,
            _text(row.get("window"), token),
            _text(row.get("metric"), token) or _text(row.get("source"), token),
        ]
        name = " ".join(part for part in name_parts if part) or "gateway limit"
        exceeded = row.get("exceeded") if isinstance(row.get("exceeded"), bool) else percent >= 100
        limits.append({
            "name": name[:60],
            "percent": percent,
            "resetAt": _normalise_time(row.get("resetAt")),
            "exceeded": exceeded,
        })
    return limits


def _omni_usage(payload: dict, token: str | None, now: float) -> dict:
    upstream = payload["usage"]
    cost = upstream["cost"]
    spend: dict[str, dict] = {}
    for name, raw, label in (
        ("today", upstream.get("daily"), "today"),
        ("week", upstream.get("weekly"), "week"),
        ("month", cost, "month"),
    ):
        raw = raw if isinstance(raw, dict) else {}
        spend[name] = _spend(
            _first_number(raw.get("usedUsd"), raw.get("costUsd")),
            raw.get("listPriceUsd"),
            raw.get("unpricedRequests"),
            raw.get("requests"),
            raw.get("tokens"),
            raw.get("resetAt"),
            label,
            token,
        )

    limit = _number(cost.get("limitUsd"))
    budget = None
    if limit is not None and limit > 0:
        used = _first_number(cost.get("usedUsd"), cost.get("costUsd"))
        supplied_percent = _number(cost.get("usedPercent"))
        if supplied_percent is not None and supplied_percent > 100:
            supplied_percent = None
        remaining = _number(cost.get("remainingUsd"))
        if remaining is None and used is not None:
            remaining = max(0.0, limit - used)
        budget = _budget(
            limit,
            used,
            remaining,
            supplied_percent if supplied_percent is not None else _percent(used, limit),
            cost.get("resetAt"),
            cost.get("period"),
            token,
        )
    return {
        "spend": spend,
        "budget": budget,
        "limits": _omni_limits(payload.get("limits"), token),
        "exhausted": _omni_exhausted(payload.get("accountQuotas"), token, now),
    }


def _openrouter_usage(payload: dict, token: str | None) -> dict:
    data = payload["data"]
    spend = {
        "today": _spend(data.get("usage_daily"), label="today", token=token),
        "week": _spend(data.get("usage_weekly"), label="week", token=token),
        "month": _spend(data.get("usage_monthly"), label="month", token=token),
        "total": _spend(data.get("usage"), label="total", token=token),
    }
    limit = _number(data.get("limit"))
    budget = None
    if limit is not None and limit > 0:
        remaining = _number(data.get("limit_remaining"))
        used = limit - remaining if remaining is not None and remaining <= limit else None
        if remaining is None and spend["total"]["usd"] is not None:
            used = spend["total"]["usd"]
            remaining = max(0.0, limit - used)
        budget = _budget(
            limit,
            used,
            remaining,
            _percent(used, limit),
            data.get("limit_reset") if isinstance(data.get("limit_reset"), str) and _timestamp(data.get("limit_reset")) is not None else None,
            _text(data.get("limit_reset"), token),
            token,
        )
    limits: list[dict] = []
    raw_budgets = data.get("budgets")
    if isinstance(raw_budgets, list):
        for row in raw_budgets[:500]:
            if not isinstance(row, dict):
                continue
            budget_limit = _number(row.get("limit_usd"))
            used = _number(row.get("spend_usd"))
            percent = _percent(used, budget_limit)
            if budget_limit is None or budget_limit <= 0 or used is None or percent is None:
                continue
            interval = _text(row.get("reset_interval"), token, "unknown")
            limits.append({
                "name": f"{interval} budget"[:60],
                "percent": percent,
                "resetAt": _normalise_time(row.get("resets_at")),
                "exceeded": percent >= 100,
            })
    return {"spend": spend, "budget": budget, "limits": limits, "exhausted": []}


def _litellm_usage(payload: dict, token: str | None) -> dict:
    info = payload["info"]
    spend_value = _number(info.get("spend"))
    label = _text(info.get("budget_duration"), token, "total")
    spend = {"period": _spend(spend_value, label=label, token=token)}
    limit = _number(info.get("max_budget"))
    budget = None
    if limit is not None and limit > 0:
        remaining = max(0.0, limit - spend_value) if spend_value is not None else None
        budget = _budget(
            limit,
            spend_value,
            remaining,
            _percent(spend_value, limit),
            info.get("budget_reset_at"),
            label,
            token,
        )
    return {"spend": spend, "budget": budget, "limits": [], "exhausted": []}


def _session_row(payload: object, session_id: str, token: str | None, fetched_at: str) -> dict | None:
    if not isinstance(payload, dict) or not isinstance(payload.get("sessions"), list):
        return None
    for row in payload["sessions"][:500]:
        if not isinstance(row, dict) or row.get("clientSessionId") != session_id:
            continue
        cost = _number(row.get("costUsd"))
        listed = _number(row.get("listPriceUsd"))
        if listed is None:
            usd, list_usd = None, cost
        else:
            usd, list_usd = cost, listed
        return {
            "usd": usd,
            "listUsd": list_usd,
            "unpriced": _count(row.get("unpricedCount")),
            "requests": _count(row.get("requestCount")),
            "errors": _count(row.get("errorCount")),
            "tokens": _token_total(row.get("tokens")),
            "fetchedAt": fetched_at,
        }
    return None


def _request_json(base: str, path: str, token: str, deadline: float) -> object | None:
    """Return the decoded answer, None when the gateway answered without one,
    or _UNREACHABLE when it could not be reached in time or was failing."""
    url = base + path
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return _UNREACHABLE
    try:
        request = urllib.request.Request(
            url,
            headers={
                "authorization": "Bearer " + token,
                "x-api-key": token,
                "accept": "application/json",
                "user-agent": "claude-agents-config/1.0.0",
            },
            method="GET",
        )
        opener = provider_catalog._credential_opener(url)
        with opener.open(request, timeout=min(REQUEST_TIMEOUT, remaining)) as response:
            if response.getcode() != 200 or response.geturl() != url:
                return None
            # The socket timeout only limits each wait, so a gateway that keeps
            # sending a few bytes could hold a single read() open for ever.
            # Read what has arrived, piece by piece, and stop at the deadline.
            chunks: list[bytes] = []
            size = 0
            while True:
                if time.monotonic() >= deadline:
                    return _UNREACHABLE
                chunk = response.read1(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    return None
                chunks.append(chunk)
    except urllib.error.HTTPError as error:
        try:
            error.close()
        except BaseException:
            pass
        return _UNREACHABLE if error.code in _RETRYABLE_STATUS else None
    except BaseException:
        # A timeout, refused connection or dropped reply. Never surface the
        # exception text: it may contain a URL or request header.
        return _UNREACHABLE
    try:
        return json.loads(b"".join(chunks).decode("utf-8"))
    except BaseException:
        return None


def _read_reader(reader: str, base: str, token: str, session_id: str | None, deadline: float) -> tuple[dict, dict | None] | object | None:
    """Return (usage, session), None when the gateway answered without this
    report, or _UNREACHABLE when it could not tell."""
    if reader == "omniroute":
        payload = _request_json(base, "/v1/me/status", token, deadline)
        if payload is _UNREACHABLE:
            return _UNREACHABLE
        if not isinstance(payload, dict):
            return None
        usage = payload.get("usage")
        if not isinstance(usage, dict) or not isinstance(usage.get("cost"), dict):
            return None
        mapped = _omni_usage(payload, token, time.time())
        session = None
        if session_id:
            query = urlencode({"clientSessionId": session_id})
            session_payload = _request_json(base, "/v1/me/sessions?" + query, token, deadline)
            session = _session_row(session_payload, session_id, token, _now_iso())
        return mapped, session
    if reader == "openrouter":
        payload = _request_json(base, "/v1/key", token, deadline)
        if payload is _UNREACHABLE:
            return _UNREACHABLE
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
            return None
        return _openrouter_usage(payload, token), None
    if reader == "litellm":
        payload = _request_json(base, "/key/info", token, deadline)
        if payload is _UNREACHABLE:
            return _UNREACHABLE
        if not isinstance(payload, dict) or not isinstance(payload.get("info"), dict):
            return None
        return _litellm_usage(payload, token), None
    return None


def gateway(home: Path) -> tuple[str, str] | None:
    """Return the validated gateway endpoint and credential from settings."""
    if provider_catalog is None:
        return None
    try:
        settings = provider_catalog.load_json(Path(home) / ".claude" / "settings.json")
        env = settings.get("env") if isinstance(settings, dict) else None
        if not isinstance(env, dict):
            return None
        base = env.get("ANTHROPIC_BASE_URL")
        token = env.get("ANTHROPIC_AUTH_TOKEN")
        if not isinstance(token, str) or not token or any(character in token for character in "\r\n"):
            return None
        endpoint = provider_catalog.endpoint_scope(base)
        hostname = (urlsplit(endpoint).hostname or "").lower()
        if hostname == "api.anthropic.com" or hostname.endswith(".api.anthropic.com") or token in endpoint:
            return None
        # Validate credentials with the shared account-scope boundary without
        # retaining the resulting digest anywhere outside the summary.
        provider_catalog.account_scope(token)
        return endpoint, token
    except BaseException:
        return None


def _enabled(home: Path) -> bool:
    if provider_catalog is None:
        return False
    try:
        preferences = provider_catalog.load_preferences(home)
        if preferences.get("providerUsage", True) is False:
            return False
        # providerUsage is being added to the shared preference defaults; honor
        # it already if this module is installed with an older catalog module.
        raw = provider_catalog.load_json(provider_catalog.preferences_path(home))
        return not (isinstance(raw, dict) and raw.get("providerUsage") is False)
    except BaseException:
        return True


def load(home: Path) -> dict | None:
    """Load a summary only when its endpoint and key hash still match."""
    current = gateway(home)
    if current is None:
        return None
    endpoint, token = current
    path = _store_path(home)
    try:
        if _path_has_symlink(path) or not path.is_file():
            return None
        if path.stat().st_size > 2 * 1024 * 1024:
            return None
        stored = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(stored, dict) or stored.get("format") != FORMAT:
            return None
        if stored.get("endpoint") != endpoint or stored.get("account") != provider_catalog.account_scope(token):
            return None
        return stored
    except BaseException:
        return None


def scope(home: Path) -> tuple[str, str] | None:
    """The current gateway's endpoint and key hash, or None without a gateway."""
    current = gateway(home)
    if current is None:
        return None
    try:
        return current[0], provider_catalog.account_scope(current[1])
    except BaseException:
        return None


def discard_foreign(home: Path) -> None:
    """Delete saved figures and published prices that belong to another
    gateway or key, or to none on a Claude subscription profile. The mods read
    these files but cannot check the key, so the files themselves must only
    ever hold the current profile's figures."""
    current = scope(home)
    store = _store_path(home)
    for path in (store, store.with_name(PUBLISHED_PRICES_NAME)):
        try:
            if _path_has_symlink(path) or not path.is_file():
                continue
            if current is not None and path.stat().st_size <= 2 * 1024 * 1024:
                stored = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(stored, dict) and (stored.get("endpoint"), stored.get("account")) == current:
                    continue
            path.unlink()
        except BaseException:
            continue


def _age(value: object, now: float) -> float | None:
    stamp = _timestamp(value)
    return now - stamp if stamp is not None else None


def refresh_due(home: Path, session_id: str | None, now: float | None = None) -> bool:
    """Return whether a background usage refresh should be scheduled."""
    try:
        if not _enabled(home) or gateway(home) is None:
            return False
        current = time.time() if now is None else float(now)
        stored = load(home)
        if stored is None:
            return True
        started_age = _age(stored.get("startedAt"), current)
        if started_age is not None and 0 <= started_age < RUNNING_INTERVAL:
            return False
        fetched_age = _age(stored.get("fetchedAt"), current)
        # The last attempt fetched nothing, for instance because the gateway
        # was down: wait the usual interval before the next one.
        last_failed = started_age is not None and (fetched_age is None or fetched_age > started_age)
        if last_failed and 0 <= started_age < REFRESH_INTERVAL:
            return False
        if stored.get("reader") is None:
            if stored.get("detectedAt") is None:
                return True
            detected_age = _age(stored.get("detectedAt"), current)
            return detected_age is None or detected_age >= UNKNOWN_RETRY_INTERVAL
        valid_session = session_id if isinstance(session_id, str) and _SESSION_ID.fullmatch(session_id) else None
        sessions = stored.get("sessions")
        if valid_session and (not isinstance(sessions, dict) or valid_session not in sessions):
            if fetched_age is None or fetched_age >= SESSION_INTERVAL:
                return True
        if fetched_age is None or fetched_age >= REFRESH_INTERVAL:
            return True
        return False
    except BaseException:
        return False


def _clean_sessions(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    sessions: dict[str, dict] = {}
    for session_id, row in value.items():
        if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id) or not isinstance(row, dict):
            continue
        fetched_at = _normalise_time(row.get("fetchedAt"))
        if fetched_at is None:
            continue
        sessions[session_id] = {
            "usd": _number(row.get("usd")),
            "listUsd": _number(row.get("listUsd")),
            "unpriced": _count(row.get("unpriced")),
            "requests": _count(row.get("requests")),
            "errors": _count(row.get("errors")),
            "tokens": _count(row.get("tokens")),
            "fetchedAt": fetched_at,
        }
    return dict(sorted(sessions.items(), key=lambda pair: _timestamp(pair[1]["fetchedAt"]) or 0, reverse=True)[:20])


def _step(percent: float) -> int:
    """Percentages in 5% steps, so a notice (and a hidden status band) stays
    the same between refreshes."""
    return int(percent // 5 * 5)


def _notices(usage: dict, now: float) -> list[dict]:
    notices: list[dict] = []

    def reset_suffix(value: object, date_only: bool = False) -> str:
        stamp = _timestamp(value)
        if stamp is None:
            return ""
        dt = datetime.fromtimestamp(stamp, timezone.utc)
        shown = dt.strftime("%Y-%m-%d" if date_only else "%H:%M UTC")
        return f", resets {shown}"

    budget = usage.get("budget")
    if isinstance(budget, dict):
        percent = _number(budget.get("percent"))
        used = _number(budget.get("usedUsd"))
        limit = _number(budget.get("limitUsd"))
        if percent is not None and percent >= 80:
            amount = f" (${used:.2f} of ${limit:.2f})" if used is not None and limit is not None else ""
            text = f"Gateway budget {_step(percent)}% used{amount}{reset_suffix(budget.get('resetAt'), True)}."
            notices.append({"text": text[:180], "until": _normalise_time(budget.get("resetAt"))})
    limits = usage.get("limits")
    if isinstance(limits, list):
        for row in limits[:100]:
            if not isinstance(row, dict):
                continue
            percent = _number(row.get("percent"))
            if percent is None or percent < 80:
                continue
            name = _text(row.get("name"), default="limit") or "limit"
            text = f"Gateway limit {name} {_step(percent)}% used{reset_suffix(row.get('resetAt'))}."
            notices.append({"text": text[:180], "until": _normalise_time(row.get("resetAt"))})
    exhausted = usage.get("exhausted")
    if isinstance(exhausted, list):
        for row in exhausted[:100]:
            if not isinstance(row, dict):
                continue
            provider = _text(row.get("provider"), default="provider") or "provider"
            reset = row.get("resetAt")
            suffix = ""
            if _timestamp(reset) is not None:
                suffix = f" until {datetime.fromtimestamp(_timestamp(reset), timezone.utc).strftime('%H:%M UTC')}"
            text = f"All {provider} accounts are exhausted{suffix}."
            notices.append({"text": text[:180], "until": _normalise_time(reset)})
    return notices[:201]


def _document(endpoint: str, account: str, reader: str | None, detected_at: str | None, fetched_at: str | None,
              started_at: str | None, usage: dict | None, sessions: dict, notices: list[dict]) -> dict:
    return {
        "format": FORMAT,
        "endpoint": endpoint,
        "account": account,
        "reader": reader,
        "detectedAt": detected_at,
        "fetchedAt": fetched_at,
        "startedAt": started_at,
        "usage": usage,
        "sessions": sessions,
        "notices": notices,
    }


def _save(home: Path, document: dict, token: str) -> bool:
    path = _store_path(home)
    temp: Path | None = None
    try:
        if _path_has_symlink(path):
            return False
        path.parent.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if _path_has_symlink(path):
            return False
        encoded = (json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        if token.encode("utf-8") in encoded:
            return False
        temp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temp, 0o600)
            if _path_has_symlink(path):
                temp.unlink(missing_ok=True)
                return False
            os.replace(temp, path)
            return True
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            raise
    except BaseException:
        if temp is not None:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
        return False


def _remove_stale_temps(home: Path, now: float | None = None) -> None:
    """Remove temporary files a reader left when the watchdog ended it in the
    middle of a save. Only files older than any live reader are touched."""
    path = _store_path(home)
    cutoff = (time.time() if now is None else now) - RUNNING_INTERVAL
    try:
        if _path_has_symlink(path.parent):
            return
        for temp in path.parent.glob(f".{path.name}.*.tmp"):
            try:
                if not temp.is_symlink() and temp.is_file() and temp.stat().st_mtime < cutoff:
                    temp.unlink()
            except OSError:
                continue
    except OSError:
        return


def refresh(home: Path, session_id: str | None) -> int:
    """Fetch supported gateway usage, cache a safe summary, and never raise."""
    try:
        home = Path(home)
        _remove_stale_temps(home)
        if not _enabled(home):
            return 1
        current = gateway(home)
        if current is None or provider_catalog is None:
            return 1
        endpoint, token = current
        account = provider_catalog.account_scope(token)
        previous = load(home)
        now = time.time()
        started_at = _now_iso(now)
        safe_session = session_id if isinstance(session_id, str) and _SESSION_ID.fullmatch(session_id) else None
        sessions = _clean_sessions(previous.get("sessions") if previous else None)
        previous_reader = previous.get("reader") if previous and previous.get("reader") in _READERS else None
        previous_detected = _normalise_time(previous.get("detectedAt")) if previous else None
        previous_fetched = _normalise_time(previous.get("fetchedAt")) if previous else None
        # Saved before asking the gateway, and kept as it is when the gateway
        # cannot be reached: what was known stays, and a reader stopped half
        # way never counts as a gateway without a usage report.
        unchanged = _document(
            endpoint,
            account,
            previous_reader,
            previous_detected,
            previous_fetched,
            started_at,
            previous.get("usage") if previous else None,
            sessions,
            previous.get("notices") if previous and isinstance(previous.get("notices"), list) else [],
        )
        if not _save(home, unchanged, token):
            return 1

        deadline = time.monotonic() + OVERALL_TIMEOUT
        result: tuple[dict, dict | None] | None = None
        reader: str | None = None
        unreachable = False
        redetecting = previous_reader is None
        if previous_reader is not None:
            answer = _read_reader(previous_reader, endpoint, token, safe_session, deadline)
            if answer is _UNREACHABLE:
                unreachable = True
            elif answer is not None:
                reader = previous_reader
                result = answer
            else:
                redetecting = True
        if result is None and redetecting:
            for candidate in _READERS:
                if time.monotonic() >= deadline:
                    unreachable = True
                    break
                answer = _read_reader(candidate, endpoint, token, safe_session, deadline)
                if answer is _UNREACHABLE:
                    unreachable = True
                    continue
                if answer is not None:
                    reader = candidate
                    result = answer
                    break

        # The profile may have changed while the gateway answered; never save
        # one gateway's figures after settings point at another.
        if scope(home) != (endpoint, account):
            return 1
        if result is None or reader is None:
            if unreachable:
                # Not an answer: keep what was known and try again later.
                return 1
            detected_at = _now_iso()
            document = _document(endpoint, account, None, detected_at, None, started_at, None, sessions, [])
            return 0 if _save(home, document, token) else 1

        usage, new_session = result
        if safe_session and new_session is not None:
            sessions[safe_session] = new_session
            sessions = _clean_sessions(sessions)
        fetched_at = _now_iso()
        detected_at = previous_detected if reader == previous_reader and previous_detected else _now_iso()
        document = _document(
            endpoint,
            account,
            reader,
            detected_at,
            fetched_at,
            started_at,
            usage,
            sessions,
            _notices(usage, time.time()),
        )
        return 0 if _save(home, document, token) else 1
    except BaseException:
        return 1


def spawn_refresh(home: Path, session_id: str | None) -> None:
    """Start this module detached without copying credentials to the child."""
    try:
        command = [sys.executable, str(Path(__file__).resolve()), "--refresh", "--home", str(Path(home))]
        if isinstance(session_id, str) and _SESSION_ID.fullmatch(session_id):
            command.extend(["--session", session_id])
        if os.name == "nt":
            detached_process = 0x00000008
            create_new_process_group = 0x00000200
            subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=detached_process | create_new_process_group,
            )
        else:
            subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
    except BaseException:
        return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--home", required=True)
    parser.add_argument("--session")
    args = parser.parse_args(argv)
    if not args.refresh:
        return 1
    _start_watchdog()
    session_id = args.session if isinstance(args.session, str) and _SESSION_ID.fullmatch(args.session) else None
    return refresh(Path(args.home), session_id)



def _start_watchdog() -> None:
    """End this background process after a hard limit, whatever it is waiting
    on, so a slow gateway never leaves readers running. The limit stays well
    under RUNNING_INTERVAL, so no second reader starts while this one lives."""
    timer = threading.Timer(WATCHDOG_SECONDS, os._exit, args=(1,))
    timer.daemon = True
    timer.start()


if __name__ == "__main__":
    raise SystemExit(main())
