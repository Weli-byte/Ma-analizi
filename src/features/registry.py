"""Feature registry (feature contract): source, window, aggregation, available_at, leakage_rule."""

import hashlib

from src.schemas import FeatureSpec
from src.versioning import canonical_json

FEATURE_VERSION = "fv1"
AVAIL_SUFFIX = "_avail"  # 1.0 = value present, 0.0 = insufficient history (value is None/NaN)
VENUE_FEATURES = ("home_win_rate", "away_win_rate")  # already side-specific
_RES = ["fixtures.results", "fixtures.kickoff_utc"]
_LEAK = "only matches with kickoff+3h <= information_cutoff; current fixture excluded"

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
        name="xg_avg_10",
        source="team_match_stats",
        window="last 10 matches",
        aggregation="mean xG for",
        available_at="t-1",
        leakage_rule=_LEAK + "; NaN when any xG missing (source has none in dv1)",
        depends_on=["team_match_stats.xg"],
    ),
    FeatureSpec(
        name="xga_avg_10",
        source="team_match_stats",
        window="last 10 matches",
        aggregation="mean xG against",
        available_at="t-1",
        leakage_rule=_LEAK + "; NaN when any xG missing (source has none in dv1)",
        depends_on=["team_match_stats.xg"],
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
        name="rest_days",
        source="results",
        window="last match",
        aggregation="days between last available match kickoff and this kickoff",
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
        aggregation="mean points-per-game of recent opponents at cutoff "
        "(opponent-strength base; opponent needs >=5 matches)",
        available_at="t-1",
        leakage_rule=_LEAK,
        depends_on=_RES,
    ),
]
SPECS = {s.name: s for s in REGISTRY}
TEAM_FEATURES = [s.name for s in REGISTRY if s.name not in VENUE_FEATURES]


def spec_for(produced_name: str) -> FeatureSpec:
    """Map a produced column (home_form_points_5, away_rest_days, home_win_rate) to its spec."""
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
    """Hash of the feature contract; changes whenever a definition changes (bump fv!)."""
    payload = canonical_json([s.model_dump() for s in REGISTRY])
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
