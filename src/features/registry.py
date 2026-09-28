"""Feature registry (feature contract): source, window, aggregation, available_at, leakage_rule.

`FEATURE_VERSION` must be bumped whenever ANY definition or computation changes; `registry_hash`
detects definition changes made without a bump (stale artifacts then fail to load).
"""

import hashlib

from src.schemas import FeatureSpec
from src.versioning import canonical_json

FEATURE_VERSION = "fv2"
BUILDER_VERSION = "builder-2.0.0"
AVAIL_SUFFIX = "_available"  # 1.0 = value present, 0.0 = unavailable (value is None/NaN)
VENUE_FEATURES = ("home_win_rate", "away_win_rate")  # already side-specific
_RES = ("fixtures.result", "fixtures.result_available_at_utc", "fixtures.kickoff_utc")
_LEAK = "only FINISHED matches with result_available_at_utc <= information_cutoff; current fixture excluded"

REGISTRY: list[FeatureSpec] = [
    *[
        FeatureSpec(
            name=f"form_points_{n}",
            source="results",
            window=f"last {n} matches",
            aggregation="points sum (W=3,D=1,L=0)",
            available_at="t-1",
            leakage_rule=_LEAK,
            depends_on=_RES,
        )
        for n in (3, 5, 10)
    ],
    FeatureSpec(
        name="goals_for_avg_5",
        source="results",
        window="last 5 matches",
        aggregation="mean goals for",
        available_at="t-1",
        leakage_rule=_LEAK,
        depends_on=_RES,
    ),
    FeatureSpec(
        name="goals_against_avg_5",
        source="results",
        window="last 5 matches",
        aggregation="mean goals against",
        available_at="t-1",
        leakage_rule=_LEAK,
        depends_on=_RES,
    ),
    FeatureSpec(
        name="home_win_rate",
        source="results",
        window="last 10 home matches (>=5)",
        aggregation="win share of the home team's home matches",
        available_at="t-1",
        leakage_rule=_LEAK,
        depends_on=_RES,
    ),
    FeatureSpec(
        name="away_win_rate",
        source="results",
        window="last 10 away matches (>=5)",
        aggregation="win share of the away team's away matches",
        available_at="t-1",
        leakage_rule=_LEAK,
        depends_on=_RES,
    ),
    FeatureSpec(
        name="rest_days_raw",
        source="results",
        window="last match",
        aggregation="days between last available match kickoff and this kickoff "
        "(includes summer breaks; cup/continental matches are absent)",
        available_at="t-1",
        leakage_rule=_LEAK,
        depends_on=_RES,
    ),
    FeatureSpec(
        name="rest_days_capped",
        source="results",
        window="last match",
        aggregation="min(rest_days_raw, features.rest_days_cap)",
        available_at="t-1",
        leakage_rule=_LEAK,
        depends_on=_RES,
    ),
    FeatureSpec(
        name="season_break_flag",
        source="results",
        window="last match",
        aggregation="1 if the last available match belongs to an earlier season",
        available_at="t-1",
        leakage_rule=_LEAK,
        depends_on=_RES,
    ),
    FeatureSpec(
        name="win_streak",
        source="results",
        window="current run",
        aggregation="consecutive wins ending at last match",
        available_at="t-1",
        leakage_rule=_LEAK,
        depends_on=_RES,
    ),
    FeatureSpec(
        name="loss_streak",
        source="results",
        window="current run",
        aggregation="consecutive losses ending at last match",
        available_at="t-1",
        leakage_rule=_LEAK,
        depends_on=_RES,
    ),
    FeatureSpec(
        name="opp_ppg_5",
        source="results",
        window="last 5 opponents (>=3 rated)",
        aggregation="mean points-per-game of recent opponents at cutoff (opponent needs >=5 matches)",
        available_at="t-1",
        leakage_rule=_LEAK,
        depends_on=_RES,
    ),
]

# Declared but NOT active: never produced, never fed to models (ADR 0009; docs/data_sources/xg.md).
EXPERIMENTAL: list[FeatureSpec] = [
    FeatureSpec(
        name="xg_avg_10",
        source="team_match_stats",
        window="last 10 matches",
        aggregation="mean xG for",
        available_at="t-1",
        leakage_rule=_LEAK,
        depends_on=("team_match_stats.xg",),
        status="experimental",
    ),
    FeatureSpec(
        name="xga_avg_10",
        source="team_match_stats",
        window="last 10 matches",
        aggregation="mean xG against",
        available_at="t-1",
        leakage_rule=_LEAK,
        depends_on=("team_match_stats.xg",),
        status="experimental",
    ),
]
SPECS = {s.name: s for s in [*REGISTRY, *EXPERIMENTAL]}
TEAM_FEATURES = [s.name for s in REGISTRY if s.name not in VENUE_FEATURES]


def spec_for(produced_name: str) -> FeatureSpec:
    """Map a produced column (home_form_points_5, away_rest_days_raw, home_win_rate) to its spec."""
    if produced_name in SPECS:
        return SPECS[produced_name]
    for side in ("home_", "away_"):
        if produced_name.startswith(side) and produced_name[len(side) :] in SPECS:
            return SPECS[produced_name[len(side) :]]
    raise KeyError(f"feature {produced_name!r} has no registry spec")


def produced_names() -> list[str]:
    names = [f"{side}_{b}" for side in ("home", "away") for b in TEAM_FEATURES]
    return names + list(VENUE_FEATURES)


def registry_hash() -> str:
    """Hash of the feature contract + builder version."""
    payload = canonical_json(
        {
            "version": FEATURE_VERSION,
            "builder": BUILDER_VERSION,
            "specs": [s.model_dump(mode="json") for s in [*REGISTRY, *EXPERIMENTAL]],
        }
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
