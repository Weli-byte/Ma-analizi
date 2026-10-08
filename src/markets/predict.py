"""Match intelligence for one fixture from fitted `MarketModels` (ADR 0041).

Output sections: result (1X2, double chance, draw-no-bet, handicap), scorelines (top correct scores),
goals (expected, totals, over/under, BTTS, team totals, clean sheets) and counts (corners, yellow cards,
shots on target: expected home/away/total and over/under lines). `tips` ranks the most probable side of each
market with a plain-language lean. Probabilities are model estimates; every section states data quality.
"""

import numpy as np

from .model import MODEL_VERSION, MarketModels, count_pmf, scoreline_matrix

LEAN_STRONG, LEAN = 0.65, 0.55


def devig(odds: tuple[float, float, float]) -> list[float]:
    inv = np.array([1.0 / o for o in odds])
    return (inv / inv.sum()).tolist()


def lean_label(p: float) -> str:
    return "strong lean" if p >= LEAN_STRONG else "lean" if p >= LEAN else "toss-up"


def _ou(pmf: np.ndarray, line: float) -> dict:
    over = float(pmf[int(np.floor(line)) + 1 :].sum())
    return {"line": line, "over": round(over, 4), "under": round(1.0 - over, 4)}


def _data_quality(mm: MarketModels, home: str, away: str) -> dict:
    tm = mm.goals.team_matches
    flags = []
    for side, t in (("home", home), ("away", away)):
        if t not in tm:
            flags.append(f"{side} team has no history: neutral strength used")
        elif tm[t] < 8:
            flags.append(f"{side} team has little recent history (weight {tm[t]:.1f})")
    return {
        "flags": flags,
        "history_weight": {home: round(tm.get(home, 0.0), 1), away: round(tm.get(away, 0.0), 1)},
    }


def intelligence(
    mm: MarketModels,
    home: str,
    away: str,
    league: str,
    market_odds: tuple[float, float, float] | None = None,
    blend_weight: float | None = None,
) -> dict:
    cfg = mm.cfg
    lh, la = mm.goals.lambdas(home, away, league)
    mat = scoreline_matrix(lh, la, mm.rho, cfg.max_goals)
    n = mat.shape[0]
    i, j = np.indices(mat.shape)
    p_h, p_d, p_a = (float(mat[i > j].sum()), float(mat[i == j].sum()), float(mat[i < j].sum()))
    model_1x2 = [p_h, p_d, p_a]
    w = cfg.market_blend_weight if blend_weight is None else blend_weight
    headline = model_1x2
    market = None
    if market_odds is not None:
        market = devig(market_odds)
        headline = [(1 - w) * m + w * k for m, k in zip(model_1x2, market, strict=True)]
    cons = mat
    if market is not None:  # market-consistent scorelines: rescale the three outcome regions to the headline
        cons = mat.copy()
        for mask, target, cur in (
            (i > j, headline[0], p_h),
            (i == j, headline[1], p_d),
            (i < j, headline[2], p_a),
        ):
            cons[mask] *= target / max(cur, 1e-12)
        cons /= cons.sum()
    totals = np.array([mat[(i + j) == t].sum() for t in range(2 * n - 1)])
    top = sorted(((cons[x, y], x, y) for x in range(n) for y in range(n)), reverse=True)[:6]
    btts = float(mat[1:, 1:].sum())
    goals_h, goals_a = mat.sum(axis=1), mat.sum(axis=0)
    result = {
        "model_probs": {"home": p_h, "draw": p_d, "away": p_a},
        "market_probs_devig": None if market is None else dict(zip(("home", "draw", "away"), market, strict=True)),
        "headline_probs": dict(zip(("home", "draw", "away"), headline, strict=True)),
        "headline_basis": "model" if market is None else f"blend: {w:.2f} market + {1 - w:.2f} model",
        "double_chance": {
            "1X": headline[0] + headline[1], "12": headline[0] + headline[2], "X2": headline[1] + headline[2],
        },
        "draw_no_bet": {"home": headline[0] / (headline[0] + headline[2]), "away": headline[2] / (headline[0] + headline[2])},
        "handicap_home_minus_1": float(mat[(i - j) >= 2].sum()),  # home wins by 2+
        "handicap_away_minus_1": float(mat[(j - i) >= 2].sum()),
    }  # fmt: skip
    goals = {
        "expected": {"home": round(lh, 3), "away": round(la, 3), "total": round(lh + la, 3)},
        "over_under": [_ou(totals, ln) for ln in cfg.over_under_goal_lines],
        "btts_yes": btts, "btts_no": 1.0 - btts,
        "home_over_1_5": float(goals_h[2:].sum()), "away_over_1_5": float(goals_a[2:].sum()),
        "home_clean_sheet": float(goals_a[0]), "away_clean_sheet": float(goals_h[0]),
        "total_goals_most_likely": int(np.argmax(totals)),
    }  # fmt: skip
    counts = {}
    for stat, table in mm.counts.items():
        ch, ca = table.lambdas(home, away, league)
        kmax = 60
        pmf = np.convolve(count_pmf(ch, table.alpha, kmax), count_pmf(ca, table.alpha, kmax))
        counts[stat] = {
            "expected": {"home": round(ch, 2), "away": round(ca, 2), "total": round(ch + ca, 2)},
            "most_likely_total": int(np.argmax(pmf)),
            "over_under": [_ou(pmf, ln) for ln in cfg.over_under_lines.get(stat, [])],
        }
    tips = _tips(result, goals, counts)
    return {
        "model_version": MODEL_VERSION,
        "scorelines": [{"score": f"{x}-{y}", "p": round(float(p), 4)} for p, x, y in top],
        "most_likely_score": f"{top[0][1]}-{top[0][2]}",
        "result": result, "goals": goals, "counts": counts, "tips": tips,
        "data_quality": _data_quality(mm, home, away), "rho": round(mm.rho, 4),
        "fitted_on": {"matches": mm.n_matches, "as_of": mm.as_of.isoformat()},
    }  # fmt: skip


def _tips(result: dict, goals: dict, counts: dict) -> list[dict]:
    tips = []

    def add(market: str, pick: str, p: float) -> None:
        tips.append({"market": market, "pick": pick, "probability": round(p, 4), "lean": lean_label(p)})

    hp = result["headline_probs"]
    side = max(hp, key=hp.get)
    add("match_result", {"home": "Home win", "draw": "Draw", "away": "Away win"}[side], hp[side])
    dc = result["double_chance"]
    k = max(dc, key=dc.get)
    add("double_chance", k, dc[k])
    ou25 = next(x for x in goals["over_under"] if x["line"] == 2.5)
    add(
        "goals_2_5",
        "Over 2.5 goals" if ou25["over"] >= 0.5 else "Under 2.5 goals",
        max(ou25["over"], ou25["under"]),
    )
    add(
        "btts",
        "Both teams score" if goals["btts_yes"] >= 0.5 else "Not both teams score",
        max(goals["btts_yes"], goals["btts_no"]),
    )
    for stat, c in counts.items():
        if not c["over_under"]:
            continue
        line = min(c["over_under"], key=lambda x: abs(x["over"] - 0.5))  # the line closest to a coin flip
        side_over = line["over"] >= 0.5
        add(
            f"{stat}_{line['line']}",
            f"{'Over' if side_over else 'Under'} {line['line']} {stat.replace('_', ' ')}",
            max(line["over"], line["under"]),
        )
    return sorted(tips, key=lambda t: -t["probability"])
