"""Operational log (ADR 0032): one JSON line per real provider request and per scheduled tick.

`logged_urlopen` is the single place data-provider HTTP calls go through, so latency, success and
failure are recorded for every provider without each adapter re-implementing it. The log holds NO
URLs, headers or keys: callers pass a short `endpoint` label. LLM calls are not duplicated here: they
already carry latency/tokens/cost/status in `calls.jsonl` (`LLMCallRecord`).
"""

import json
import os
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def ops_dir() -> Path:
    d = Path(os.environ.get("FOOTBALL_OPS_DIR", ROOT / "artifacts" / "ops"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _append(name: str, row: dict, directory: Path | None = None) -> None:
    path = (directory or ops_dir()) / name
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")


def record_call(
    provider: str,
    endpoint: str,
    ok: bool,
    latency_ms: float,
    status: int | None = None,
    error: str | None = None,
    response_bytes: int | None = None,
    directory: Path | None = None,
) -> None:
    _append(
        "provider_calls.jsonl",
        {
            "ts": datetime.now(UTC).isoformat(), "provider": provider, "endpoint": endpoint, "ok": ok,
            "latency_ms": round(latency_ms, 1), "status": status, "error": (error or None) and error[:120],
            "response_bytes": response_bytes,
        },
        directory,
    )  # fmt: skip


def heartbeat(name: str, directory: Path | None = None) -> None:
    """A scheduled tick calls this when it starts; gaps between heartbeats reveal a dead scheduler or a
    machine that was switched off."""
    _append("heartbeats.jsonl", {"ts": datetime.now(UTC).isoformat(), "name": name}, directory)


def read_rows(name: str, directory: Path | None = None) -> list[dict]:
    path = (directory or ops_dir()) / name
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def logged_urlopen(provider: str, endpoint: str, req, timeout: float) -> bytes:
    """`urlopen` + read, recording the outcome. The original exception is re-raised unchanged."""
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - callers pass fixed https URLs
            body = resp.read()
            status = resp.status
    except urllib.error.HTTPError as e:
        record_call(provider, endpoint, False, (time.monotonic() - t0) * 1000, e.code, f"HTTP {e.code}")
        raise
    except (urllib.error.URLError, TimeoutError) as e:
        record_call(provider, endpoint, False, (time.monotonic() - t0) * 1000, None, type(e).__name__)
        raise
    record_call(provider, endpoint, True, (time.monotonic() - t0) * 1000, status, None, len(body))
    return body
