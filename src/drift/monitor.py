"""Input drift and performance drift of the product market model, shown apart (ADR 0092).

Rows: one per fixture, the last forecast published before kick-off (the live-record rule, ADR 0048). Input features are the MODEL's own outputs (model 1X2 home probability, over 2.5,
BTTS) plus the league mix, the share of forecasts with exact odds and the share flagged low-confidence. The reference window is fixed chronologically: fixtures kicking off in the first
REFERENCE_DAYS of the record; the current window is the last CURRENT_DAYS. Thresholds come from `docs/phase3/s14_thresholds.json` (validation history, `thresholds.py`).

Performance drift uses settled matches only (an unsettled match is not in any denominator; label latency is respected by construction): mean log loss of the first REFERENCE_N settled matches
against the last CURRENT_N, with a block bootstrap over ISO weeks at alpha 0.05 / 3 (three markets). With fewer settled matches than REFERENCE_N + CURRENT_N the answer is INSUFFICIENT_DATA:
no performance alarm is made up in a period without results.
"""

import json
import math
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from src.league_models import bench_metrics as bm
from src.markets.track import load_artifacts

from . import stats

REFERENCE_DAYS = 14
CURRENT_DAYS = 14
MIN_WEEKS = 4  # each performance window must span at least this many ISO weeks: fewer weekly blocks give no usable interval
REFERENCE_N = 100
CURRENT_N = 50
EPS = 1e-12


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def forecast_rows(markets_root) -> list[dict]:
    out = []
    for key, arts in load_artifacts(Path(markets_root)).items():
        kickoff = _utc(arts[0]["kickoff_utc"])
        before = [a for a in arts if _utc(a["information_cutoff"]) < kickoff]
        if not before:
            continue
        a = max(before, key=lambda x: x["information_cutoff"])
        i = a["intelligence"]
        out.append({"key": key, "league": a["league"], "kickoff": kickoff, "p_home": i["result"]["model_probs"]["home"],
                    "p_over_2_5": next(x["over"] for x in i["goals"]["over_under"] if x["line"] == 2.5), "p_btts": i["goals"]["btts_yes"],
                    "odds": bool(a.get("market_source")), "low_confidence": bool((i.get("data_quality") or {}).get("low_confidence"))})  # fmt: skip
    return sorted(out, key=lambda r: (r["kickoff"], r["key"]))


def input_drift(rows: list[dict], thresholds: dict, now: datetime) -> dict:
    if not rows:
        return {"status": "INSUFFICIENT_DATA", "reason": "no forecasts"}
    t0 = rows[0]["kickoff"]
    ref_end = t0 + timedelta(days=REFERENCE_DAYS)
    ref = [r for r in rows if r["kickoff"] < ref_end]
    cur_start = max(ref_end, now - timedelta(days=CURRENT_DAYS))  # the windows never overlap
    cur = [r for r in rows if cur_start <= r["kickoff"] <= now]
    out = {"reference": {"from": t0.isoformat(), "days": REFERENCE_DAYS, "n": len(ref)}, "current": {"from": cur_start.isoformat(), "days": CURRENT_DAYS, "to": now.isoformat(), "n": len(cur)}, "features": {}, "alarms": []}
    for name in ("p_home", "p_over_2_5", "p_btts"):
        m = stats.continuous([r[name] for r in ref], [r[name] for r in cur])
        if m["status"] == "MEASURED":
            m["over_threshold"] = [k for k in ("psi", "js", "ks") if m[k] > thresholds[f"{name}.{k}"]]
            if m["over_threshold"]:
                out["alarms"].append(f"input_drift:{name}")
        out["features"][name] = m
    m = stats.categorical([r["league"] for r in ref], [r["league"] for r in cur])
    if m["status"] == "MEASURED" and m["js"] > thresholds["league_mix.js"]:
        out["alarms"].append("input_drift:league_mix")
    out["features"]["league_mix"] = m
    for flag in ("odds", "low_confidence"):
        out["features"][f"{flag}_share"] = {"reference": round(sum(r[flag] for r in ref) / len(ref), 4) if ref else None, "current": round(sum(r[flag] for r in cur) / len(cur), 4) if cur else None}
    out["status"] = "ALARM" if out["alarms"] else ("INSUFFICIENT_DATA" if any(v.get("status") == "INSUFFICIENT_DATA" for v in out["features"].values() if isinstance(v, dict) and "status" in v) else "OK")
    return out


def settled_losses(markets_root) -> list[dict]:
    root = Path(markets_root)
    track = json.loads((root / "artifacts" / "markets" / "track_record.json").read_text(encoding="utf-8"))
    arts = load_artifacts(root)
    out = []
    for m in track["matches"]:
        cand = arts.get(m["key"], [])
        kick = _utc(cand[0]["kickoff_utc"]) if cand else None
        before = [a for a in cand if kick and _utc(a["information_cutoff"]) < kick]
        if not before:
            continue
        i = max(before, key=lambda x: x["information_cutoff"])["intelligence"]
        h, a = (int(x) for x in m["score"].split("-"))
        y = 0 if h > a else 1 if h == a else 2
        hp = [i["result"]["headline_probs"][k] for k in ("home", "draw", "away")]
        po = next(x["over"] for x in i["goals"]["over_under"] if x["line"] == 2.5)
        pb = i["goals"]["btts_yes"]
        o, b = int(h + a > 2), int(h > 0 and a > 0)
        out.append({"kickoff": kick, "1x2": -math.log(max(hp[y], EPS)), "over_2_5": -math.log(max(po if o else 1 - po, EPS)), "btts": -math.log(max(pb if b else 1 - pb, EPS))})
    return sorted(out, key=lambda r: r["kickoff"])


def performance_drift(rows: list[dict]) -> dict:
    n = len(rows)
    if n < REFERENCE_N + CURRENT_N:
        return {"status": "INSUFFICIENT_DATA", "settled": n, "needed": REFERENCE_N + CURRENT_N}
    ref, cur = rows[:REFERENCE_N], rows[-CURRENT_N:]
    weeks = (len({bm.iso_week_block(r["kickoff"]) for r in ref}), len({bm.iso_week_block(r["kickoff"]) for r in cur}))
    if min(weeks) < MIN_WEEKS:
        return {"status": "INSUFFICIENT_DATA", "settled": n, "needed": REFERENCE_N + CURRENT_N, "weeks": list(weeks), "min_weeks": MIN_WEEKS,
                "reason": f"the reference / current windows span {weeks[0]} / {weeks[1]} ISO weeks, at least {MIN_WEEKS} each are needed"}  # fmt: skip
    out = {"status": "OK", "settled": n, "reference_n": REFERENCE_N, "current_n": CURRENT_N, "alpha": 0.05 / 3, "markets": {}, "alarms": []}
    rng = np.random.default_rng(20261031)
    for t in ("1x2", "over_2_5", "btts"):
        a, b = np.array([r[t] for r in cur]), np.array([r[t] for r in ref])
        ba, bb = [bm.iso_week_block(r["kickoff"]) for r in cur], [bm.iso_week_block(r["kickoff"]) for r in ref]
        ka, kb = sorted(set(ba)), sorted(set(bb))
        draws = []
        for _ in range(2000):  # unpaired windows: resample whole weeks within each window
            sa = np.concatenate([a[[i for i, x in enumerate(ba) if x == w]] for w in rng.choice(ka, len(ka))])
            sb = np.concatenate([b[[i for i, x in enumerate(bb) if x == w]] for w in rng.choice(kb, len(kb))])
            draws.append(sa.mean() - sb.mean())
        lo, hi = np.percentile(draws, [100 * out["alpha"] / 2, 100 * (1 - out["alpha"] / 2)])
        out["markets"][t] = {"log_loss_reference": float(b.mean()), "log_loss_current": float(a.mean()), "delta": float(a.mean() - b.mean()), "interval": [float(lo), float(hi)],
                             "weeks": [len(kb), len(ka)]}  # fmt: skip
        if lo > 0:
            out["alarms"].append(f"performance_drift:{t}")
    if out["alarms"]:
        out["status"] = "ALARM"
    return out
