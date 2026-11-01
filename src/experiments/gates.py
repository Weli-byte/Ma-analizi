"""Leakage gate and promotion gate (ADR 0096).

Leakage gate (must PASS before a comparison is recorded):
- out-of-fold rows: every row's model was fitted at or before the row's kick-off (`fit_as_of`), the evaluation seasons are disjoint from the fit seasons, no final-test season is present;
- live forecasts: the information cutoff is before kick-off and every odds observation is at or before the cutoff (closing or later odds never enter);
- calibration / test overlap: no season is both a calibration (fit / select) window and the test window.
Promotion gate: primary-metric improvement (the Bonferroni interval of candidate minus champion wholly below -min_practical_effect), no severe league regression (a league with at least
MIN_LEAGUE_N matches whose interval is wholly above 0), calibration non-regression (ECE at most 0.005 worse), coverage at least 95 % of the champion's fixtures, no paid call (cost SLO,
the owner's budget is 0), the leakage gate passed, and the result reproduced (same result hash on a rerun).
"""

from datetime import datetime

FINAL_TEST = ("2024-25", "2025-26")
MIN_LEAGUE_N = 100
ECE_TOLERANCE = 0.005
MIN_COVERAGE = 0.95


def _utc(t: str) -> datetime:
    return datetime.fromisoformat(t.replace("Z", "+00:00"))


def leakage_oof(rows: list[dict], fit_seasons: tuple[str, ...], test_seasons: tuple[str, ...]) -> dict:
    problems = []
    if set(fit_seasons) & set(test_seasons):
        problems.append("fit and test seasons overlap")
    if {r["season"] for r in rows} & set(FINAL_TEST):
        problems.append("a final-test season is present")
    late = [r for r in rows if r.get("fit_as_of") and _utc(r["fit_as_of"]) > _utc(r["kickoff"])]
    if late:
        problems.append(f"{len(late)} rows predicted by a model fitted after their kick-off")
    missing = sum(1 for r in rows if not r.get("fit_as_of"))
    return {"status": "FAIL" if problems else "PASS", "problems": problems, "rows": len(rows), "rows_without_fit_time": missing}


def leakage_live(artifacts: dict[str, list[dict]]) -> dict:
    """The scorer only uses forecasts whose cutoff is before kick-off; post-kick-off ones are counted, never scored."""
    problems, checked, after_kickoff = [], 0, 0
    for key, arts in artifacts.items():
        for a in arts:
            checked += 1
            cut, kick = _utc(a["information_cutoff"]), _utc(a["kickoff_utc"])
            after_kickoff += cut >= kick
            src = a.get("market_source") or None
            if src and _utc(src["observed_at"]) > cut:
                problems.append(f"{key}: odds observed after the forecast cutoff")
    return {"status": "FAIL" if problems else "PASS", "problems": problems[:20], "forecasts_checked": checked, "post_kickoff_forecasts_not_scored": after_kickoff}


def promotion(delta: dict | None, league_deltas: dict[str, dict], ece_delta: float | None, coverage: float | None, paid_calls: int, leakage: str, reproduced: bool,
              min_effect: float) -> dict:  # fmt: skip
    checks = {
        "primary_metric": bool(delta) and delta["interval"][1] < -min_effect,
        "no_severe_league_regression": not any(v["n"] >= MIN_LEAGUE_N and v["interval"][0] > 0 for v in league_deltas.values()),
        "calibration_non_regression": ece_delta is not None and ece_delta <= ECE_TOLERANCE,
        "coverage": coverage is not None and coverage >= MIN_COVERAGE,
        "cost": paid_calls == 0,
        "leakage_gate": leakage == "PASS",
        "reproduced": reproduced,
    }
    return {"checks": checks, "decision": "PROMOTION_CANDIDATE" if all(checks.values()) else ("NO_GAIN" if checks["leakage_gate"] else "BLOCKED_LEAKAGE")}
