"""Provider circuit breaker and bounded retry (ADR 0097), applied inside `src.mlops.oplog.logged_urlopen`, the one HTTP path of every data provider.

Enabled by `PROVIDER_BREAKER=1` (the cloud jobs set it; tests and local runs are unaffected unless they opt in). State per provider, optionally persisted to `PROVIDER_BREAKER_STATE` (a
JSON file) so consecutive scheduled runs share it:
- 429 (rate limited / quota): the circuit opens for Retry-After seconds, at least RATE_LIMIT_COOLDOWN; never retried (retrying burns quota);
- 401 / 403 (auth, expired or revoked key, suspended account): opens for AUTH_COOLDOWN; never retried;
- 5xx, timeouts and network errors: one bounded retry after a short pause for 502 / 503 / 504 / timeouts; FAILURE_THRESHOLD consecutive failures open the circuit for FAILURE_COOLDOWN;
- 404 and other 4xx are a request problem, not a provider fault: no count, no retry;
- a success closes the circuit and resets the count. After the cooldown one trial call is let through (half-open).
An open circuit raises `CircuitOpen`, a `urllib.error.URLError`, so every caller's existing provider-error path handles it (the caller shows UNKNOWN or keeps the last stored data; nothing
is invented). A response body that does not parse is the caller's schema guard (`ProviderError` / `None` from the parsers), recorded by `schema_error()` without opening the circuit.
"""

import json
import os
import time
import urllib.error
from datetime import UTC, datetime
from pathlib import Path

FAILURE_THRESHOLD = 3
FAILURE_COOLDOWN = 30 * 60
RATE_LIMIT_COOLDOWN = 60 * 60
AUTH_COOLDOWN = 6 * 60 * 60
RETRY_CODES = (502, 503, 504)
RETRY_PAUSE = 2.0

_STATE: dict[str, dict] = {}
_LOADED: set[str] = set()


class CircuitOpen(urllib.error.URLError):
    pass


def enabled() -> bool:
    return os.environ.get("PROVIDER_BREAKER", "0") == "1"


def _path() -> Path | None:
    p = os.environ.get("PROVIDER_BREAKER_STATE")
    return Path(p) if p else None


def _load() -> None:
    p = _path()
    if p and str(p) not in _LOADED:
        _LOADED.add(str(p))
        if p.is_file():
            try:
                _STATE.update(json.loads(p.read_text(encoding="utf-8")))
            except (ValueError, OSError):
                pass  # a corrupt state file starts closed; the next write replaces it


def _save() -> None:
    p = _path()
    if p:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(_STATE, indent=1, sort_keys=True), encoding="utf-8", newline="\n")
        tmp.replace(p)


def _now() -> float:
    return time.time()


def state(provider: str) -> dict:
    _load()
    return _STATE.setdefault(provider, {"failures": 0, "open_until": 0.0, "reason": None, "opened_total": 0, "schema_errors": 0})


def guard(provider: str) -> None:
    s = state(provider)
    if s["open_until"] > _now():
        raise CircuitOpen(f"circuit open for {provider} ({s['reason']}) until {datetime.fromtimestamp(s['open_until'], UTC).isoformat()}")


def _open(provider: str, seconds: float, reason: str) -> None:
    s = state(provider)
    s.update(open_until=_now() + seconds, reason=reason, opened_total=s["opened_total"] + 1, opened_at=datetime.now(UTC).isoformat())
    _save()


def success(provider: str) -> None:
    s = state(provider)
    if s["failures"] or s["open_until"]:
        s.update(failures=0, open_until=0.0, reason=None)
        _save()


def failure(provider: str, code: int | None, retry_after: float | None = None) -> None:
    """Classify one failed call; `code` None = network error or timeout."""
    if code == 429:
        _open(provider, max(retry_after or 0.0, RATE_LIMIT_COOLDOWN), "RATE_LIMITED")
    elif code in (401, 403):
        _open(provider, AUTH_COOLDOWN, "AUTH")
    elif code is None or code >= 500:
        s = state(provider)
        s["failures"] += 1
        if s["failures"] >= FAILURE_THRESHOLD:
            _open(provider, FAILURE_COOLDOWN, "FAILURES")
        else:
            _save()


def schema_error(provider: str) -> None:
    state(provider)["schema_errors"] += 1
    _save()


def retryable(code: int | None) -> bool:
    return code is None or code in RETRY_CODES


def retry_after_of(exc: urllib.error.HTTPError) -> float | None:
    try:
        return float(exc.headers.get("Retry-After")) if exc.headers and exc.headers.get("Retry-After") else None
    except (TypeError, ValueError):
        return None


def observe(provider: str, call):
    """Guard + classify a quota-metered call that has its own HTTP code path (The Odds API, Highlightly): no retry, ever (a retry spends quota). A no-op wrapper when disabled."""
    if not enabled():
        return call()
    guard(provider)
    try:
        out = call()
    except urllib.error.HTTPError as e:
        failure(provider, e.code, retry_after_of(e))
        raise
    except (urllib.error.URLError, TimeoutError):
        failure(provider, None)
        raise
    success(provider)
    return out


def snapshot() -> dict:
    _load()
    now = _now()
    return {p: {**s, "open": s["open_until"] > now} for p, s in sorted(_STATE.items())}


def reset() -> None:
    _STATE.clear()
    _LOADED.clear()
