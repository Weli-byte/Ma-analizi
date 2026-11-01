"""Selective prediction: coverage levels fixed in advance, thresholds chosen on validation history, scored once on later data (ADR 0095).

Coverage levels: 100 / 80 / 60 / 40 % (pre-registered). The confidence statistic is the market model's own top 1X2 probability. The threshold of a level is the quantile of that
statistic on the out-of-fold predictions of the FIT and SELECT seasons (2020-21 to 2022-23, ADR 0089); it is then applied unchanged to (a) the untouched 2023-24 out-of-fold season and (b) the
live prospective record. At each level: realised coverage, accuracy with a Wilson interval, log loss, error rate (risk). An abstained match is never dropped from the denominator of the
coverage: coverage = answered / all, and the all-matches accuracy is always shown next to the selected one.
"""

import math

import numpy as np

from src.markets.track import wilson

LEVELS = (1.0, 0.8, 0.6, 0.4)
HISTORY_SEASONS = ("2020-21", "2021-22", "2022-23")
TEST_SEASON = "2023-24"
MIN_TIER_EVIDENCE = 50


def thresholds(history_top: list[float]) -> dict[str, float]:
    """Top-probability cutoff of every level: the matches at or above it are the most confident `level` share of the history."""
    x = np.asarray(history_top, dtype=float)
    return {f"{int(lv * 100)}": (0.0 if lv >= 1.0 else float(np.quantile(x, 1 - lv))) for lv in LEVELS}


def curve(top_p: list[float], probs: list[list[float]], y: list[int], cuts: dict[str, float]) -> list[dict]:
    n = len(y)
    out = []
    for name, cut in cuts.items():
        idx = [i for i in range(n) if top_p[i] >= cut]
        k = len(idx)
        hits = sum(1 for i in idx if max(range(3), key=lambda j: probs[i][j]) == y[i])
        ll = float(np.mean([-math.log(max(probs[i][y[i]], 1e-12)) for i in idx])) if idx else None
        lo, hi = wilson(hits, k) if k else (None, None)
        out.append({"level": name, "threshold": round(cut, 4), "answered": k, "coverage": round(k / n, 4) if n else None, "accuracy": round(hits / k, 4) if k else None,
                    "accuracy_ci95": [round(lo, 4), round(hi, 4)] if k else None, "risk": round(1 - hits / k, 4) if k else None, "log_loss": round(ll, 4) if ll is not None else None})  # fmt: skip
    return out


def bucket(top_p: float, cuts: dict[str, float]) -> str:
    """The most selective pre-registered level the forecast still belongs to ("40" = among the 40 % most confident), or "abstain" below the 80 % level."""
    for name in ("40", "60", "80"):
        if top_p >= cuts[name]:
            return name
    return "abstain"
