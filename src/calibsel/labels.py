"""Prospective reliability of the confidence labels shown to users (ADR 0089).

The product tags each tip `strong lean` (stated probability >= 0.65), `lean` (>= 0.55) or `toss-up` (`src/markets/predict.py`). The plan allows a "high confidence" label only with prospective
evidence. For every settled match of the live record, the tips of the last artifact published before kick-off are scored against the 90-minute result (markets that the score alone can judge:
match result, double chance, over/under 2.5, BTTS). Per label and per market: number of tips, mean stated probability, hit rate with a Wilson interval. A label is reliable when its interval
contains the mean stated probability; above it means the label understates, below it overstates.
"""

import json
from datetime import datetime
from pathlib import Path

from src.markets.track import load_artifacts, wilson

LABELS = ("strong lean", "lean", "toss-up")
JUDGED = ("match_result", "double_chance", "goals_2_5", "btts")


def tip_true(tip: dict, score: str) -> bool | None:
    h, a = (int(x) for x in score.split("-"))
    out = 0 if h > a else 1 if h == a else 2
    market, pick = tip["market"], tip["pick"]
    if market == "match_result":
        return {"Home win": 0, "Draw": 1, "Away win": 2}[pick] == out
    if market == "double_chance":
        return out in {"1X": (0, 1), "12": (0, 2), "X2": (1, 2)}[pick]
    if market == "goals_2_5":
        return (h + a > 2) == pick.startswith("Over")
    if market == "btts":
        return (h > 0 and a > 0) == (pick == "Both teams score")
    return None


def reliability(markets_root) -> dict:
    root = Path(markets_root)
    track = json.loads((root / "artifacts" / "markets" / "track_record.json").read_text(encoding="utf-8"))
    arts = load_artifacts(root)
    cells: dict[tuple[str, str], list[tuple[float, bool]]] = {}
    matches = 0
    for m in track["matches"]:
        cand = arts.get(m["key"], [])
        if not cand:
            continue
        kickoff = datetime.fromisoformat(cand[0]["kickoff_utc"])
        before = [x for x in cand if datetime.fromisoformat(x["information_cutoff"]) < kickoff]
        if not before:
            continue
        art = max(before, key=lambda x: x["information_cutoff"])
        matches += 1
        for tip in art["intelligence"]["tips"]:
            if tip["market"] not in JUDGED:
                continue
            ok = tip_true(tip, m["score"])
            if ok is not None:
                cells.setdefault((tip["lean"], tip["market"]), []).append((tip["probability"], ok))
                cells.setdefault((tip["lean"], "ALL"), []).append((tip["probability"], ok))
    out = {"matches": matches, "cells": []}
    for (label, market), v in sorted(cells.items(), key=lambda kv: (LABELS.index(kv[0][0]), kv[0][1] != "ALL", kv[0][1])):
        n, hits = len(v), sum(1 for _, ok in v if ok)
        stated = sum(p for p, _ in v) / n
        lo, hi = wilson(hits, n)
        verdict = "CONSISTENT" if lo <= stated <= hi else ("UNDERSTATES" if stated < lo else "OVERSTATES")
        out["cells"].append({"label": label, "market": market, "n": n, "mean_stated": stated, "hit_rate": hits / n, "ci95": [lo, hi], "verdict": verdict})
    return out
