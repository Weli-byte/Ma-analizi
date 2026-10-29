"""Probability consistency audit of the published match-intelligence artifacts (ADR 0088).

Every `artifacts/markets/<fixture>/intel-*.json` is checked against invariants that must hold for any forecast built from one score grid: probabilities lie in [0, 1]; over + under = 1 on
every line; BTTS yes + no = 1; the three 1X2 probabilities (model and headline) sum to 1; double chance, draw-no-bet and the handicap lines follow from the headline; the listed scorelines are
sorted and sum to at most 1; the total-goals distribution (when stored) sums to 1 and gives the same over 2.5 as the stored line. The audit reports counts by invariant and by model version and
changes nothing: a violation is a finding, never silently repaired.
"""

import json
from pathlib import Path

TOL = 2e-3          # the artifacts store probabilities rounded to four decimals


def check(art: dict) -> list[str]:
    i = art["intelligence"]
    bad: list[str] = []
    res, goals = i["result"], i["goals"]

    def unit(name: str, p) -> None:
        if p is None or not (-1e-9 <= p <= 1 + 1e-9):
            bad.append(f"out_of_range:{name}")

    for block in ("model_probs", "headline_probs"):
        v = [res[block][k] for k in ("home", "draw", "away")]
        for k, p in zip(("home", "draw", "away"), v, strict=True):
            unit(f"{block}.{k}", p)
        if abs(sum(v) - 1) > TOL:
            bad.append(f"sum_not_one:{block}")
    mk = res.get("market_probs_devig")
    if mk and abs(sum(mk.values()) - 1) > TOL:
        bad.append("sum_not_one:market_probs_devig")
    h = res["headline_probs"]
    dc = res["double_chance"]
    for name, val in (("1X", h["home"] + h["draw"]), ("12", h["home"] + h["away"]), ("X2", h["draw"] + h["away"])):
        unit(f"double_chance.{name}", dc[name])
        if abs(dc[name] - val) > TOL:
            bad.append(f"double_chance_mismatch:{name}")
    dnb = res["draw_no_bet"]
    if h["home"] + h["away"] > 0 and abs(dnb["home"] - h["home"] / (h["home"] + h["away"])) > TOL:
        bad.append("draw_no_bet_mismatch")
    for line in goals["over_under"]:
        unit(f"over_under.{line['line']}.over", line["over"])
        if abs(line["over"] + line["under"] - 1) > TOL:
            bad.append(f"over_under_not_one:{line['line']}")
    overs = [x["over"] for x in sorted(goals["over_under"], key=lambda x: x["line"])]
    if any(b > a + TOL for a, b in zip(overs, overs[1:], strict=False)):
        bad.append("over_not_decreasing_in_line")
    unit("btts_yes", goals["btts_yes"])
    if abs(goals["btts_yes"] + goals["btts_no"] - 1) > TOL:
        bad.append("btts_not_one")
    dist = goals.get("total_goals_distribution")
    if dist:
        if abs(sum(dist) - 1) > TOL:
            bad.append("total_distribution_not_one")
        o25 = next((x["over"] for x in goals["over_under"] if x["line"] == 2.5), None)
        if o25 is not None and abs((1 - sum(dist[:3])) - o25) > TOL:
            bad.append("over_2_5_differs_from_total_distribution")
    sc = [s["p"] for s in i["scorelines"]]
    if sc != sorted(sc, reverse=True):
        bad.append("scorelines_not_sorted")
    if sum(sc) > 1 + TOL:
        bad.append("scorelines_sum_above_one")
    if i["scorelines"] and i["most_likely_score"] != i["scorelines"][0]["score"]:
        bad.append("most_likely_not_first")
    return bad


def audit(markets_root) -> dict:
    root = Path(markets_root) / "artifacts" / "markets"
    out = {"artifacts": 0, "with_violation": 0, "by_invariant": {}, "by_model_version": {}, "with_total_distribution": 0, "unreadable": 0}
    for path in sorted(root.glob("*__*__*/intel-*.json")) if root.exists() else []:
        try:
            art = json.loads(path.read_text(encoding="utf-8"))
            bad = check(art)
        except (KeyError, ValueError, TypeError):
            out["unreadable"] += 1
            continue
        out["artifacts"] += 1
        out["with_total_distribution"] += 1 if art["intelligence"]["goals"].get("total_goals_distribution") else 0
        ver = art.get("model_version", "?")
        slot = out["by_model_version"].setdefault(ver, {"artifacts": 0, "with_violation": 0})
        slot["artifacts"] += 1
        if bad:
            out["with_violation"] += 1
            slot["with_violation"] += 1
            for b in set(bad):
                out["by_invariant"][b] = out["by_invariant"].get(b, 0) + 1
    return out
