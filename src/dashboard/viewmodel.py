"""Dashboard view model (S17, ADR 0034): everything the dashboard shows is read from REAL artifacts and
logs; there is no hard-coded number. A section with no data says so (`available: False`) instead of
showing a placeholder. Data cells carry an explicit status (OBSERVED / INFERRED / UNKNOWN / STALE /
FAILED, ADR 0030 production data contract): unknown is never shown as zero.

Every prediction row traces to prediction_id, model, provider, timestamps, data_version,
feature_version and (for LLMs) prompt_version.
"""

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

from src.config import config_dir_for, load_config
from src.data.teams import TeamDirectory
from src.evaluation.reliability import reliability_curve
from src.mlops import monitor
from src.mlops.oplog import read_rows

OUTCOME_INDEX = {"H": 0, "D": 1, "A": 2}


def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass  # a damaged line is skipped here; the ops report counts corrupt lines
    return out


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _model_class(model_id: str) -> str:
    if model_id.startswith("llm_"):
        return "LLM_REAL"
    from src.models import REGISTRY

    cls = REGISTRY.get(model_id)
    return cls.model_class if cls else "unknown"


def _provider(model_id: str) -> str | None:
    return model_id.split("_")[1] if model_id.startswith("llm_") else None


def _names(root: Path) -> dict[str, str]:
    d = TeamDirectory.load(config_dir_for(root) / "team_aliases.yaml")
    return {k: v["canonical_name"] for k, v in d.teams.items()}


def _match_key(home: str, away: str, kickoff_iso: str) -> str:
    return f"{home}|{away}|{kickoff_iso[:10]}"


# ----------------------------------------------------------------------------- matches / predictions
def _stage_dirs(root: Path):
    for snap in sorted(Path(root).glob("artifacts/snapshots/*/*/snapshot.json")):
        if (snap.parent / "LOCK.json").exists():
            yield snap.parent


def matches_and_predictions(root: Path, now: datetime) -> tuple[list[dict], list[dict], list[dict]]:
    names = _names(root)
    matches: dict[str, dict] = {}
    preds: list[dict] = []
    updates: list[dict] = []

    def match(home: str, away: str, kickoff: str, league: str | None = None) -> dict:
        m = matches.setdefault(
            _match_key(home, away, kickoff),
            {
                "key": _match_key(home, away, kickoff), "home_id": home, "away_id": away,
                "home": names.get(home, home), "away": names.get(away, away), "kickoff_utc": kickoff,
                "league": league, "stages": {}, "odds": {}, "injuries": "UNKNOWN", "lineups": "UNKNOWN",
            },
        )  # fmt: skip
        return m

    # S13 locked stages
    for d in _stage_dirs(root):
        snap = _json(d / "snapshot.json")
        lock = _json(d / "LOCK.json")
        m = match(snap["home_id"], snap["away_id"], snap["kickoff_utc"], snap.get("league_id"))
        m["stages"][snap["stage"]] = {
            "snapshot_hash": lock["snapshot_hash"], "generated_at": lock["generated_at"],
            "lateness_minutes": lock.get("lateness_minutes"),
        }  # fmt: skip
        av = snap.get("availability", {})
        m["injuries"] = av.get("injuries", {}).get("status", "UNKNOWN")
        m["lineups"] = av.get("lineups", {}).get("status", "UNKNOWN")
        calls = {c["provider"] + ":" + c["model"]: c for c in _jsonl(d / "llm_calls.jsonl")}
        for r in _jsonl(d / "predictions.jsonl"):
            call = next(
                (c for c in calls.values() if r["model_id"].startswith(f"llm_{c['provider']}_")), None
            )
            preds.append(_pred_row(r, m["key"], f"stage {snap['stage']}", call))
        for dl in _json(d / "deltas.json") or []:
            updates.append({**dl, "match": m["key"], "home": m["home"], "away": m["away"]})

    # ad-hoc real LLM forecast runs (src.llm.forecast)
    for run in sorted(Path(root).glob("artifacts/llm_runs/forecast_*")):
        snap = _json(run / "snapshot.json")
        if not snap:
            continue
        m = match(snap["home_team_id"], snap["away_team_id"], snap["kickoff_utc"], snap.get("league_id"))
        calls = _jsonl(run / "calls.jsonl")
        for r in _jsonl(run / "predictions.jsonl"):
            call = next((c for c in calls if r["model_id"].startswith(f"llm_{c['provider']}_")), None)
            preds.append(_pred_row(r, m["key"], f"forecast run {run.name}", call))

    # odds stores (local ESPN + cloud-collected exact quotes)
    from src.odds.store import OddsStore
    from src.odds.value import snapshots

    for base in ("odds", "odds_remote"):
        for meta_path in sorted((Path(root) / "artifacts" / base).glob("*/meta.json")):
            store = OddsStore(root, meta_path.parent.name, base)
            meta = store.meta()
            m = match(meta["home_id"], meta["away_id"], meta["kickoff_utc"], meta.get("league"))
            snaps = snapshots(store.quotes())
            complete = [(k, v) for k, v in snaps.items() if len(v) == 3]
            if not complete:
                continue
            (book, observed), q = max(complete, key=lambda kv: kv[0][1])
            source = next(iter(q.values())).source
            m["odds"].setdefault(source, []).append(
                {
                    "bookmaker": book,
                    "observed_at": observed.isoformat(),
                    "odds": [q[s].decimal_odds for s in ("H", "D", "A")],
                    "quality": next(iter(q.values())).timestamp_quality,
                    "latency_s": next(iter(q.values())).source_latency_s,
                }  # fmt: skip
            )
    for m in matches.values():  # keep the freshest snapshot per source and bookmaker
        for src, rows in m["odds"].items():
            best: dict[str, dict] = {}
            for r in rows:
                if r["bookmaker"] not in best or r["observed_at"] > best[r["bookmaker"]]["observed_at"]:
                    best[r["bookmaker"]] = r
            m["odds"][src] = sorted(best.values(), key=lambda r: r["bookmaker"])
    out = sorted(matches.values(), key=lambda m: m["kickoff_utc"])
    for m in out:
        m["upcoming"] = datetime.fromisoformat(m["kickoff_utc"]) > now
    return out, preds, updates


def _pred_row(r: dict, match_key: str, source: str, call: dict | None) -> dict:
    return {
        "match": match_key, "model_id": r["model_id"], "model_class": _model_class(r["model_id"]),
        "provider": _provider(r["model_id"]), "model_version": r["model_version"],
        "prompt_version": call["prompt_version"] if call else None,
        "p": [r["p_home"], r["p_draw"], r["p_away"]], "generated_at": r["generated_at"],
        "information_cutoff": r["information_cutoff"], "prediction_id": r["prediction_id"],
        "data_version": r["data_version"], "feature_version": r["feature_version"], "source": source,
        "status": r.get("status", "draft"),
    }  # fmt: skip


# --------------------------------------------------------------------------- benchmark evaluation
def _latest_benchmark_eval(root: Path) -> tuple[Path | None, dict | None]:
    runs = sorted(Path(root).glob("artifacts/llm_runs/benchmark_historical_*/evaluation.json"))
    return (runs[-1].parent, _json(runs[-1])) if runs else (None, None)


def models_and_calibration(root: Path) -> dict:
    run, ev = _latest_benchmark_eval(root)
    if ev is None:
        return {"available": False, "reason": "no historical benchmark evaluation on this machine"}
    rows, hist = [], []
    for r in ev["results"]:
        rows.append(
            {
                "model_id": r["model_id"],
                "model_class": r["model_class"],
                **r["metrics"],
                "ci": r.get("confidence_intervals", {}),
            }
        )
        hist.append({"model_id": r["model_id"], "by_league": r["by_league"], "by_season": r["by_season"]})
    cal = {}
    for model_id, c in ev["calibration"].items():
        cal[model_id] = {"skipped": True, "reason": c["reason"]} if c["skipped"] else {
            "skipped": False, "temperature": c["temperature"], "fit_rows": c["calibration_fit_rows"],
            "report_rows": c["report_rows"], "raw": c["raw_metrics"], "calibrated": c["calibrated_metrics"],
        }  # fmt: skip
    outcomes = _json(run / "outcomes.json") or {}
    preds = defaultdict(list)
    for r in _jsonl(run / "predictions.jsonl"):
        if r["fixture_id"] in outcomes:
            preds[r["model_id"]].append(
                (
                    [r["p_home"], r["p_draw"], r["p_away"]],
                    OUTCOME_INDEX["HDA"[outcomes[r["fixture_id"]]["outcome"]]],
                )
            )
    reliability = {
        m: {"n": len(v), "bins": reliability_curve(np.array([p for p, _ in v]), np.array([y for _, y in v]), 10)}
        for m, v in preds.items() if v
    }  # fmt: skip
    return {
        "available": True, "run": run.name, "models": rows, "history": hist, "calibration": cal,
        "reliability": reliability, "n_per_model": max((m["n"] for m in rows), default=0),
        "caveat": "HISTORICAL track: the LLMs may have memorized these results; small n; no winner is declared.",
        "non_llm": "no walk-forward artifacts on this machine" if not list(Path(root).glob("artifacts/walk_forward/*")) else "see walk_forward reports",
    }  # fmt: skip


# ----------------------------------------------------------------------------------- live / ops
def live_matches(root: Path) -> list[dict]:
    out = []
    for d in sorted(Path(root).glob("artifacts/live/*")):
        if d.name.startswith("_"):
            continue
        states = _jsonl(d / "states.jsonl")
        if not states:
            continue
        last = states[-1]
        preds = _jsonl(d / "predictions.jsonl")
        out.append(
            {
                "fixture_id": d.name,
                "state": last,
                "events": len(_jsonl(d / "events.jsonl")),
                "minute_status": "OBSERVED"
                if last.get("minute_source") == "reported"
                else ("INFERRED" if last.get("minute") is not None else "UNKNOWN"),
                "forecasts": [
                    {
                        k: p[k]
                        for k in (
                            "model_id",
                            "p_home",
                            "p_draw",
                            "p_away",
                            "match_minute",
                            "minute_source",
                            "calibration_status",
                            "prediction_id",
                            "generated_at",
                            "data_version",
                            "feature_version",
                        )
                    }
                    for p in preds[-5:]
                ],
            }  # fmt: skip
        )
    return out


def ops_sections(root: Path, now: datetime) -> dict:
    from src.mlops.report import build_report

    rep = build_report(root, now)
    calls, sizes = monitor.read_llm_calls(root)
    cfg = load_config("mlops", config_dir_for(root))
    llm = monitor.llm_health(calls, sizes, now, cfg)
    per_provider = {}
    for prov in sorted({c["provider"] for c in calls}):
        sub = [c for c in calls if c["provider"] == prov]
        h = monitor.llm_health(sub, [], now, cfg)
        per_provider[prov] = {
            k: h.get(k)
            for k in (
                "calls",
                "successful",
                "failed",
                "failure_rate",
                "rate_limit_rate",
                "latency_p50_ms",
                "latency_p95_ms",
                "tokens_total",
                "estimated_cost_usd",
                "calls_without_cost_estimate",
            )
        }
    credits = _json(Path(root) / "artifacts" / "odds_remote" / "_credits.json") or _json(
        Path(root) / "artifacts" / "odds" / "_credits.json"
    )
    from src.llm.pricing import load_price_table

    return {
        "data_freshness": rep["data_freshness"], "providers": rep["providers"], "heartbeats": rep["heartbeats"],
        "llm": llm, "llm_by_provider": per_provider, "checklist": rep["checklist"],
        "odds_api_credits": credits, "pricing_version": load_price_table().version,
        "ingested_results": len(read_rows("results.jsonl")),
    }  # fmt: skip


def build_viewmodel(root: Path, now: datetime) -> dict:
    root = Path(root)
    matches, preds, updates = matches_and_predictions(root, now)
    return {
        "generated_at": now.isoformat(),
        "matches": matches,
        "predictions": preds,
        "updates": updates,
        "models": models_and_calibration(root),
        "live": live_matches(root),
        "ops": ops_sections(root, now),
    }
