"""Bet suggestions from value rows (ADR 0039). Model-based research output, NOT a guarantee.

A suggestion needs an ELIGIBLE value row (all three 1X2 quotes `exact`, ADR 0030) whose edge AND expected
value reach the configured thresholds. Several models voting for the same selection are aggregated; the
stake hint is a capped fractional Kelly on the mean model probability. Confidence never exceeds MEDIUM:
no model here has been shown to beat the market on a large enough out-of-sample (the historical LLM
benchmark has n=20 per model and possible memorization), so every suggestion carries that caveat.
"""

from collections import defaultdict

SELECTIONS = ("H", "D", "A")

CAVEATS = (
    (
        "No model has beaten the market-implied probabilities on the 2022-24 validation "
        "(1520 matches; see docs/research_report.md)."
    ),
    "Expected value is an estimate, not a promise: every bet can lose; never stake money you cannot lose.",
    "Odds are research snapshots and may have moved; check the live price before acting.",
    "18+ only. If gambling stops being fun, get help (e.g. begambleaware.org).",
)


def kelly_stake_pct(p: float, odds: float, fraction: float, cap_pct: float) -> float:
    """Fractional Kelly as a percentage of bankroll, floored at 0 and capped."""
    full = (p * odds - 1.0) / (odds - 1.0)
    return round(max(0.0, min(cap_pct, 100.0 * fraction * full)), 2)


def make_picks(
    rows: list[dict], min_edge: float, min_ev: float, kelly_fraction: float, max_stake_pct: float
) -> list[dict]:
    by_fixture: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["status"] == "ELIGIBLE":
            by_fixture[r["fixture_id"]].append(r)
    picks = []
    for fid, rs in by_fixture.items():
        for i, sel in enumerate(SELECTIONS):
            voters = [r for r in rs if r["edge"][i] >= min_edge and r["ev"][i] >= min_ev]
            if not voters:
                continue
            best = max(voters, key=lambda r: r["ev"][i])
            odds = best["odds"][i]
            p = sum(r["model_probs"][i] for r in voters) / len(voters)
            ev_mean = p * odds - 1.0
            agree, total = len(voters), len(rs)
            picks.append(
                {
                    "fixture_id": fid,
                    "selection": sel,
                    "bookmaker": best["bookmaker"],
                    "odds": odds,
                    "observed_at": best["observed_at"],
                    "market_prob_devig": best["market_probs_devig"][i],
                    "mean_model_prob": round(p, 4),
                    "edge": round(p - best["market_probs_devig"][i], 4),
                    "ev_per_unit": round(ev_mean, 4),
                    "stake_hint_pct_of_bankroll": kelly_stake_pct(p, odds, kelly_fraction, max_stake_pct),
                    "models_agreeing": agree,
                    "models_evaluated": total,
                    "models": sorted(r["model_id"] for r in voters),
                    "confidence": "MEDIUM" if agree >= 2 and agree == total else "LOW",
                    "caveats": list(CAVEATS),
                }  # fmt: skip
            )
    return sorted(picks, key=lambda x: (-x["ev_per_unit"], x["fixture_id"]))
