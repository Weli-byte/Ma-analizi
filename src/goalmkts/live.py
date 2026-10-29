"""Live comparison of the goal-market forecasts with trivial baselines (ADR 0088).

Rows are the settled matches of the live record (`track_record.json`) joined with the last published artifact before kick-off (same rule as ADR 0048). Labels use the 90-minute score:
over 2.5 = home + away goals > 2, BTTS = both sides scored. Candidates per target: the published production forecast, the experimental hierarchical forecast (only where the artifact carries
it, not for neutral venues, the pool of the ADR 0073 gate), a constant prevalence from the historical development seasons (pooled over 10,712 rows of the league programme, fixed BEFORE looking at
a live row) and an expanding live prevalence (as of each match, earlier live matches only, Laplace prior). Accuracy of "always over / always under" is reported as the plan asks, it is not a
probability forecast. Paired differences use the project's block bootstrap over ISO weeks.
"""

import json
from datetime import datetime
from pathlib import Path

import numpy as np

from src.league_models import bench_metrics as bm
from src.markets.track import load_artifacts

EPS = 1e-12
HIST_OVER_RATE = 0.5347     # pooled over-2.5 rate of the development seasons (docs/league_models/s4c_exact_score.json, 10,712 matches)
HIST_BTTS_RATE = 0.5429
SEED = 20261029
TARGETS = ("over_2_5", "btts")
MIN_BLOCKS = 8     # a block bootstrap over fewer weekly blocks has no usable interval


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def labels(score: str) -> tuple[int, int]:
    h, a = (int(x) for x in score.split("-"))
    return int(h + a > 2), int(h > 0 and a > 0)


def build_rows(markets_root) -> tuple[list[dict], dict]:
    root = Path(markets_root)
    track = json.loads((root / "artifacts" / "markets" / "track_record.json").read_text(encoding="utf-8"))
    arts = load_artifacts(root)
    rep = {"settled": len(track["matches"]), "no_forecast_before_kickoff": 0, "used": 0, "with_experimental": 0}
    rows = []
    for m in track["matches"]:
        key = m["key"]
        cand = arts.get(key, [])
        if not cand:
            rep["no_forecast_before_kickoff"] += 1
            continue
        kickoff = _utc(cand[0]["kickoff_utc"])
        before = [a for a in cand if _utc(a["information_cutoff"]) < kickoff]
        if not before:
            rep["no_forecast_before_kickoff"] += 1
            continue
        art = max(before, key=lambda a: a["information_cutoff"])
        o, b = labels(m["score"])
        g = art["intelligence"]["goals"]
        ex = art.get("experimental")
        use_ex = bool(ex) and not ex.get("neutral_venue")
        row = {"key": key, "league": m["league"], "kickoff": kickoff, "o": o, "b": b, "p": {"over_2_5": next(x["over"] for x in g["over_under"] if x["line"] == 2.5), "btts": g["btts_yes"]}, "ex": None}
        if use_ex:
            row["ex"] = {"over_2_5": next(x["over"] for x in ex["over_under"] if x["line"] == 2.5), "btts": ex["btts_yes"]}
            rep["with_experimental"] += 1
        rows.append(row)
    rows.sort(key=lambda r: (r["kickoff"], r["key"]))
    rep["used"] = len(rows)
    return rows, rep


def _ll(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    return -np.log(np.maximum(np.where(y == 1, p, 1 - p), EPS))


def _brier(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    return (p - y) ** 2


def _summary(p: np.ndarray, y: np.ndarray) -> dict:
    return {"n": int(len(y)), "log_loss": float(_ll(p, y).mean()), "brier": float(_brier(p, y).mean()), "accuracy": float(((p >= 0.5) == (y == 1)).mean()), "mean_prediction": float(p.mean()), "observed_rate": float(y.mean())}


def _paired(a: np.ndarray, b: np.ndarray, blocks: list[str]) -> dict:
    d = bm.paired_block_bootstrap(a, b, blocks, 2000, SEED, 0.05)
    verdict = "TOO_FEW_WEEKLY_BLOCKS" if d.n_blocks < MIN_BLOCKS else "BETTER" if d.upper < 0 else "WORSE" if d.lower > 0 else "NO_DIFFERENCE_DEMONSTRATED"
    return {"delta": float(d.delta), "ci95": [float(d.lower), float(d.upper)], "blocks": int(d.n_blocks), "verdict": verdict}


def _expanding(y: np.ndarray) -> np.ndarray:
    """As-of prevalence: before match i only matches 0..i-1 are known; Laplace prior of two pseudo-matches at 0.5."""
    c = np.concatenate([[0.0], np.cumsum(y)[:-1]])
    n = np.arange(len(y), dtype=float)
    return (c + 1.0) / (n + 2.0)


def evaluate(rows: list[dict]) -> dict:
    out: dict = {"n": len(rows), "targets": {}, "leagues": {}}
    for r in rows:
        out["leagues"][r["league"]] = out["leagues"].get(r["league"], 0) + 1
    if len(rows) < 20:
        out["status"] = "TOO_FEW_ROWS"
        return out
    blocks_all = [bm.iso_week_block(r["kickoff"]) for r in rows]
    ex_idx = [i for i, r in enumerate(rows) if r["ex"] is not None]
    for t, key, hist in (("over_2_5", "o", HIST_OVER_RATE), ("btts", "b", HIST_BTTS_RATE)):
        y = np.array([r[key] for r in rows])
        cur = np.array([r["p"][t] for r in rows])
        const = np.full(len(rows), hist)
        live = _expanding(y.astype(float))
        entry = {
            "rate_live": float(y.mean()), "rate_history": hist, "always_over_or_yes_accuracy": float(y.mean()), "always_under_or_no_accuracy": float(1 - y.mean()),
            "all_rows": {"production": _summary(cur, y), "prevalence_history": _summary(const, y), "prevalence_live_expanding": _summary(live, y)},
            "paired_all_rows": {"production_vs_prevalence_history": _paired(_ll(cur, y), _ll(const, y), blocks_all), "production_vs_prevalence_live": _paired(_ll(cur, y), _ll(live, y), blocks_all)},
        }  # fmt: skip
        if len(ex_idx) >= 20:
            yy, cc, ee = y[ex_idx], cur[ex_idx], np.array([rows[i]["ex"][t] for i in ex_idx])
            bl = [blocks_all[i] for i in ex_idx]
            cst, lv = const[ex_idx], live[ex_idx]
            entry["experimental_rows"] = {"production": _summary(cc, yy), "experimental": _summary(ee, yy), "prevalence_history": _summary(cst, yy)}
            entry["paired_experimental_rows"] = {"experimental_vs_production": _paired(_ll(ee, yy), _ll(cc, yy), bl), "experimental_vs_prevalence_history": _paired(_ll(ee, yy), _ll(cst, yy), bl),
                                                 "production_vs_prevalence_history": _paired(_ll(cc, yy), _ll(cst, yy), bl)}  # fmt: skip
            entry["live_prevalence_on_experimental_rows"] = _summary(lv, yy)
        out["targets"][t] = entry
    out["n_experimental"] = len(ex_idx)
    return out
