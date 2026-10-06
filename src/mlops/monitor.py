"""Monitoring (ADR 0032). Every function reads REAL artifacts/logs and returns a plain dict with a
`status` in {OK, WARNING, STALE, FAILED, INSUFFICIENT_DATA}; none of them fabricates a value when the
data is missing (`INSUFFICIENT_DATA` / `NO_DATA` instead). Thresholds come from `configs/mlops.yaml`.
"""

import json
import math
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from src.config import MlopsConfig

from .oplog import read_rows

SNAP_GLOB = "artifacts/snapshots/*/*/snapshot.json"


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def _pct(values: list[float], q: float) -> float | None:
    return float(np.percentile(values, q)) if values else None


# ---------------------------------------------------------------------------- data freshness
def data_freshness(latest_result_utc: datetime | None, now: datetime, cfg: MlopsConfig) -> dict:
    if latest_result_utc is None:
        return {"status": "FAILED", "reason": "no finished match in the dataset"}
    age = (now - latest_result_utc).total_seconds() / 86400
    t = cfg.data_freshness_days
    status = "FRESH" if age <= t.warning else "WARNING" if age <= t.stale else "STALE"
    return {
        "status": status, "latest_result_utc": latest_result_utc.isoformat(),
        "age_days": round(age, 1), "warning_after_days": t.warning, "stale_after_days": t.stale,
    }  # fmt: skip


# ------------------------------------------------------------------------- provider health
def provider_health(calls: list[dict], now: datetime, cfg: MlopsConfig) -> dict[str, dict]:
    """Per provider over the last `window_hours`: requests, errors, error rate, latency percentiles,
    last success/failure, freshness (time since the last successful request)."""
    since = now - timedelta(hours=cfg.provider.window_hours)
    by: dict[str, list[dict]] = defaultdict(list)
    for c in calls:
        by[c["provider"]].append(c)
    out = {}
    for prov, rows in sorted(by.items()):
        window = [r for r in rows if _parse(r["ts"]) >= since]
        ok = [r for r in rows if r["ok"]]
        bad = [r for r in rows if not r["ok"]]
        last_ok = max((_parse(r["ts"]) for r in ok), default=None)
        last_bad = max((_parse(r["ts"]) for r in bad), default=None)
        lat = [r["latency_ms"] for r in window if r["ok"]]
        errors = sum(not r["ok"] for r in window)
        rate = errors / len(window) if window else None
        stale_for = (now - last_ok).total_seconds() / 60 if last_ok else None
        if not window:
            status = "NO_DATA"
        elif last_ok is None:
            status = "FAILED"
        elif stale_for > cfg.provider.stale_minutes:
            status = "STALE"
        elif rate > cfg.provider.max_error_rate or (_pct(lat, 95) or 0) > cfg.provider.max_p95_latency_ms:
            status = "WARNING"
        else:
            status = "OK"
        out[prov] = {
            "status": status, "requests": len(window), "errors": errors,
            "error_rate": None if rate is None else round(rate, 3),
            "latency_p50_ms": _pct(lat, 50), "latency_p95_ms": _pct(lat, 95),
            "last_success": last_ok.isoformat() if last_ok else None,
            "last_failure": last_bad.isoformat() if last_bad else None,
            "minutes_since_success": None if stale_for is None else round(stale_for, 1),
            "error_kinds": dict(Counter(r["error"] for r in window if not r["ok"])),
        }  # fmt: skip
    return out


# -------------------------------------------------------------------------------- heartbeats
def heartbeat_gaps(beats: list[dict], now: datetime, cfg: MlopsConfig) -> dict[str, dict]:
    """Scheduler liveness. A gap longer than `max_gap_minutes` between two beats (or since the last
    one) means the task did not run -- machine off, task disabled, or crashed before the beat."""
    limit = timedelta(minutes=cfg.heartbeat.max_gap_minutes)
    out = {}
    for name in cfg.heartbeat.names:
        ts = sorted(_parse(b["ts"]) for b in beats if b["name"] == name)
        if not ts:
            out[name] = {"status": "NO_DATA", "beats": 0}
            continue
        gaps = [(a, b) for a, b in zip(ts, ts[1:], strict=False) if b - a > limit]
        since_last = now - ts[-1]
        out[name] = {
            "status": "STALE" if since_last > limit else ("WARNING" if gaps else "OK"),
            "beats": len(ts), "last_beat": ts[-1].isoformat(),
            "minutes_since_last": round(since_last.total_seconds() / 60, 1),
            "gaps": [
                {"from": a.isoformat(), "to": b.isoformat(), "hours": round(
                    (b - a).total_seconds() / 3600, 1)
                }
                for a, b in gaps[-10:]
            ],
            "n_gaps": len(gaps),
        }  # fmt: skip
    return out


# --------------------------------------------------------------------------------- LLM health
def llm_health(calls: list[dict], response_sizes: list[int], now: datetime, cfg: MlopsConfig) -> dict:
    """From LLMCallRecord rows (calls.jsonl): success/failure, 429 / 5xx / schema failure rates,
    latency, tokens, estimated cost, mean response size. Cost is the ESTIMATE recorded per call."""
    n = len(calls)
    if n == 0:
        return {"status": "NO_DATA"}
    kinds = Counter(c.get("error_kind") for c in calls if c["status"] != "ok")
    failed = sum(c["status"] != "ok" for c in calls)
    lat = [c["latency_ms"] for c in calls if c.get("latency_ms") is not None]
    cost = sum(c["cost_usd"] for c in calls if c.get("cost_usd") is not None)
    unknown_cost = sum(c.get("cost_usd") is None for c in calls)
    day = [c for c in calls if _parse(c["generated_at"]) >= now - timedelta(days=1)]
    day_cost = sum(c["cost_usd"] for c in day if c.get("cost_usd") is not None)
    rates = {
        "failure_rate": failed / n, "rate_limit_rate": kinds.get("rate_limit", 0) / n,
        "server_error_rate": kinds.get("server", 0) / n,
        "schema_failure_rate": (
            kinds.get("schema", 0) + sum(c["status"] == "malformed_json_exhausted" for c in calls)
        )
        / n,
    }  # fmt: skip
    t = cfg.llm
    breaches = [
        k
        for k, lim in (
            ("failure_rate", t.max_failure_rate),
            ("rate_limit_rate", t.max_rate_limit_rate),
            ("server_error_rate", t.max_server_error_rate),
            ("schema_failure_rate", t.max_schema_failure_rate),
        )
        if rates[k] > lim
    ]
    if (_pct(lat, 95) or 0) > t.max_p95_latency_ms:
        breaches.append("latency_p95")
    if day_cost > t.max_cost_usd_per_day:
        breaches.append("cost_per_day")
    return {
        "status": "WARNING" if breaches else "OK", "breaches": breaches, "calls": n,
        "successful": n - failed, "failed": failed, **{k: round(v, 4) for k, v in rates.items()},
        "latency_p50_ms": _pct(lat, 50), "latency_p95_ms": _pct(lat, 95),
        "tokens_total": sum(c.get("total_tokens") or 0 for c in calls),
        "estimated_cost_usd": round(cost, 6), "cost_last_24h_usd": round(day_cost, 6),
        "calls_without_cost_estimate": unknown_cost,
        "avg_response_chars": round(float(np.mean(response_sizes)), 1) if response_sizes else None,
        "by_provider": {
            p: dict(Counter(c["status"] for c in calls if c["provider"] == p))
            for p in sorted({c["provider"] for c in calls})
        },
    }  # fmt: skip


# ----------------------------------------------------------------- prediction distribution
def _entropy(p: np.ndarray) -> np.ndarray:
    q = np.clip(p, 1e-12, 1.0)
    return -(q * np.log(q)).sum(axis=1)


def psi(reference: np.ndarray, current: np.ndarray, bins: int = 10) -> float:
    """Population stability index of two samples on shared quantile bins of the reference."""
    edges = np.unique(np.quantile(reference, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:
        return 0.0
    edges[0], edges[-1] = -np.inf, np.inf
    r = np.histogram(reference, edges)[0] / len(reference)
    c = np.histogram(current, edges)[0] / len(current)
    r, c = np.clip(r, 1e-4, None), np.clip(c, 1e-4, None)
    return float(((c - r) * np.log(c / r)).sum())


def prediction_distribution(
    probs_by_model: dict[str, np.ndarray], reference_by_model: dict[str, np.ndarray], cfg: MlopsConfig
) -> dict[str, dict]:
    out = {}
    for model, p in sorted(probs_by_model.items()):
        n = len(p)
        if n < cfg.drift.min_samples:
            out[model] = {"status": "INSUFFICIENT_DATA", "n": n, "min_samples": cfg.drift.min_samples}
            continue
        row = {
            "n": n, "mean_probs": [round(float(x), 4) for x in p.mean(axis=0)],
            "mean_top_probability": round(float(p.max(axis=1).mean()), 4),
            "mean_entropy": round(float(_entropy(p).mean()), 4),
        }  # fmt: skip
        ref = reference_by_model.get(model)
        if ref is None or len(ref) < cfg.drift.min_samples:
            out[model] = {**row, "status": "NO_REFERENCE"}
            continue
        value = psi(ref.max(axis=1), p.max(axis=1))
        out[model] = {**row, "status": "WARNING" if value > cfg.drift.psi_threshold else "OK",
                      "psi_top_probability": round(value, 4), "reference_n": len(ref)}  # fmt: skip
    return out


# ------------------------------------------------------------- features: missingness and drift
def feature_health(
    snapshot_features: list[dict[str, float | None]],
    training_features: list[dict[str, float | None]],
    cfg: MlopsConfig,
) -> dict:
    n = len(snapshot_features)
    if n < cfg.drift.min_samples:
        return {"status": "INSUFFICIENT_DATA", "snapshots": n, "min_samples": cfg.drift.min_samples}
    names = sorted({k for r in snapshot_features for k in r if not k.endswith("_available")})
    missing_total = values_total = 0
    shifts = {}
    for name in names:
        cur = [r.get(name) for r in snapshot_features]
        missing_total += sum(v is None for v in cur)
        values_total += len(cur)
        cur_v = [v for v in cur if v is not None]
        tr = [r[name] for r in training_features if r.get(name) is not None]
        if len(cur_v) >= 1 and len(tr) >= 10 and np.std(tr) > 0:
            shifts[name] = float((np.mean(cur_v) - np.mean(tr)) / np.std(tr))
    share = missing_total / values_total if values_total else 0.0
    drifting = {k: round(v, 2) for k, v in shifts.items() if abs(v) > cfg.drift.feature_z_threshold}
    return {
        "status": "WARNING" if (drifting or share > cfg.drift.max_missing_share) else "OK",
        "snapshots": n, "features_checked": len(shifts), "missing_share": round(share, 4),
        "drifting_features": drifting, "z_threshold": cfg.drift.feature_z_threshold,
    }  # fmt: skip


# ------------------------------------------------------------------ settled metric drift
def metric_drift(
    settled: list[tuple[np.ndarray, int]], reference_log_loss: float | None, cfg: MlopsConfig
) -> dict:
    """`settled`: (probability vector H/D/A, outcome index). Compares log loss with the reference."""
    n = len(settled)
    if n < cfg.metric_drift.min_settled:
        return {"status": "INSUFFICIENT_DATA", "settled": n, "min_settled": cfg.metric_drift.min_settled}
    ll = float(np.mean([-math.log(max(float(p[y]), 1e-12)) for p, y in settled]))
    if reference_log_loss is None:
        return {"status": "NO_REFERENCE", "settled": n, "log_loss": round(ll, 4)}
    delta = ll - reference_log_loss
    return {
        "status": "WARNING" if delta > cfg.metric_drift.max_log_loss_increase else "OK",
        "settled": n, "log_loss": round(ll, 4), "reference_log_loss": round(reference_log_loss, 4),
        "delta": round(delta, 4),
    }  # fmt: skip


# --------------------------------------------------------------------------- artifact readers
def read_llm_calls(root: Path) -> tuple[list[dict], list[int]]:
    """Every LLMCallRecord under artifacts/llm_runs and artifacts/snapshots, plus raw response sizes."""
    calls, sizes = [], []
    for p in list(Path(root).glob("artifacts/llm_runs/*/calls.jsonl")) + list(
        Path(root).glob("artifacts/snapshots/*/*/llm_calls.jsonl")
    ):
        calls += [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    for p in list(Path(root).glob("artifacts/llm_runs/*/responses.jsonl")) + list(
        Path(root).glob("artifacts/snapshots/*/*/llm_responses.jsonl")
    ):
        sizes += [
            len(json.loads(x).get("text", ""))
            for x in p.read_text(encoding="utf-8").splitlines()
            if x.strip()
        ]
    return calls, sizes


def read_stage_artifacts(root: Path):
    """-> (snapshots, probs_by_model) of every locked S13 stage."""
    snaps, probs = [], defaultdict(list)
    for sp in Path(root).glob(SNAP_GLOB):
        if not (sp.parent / "LOCK.json").exists():
            continue
        snaps.append(json.loads(sp.read_text(encoding="utf-8")))
        for line in (sp.parent / "predictions.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                probs[r["model_id"]].append([r["p_home"], r["p_draw"], r["p_away"]])
    return snaps, {k: np.array(v) for k, v in probs.items()}


def now_utc() -> datetime:
    return datetime.now(UTC)


def read_provider_calls() -> list[dict]:
    return read_rows("provider_calls.jsonl")
