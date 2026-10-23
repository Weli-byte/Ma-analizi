"""Sprint 6 (ADR 0068): result reconciliation and the prospective scorecard.

Results arrive as a separate append-only outcome. Scoring joins the stored pre-match record with that outcome; a prediction is never edited. Accuracy-style claims come
only from resolved, pre-match stored records. Prospective numbers are kept apart from the backtest (Sprint 5 holdout), and a small N never produces a claim."""

import json
from datetime import UTC, datetime, timedelta

import numpy as np

from src.league_models import bench_metrics as bm

from .records import OutcomeStore, ProspectiveStore

TARGET_FIELDS = {"1x2": ("p_home", "p_draw", "p_away"), "over_under": ("p_over25",), "btts": ("p_btts_yes",)}
MIN_N_FOR_CLAIM = 30


def labels(hg: int, ag: int) -> dict:
    return {"1x2": 0 if hg > ag else (1 if hg == ag else 2), "over_under": int(hg + ag > 2.5), "btts": int(hg > 0 and ag > 0)}


def fetch_outcomes(provider, codes: dict[str, str], season: str, store: ProspectiveStore, outcomes: OutcomeStore, now: datetime) -> dict:
    """Read finished results for the fixtures that have records. Read-only; a fixture that is not final yet is simply still pending."""
    wanted = {r["fixture_id"] for r in store.all() if datetime.fromisoformat(r["kickoff_utc"]) < now}
    have = outcomes.all()
    todo = wanted - set(have)
    rep = {"pending_before": len(todo), "recorded": 0, "conflicts": 0, "provider_errors": []}
    if not todo:
        return rep
    for code in codes:
        try:
            raws = provider.list_fixtures(code, season)
        except Exception as e:
            rep["provider_errors"].append({"competition": code, "error": type(e).__name__})
            continue
        for raw in raws:
            fid = f"fdorg-{raw.provider_fixture_id}"
            if fid in todo and raw.status_raw == "FT" and raw.home_goals is not None and raw.away_goals is not None:
                try:
                    if outcomes.put(fid, raw.home_goals, raw.away_goals, "football_data_org", None, now) == "CREATED":
                        rep["recorded"] += 1
                except Exception:
                    rep["conflicts"] += 1
    return rep


def _loss(target: str, p: np.ndarray, y: np.ndarray) -> np.ndarray:
    if target == "1x2":
        return -np.log(np.clip(p[np.arange(len(y)), y], 1e-15, 1.0))
    q = np.clip(p, 1e-15, 1 - 1e-15)
    return -(y * np.log(q) + (1 - y) * np.log(1 - q))


def _brier(target: str, p: np.ndarray, y: np.ndarray) -> np.ndarray:
    if target == "1x2":
        return ((p - np.eye(3)[y]) ** 2).sum(axis=1)
    return (p - y) ** 2


def pairs(records: list[dict], outcomes: dict[str, dict]) -> dict:
    """{(stage, target, candidate_family): [(candidate probs, global probs, label, kickoff, league)]} for resolved fixtures only."""
    by_key: dict[tuple, dict[str, dict]] = {}
    for r in records:
        by_key.setdefault((r["fixture_id"], r["stage"]), {})[r["family"]] = r
    out: dict[tuple, list] = {}
    for (fid, stage), fams in by_key.items():
        o = outcomes.get(fid)
        glob = fams.get("global")
        if o is None or glob is None:
            continue
        y = labels(o["home_goals"], o["away_goals"])
        for fam, rec in fams.items():
            if fam == "global":
                continue
            target = fam.split(":")[1]  # candidate families are stored as "<model>:<target>"
            fields = TARGET_FIELDS[target]
            if any(rec[f] is None or glob[f] is None for f in fields):
                continue
            pc, pg = np.array([rec[f] for f in fields], float), np.array([glob[f] for f in fields], float)
            out.setdefault((stage, target, fam), []).append((pc if target == "1x2" else pc[0], pg if target == "1x2" else pg[0], y[target], rec["kickoff_utc"], rec["league_id"]))
    return out


def scorecard(records: list[dict], outcomes: dict[str, dict], now: datetime, backtest_note: dict | None = None) -> dict:
    kicked = [r for r in records if datetime.fromisoformat(r["kickoff_utc"]) <= now]
    pend = [r for r in kicked if r["fixture_id"] not in outcomes]
    sc: dict = {
        "generated_at_utc": now.isoformat(), "kind": "PROSPECTIVE (shadow, pre-match records written before kick-off); never mixed with the backtest",
        "live_verified": len(records) > 0, "live_verified_meaning": "at least one real pre-match record was stored by the shadow runner (it says nothing about accuracy)",
        "records_total": len(records), "fixtures_with_records": len({r["fixture_id"] for r in records}),
        "records_kickoff_range_utc": [min((r["kickoff_utc"] for r in records), default=None), max((r["kickoff_utc"] for r in records), default=None)],
        "kicked_off": len({r["fixture_id"] for r in kicked}), "resolved_fixtures": len({r["fixture_id"] for r in records if r["fixture_id"] in outcomes}),
        "pending_outcomes": len({r["fixture_id"] for r in pend}), "backtest_reference": backtest_note or "see s5_holdout_report.json (BACKTEST, a different protocol and fixture set)",
        "pairs": [], "caution": f"fewer than {MIN_N_FOR_CLAIM} resolved pairs: no claim of improvement or decline is made",
    }  # fmt: skip
    for (stage, target, fam), rows in sorted(pairs(records, outcomes).items()):
        pc = np.array([x[0] for x in rows])
        pg = np.array([x[1] for x in rows])
        y = np.array([x[2] for x in rows])
        lc, lg = _loss(target, pc, y), _loss(target, pg, y)
        entry = {"stage": stage, "target": target, "candidate": fam, "resolved_pairs": len(rows), "kickoff_range_utc": [min(x[3] for x in rows), max(x[3] for x in rows)],
                 "candidate_log_loss": round(float(lc.mean()), 5), "global_log_loss": round(float(lg.mean()), 5), "candidate_brier": round(float(_brier(target, pc, y).mean()), 5),
                 "global_brier": round(float(_brier(target, pg, y).mean()), 5), "delta_log_loss_candidate_minus_global": round(float(lc.mean() - lg.mean()), 5)}  # fmt: skip
        if len(rows) >= MIN_N_FOR_CLAIM:
            blocks = [bm.iso_week_block(datetime.fromisoformat(x[3])) for x in rows]
            d = bm.paired_block_bootstrap(lc, lg, blocks, 2000, 20261024, 0.05)
            entry["ci95"] = [round(d.lower, 5), round(d.upper, 5)]
            entry["verdict"] = "CANDIDATE_BETTER" if d.upper < 0 else ("CANDIDATE_WORSE" if d.lower > 0 else "NO_DIFFERENCE_DEMONSTRATED")
        else:
            entry["verdict"] = "TOO_FEW_PAIRS_NO_CLAIM"
        sc["pairs"].append(entry)
    return sc


def score_scorecard(forecasts: list[dict], outcomes: dict[str, dict], now: datetime) -> dict:
    """Exact-score and expected-goals scorecard of the stored score forecasts (ADR 0071), joined with the append-only outcomes. Per (family): log score of the realised scoreline,
    top-1 / top-5 hit rate and the mean absolute error of the expected goals. Scorelines above 6 goals for a side are outside the stored grid: counted, not scored. Prospective only."""
    by: dict[str, list] = {}
    for f in forecasts:
        fixture_id, stage, version, family = f["key"].split("|", 3)
        o = outcomes.get(fixture_id)
        if o is None or "score_grid_0_6" not in f:
            continue
        by.setdefault(f"{stage}|{family}", []).append((f, o))
    out = {"generated_at_utc": now.isoformat(), "kind": "PROSPECTIVE exact-score / expected-goals scorecard; never mixed with the backtest", "forecasts_total": len(forecasts), "families": [],
           "caution": f"fewer than {MIN_N_FOR_CLAIM} resolved forecasts: no claim is made"}  # fmt: skip
    for key, rows in sorted(by.items()):
        stage, family = key.split("|", 1)
        inside = [(f, o) for f, o in rows if o["home_goals"] <= 6 and o["away_goals"] <= 6]
        p = np.array([f["score_grid_0_6"][o["home_goals"]][o["away_goals"]] for f, o in inside])
        top1 = np.array([f["top_scores"][0][0] == f"{o['home_goals']}-{o['away_goals']}" for f, o in inside])
        top5 = np.array([any(s == f"{o['home_goals']}-{o['away_goals']}" for s, _ in f["top_scores"]) for f, o in inside])
        mae_h = np.array([abs(f["expected_goals_home"] - o["home_goals"]) for f, o in rows])
        mae_a = np.array([abs(f["expected_goals_away"] - o["away_goals"]) for f, o in rows])
        out["families"].append({"stage": stage, "family": family, "resolved": len(rows), "outside_stored_grid": len(rows) - len(inside),
                                "log_score": None if not len(inside) else round(float(np.mean(-np.log(np.clip(p, 1e-12, 1.0)))), 5),
                                "top1_hit_rate": None if not len(inside) else round(float(top1.mean()), 4), "top5_hit_rate": None if not len(inside) else round(float(top5.mean()), 4),
                                "expected_goals_mae_home": round(float(mae_h.mean()), 4), "expected_goals_mae_away": round(float(mae_a.mean()), 4),
                                "verdict": "TOO_FEW_RESOLVED_NO_CLAIM" if len(rows) < MIN_N_FOR_CLAIM else "REPORTED_NOT_COMPARED"})  # fmt: skip
    return out


def lag_report(records: list[dict], outcomes: dict[str, dict], now: datetime, grace_hours: float) -> dict:
    """Pending is not failure: a match is only 'late' once kick-off + grace has passed."""
    late = {}
    for r in records:
        k = datetime.fromisoformat(r["kickoff_utc"])
        if r["fixture_id"] not in outcomes and now > k + timedelta(hours=grace_hours):
            late[r["fixture_id"]] = round((now - k).total_seconds() / 3600, 1)
    return {"late_fixtures": len(late), "max_lag_hours": max(late.values(), default=0.0), "lags_hours": late}


def utc_now() -> datetime:
    return datetime.now(UTC)


def dump(path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8", newline="\n")
