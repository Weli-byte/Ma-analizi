"""Feature availability reporting and enforcement (no silent fallbacks; ADR 0009)."""

from collections import Counter
from dataclasses import asdict, dataclass, field

from src.runmode import RunMode, policy

DECLARED_REASONS = frozenset({"dataset_start", "new_team", "insufficient_history"})


class FeatureAvailabilityError(RuntimeError):
    """Feature coverage is not acceptable for the current run mode."""


@dataclass
class FeatureAvailabilityReport:
    total_fixtures: int
    available_feature_rows: int  # every required feature present
    missing_feature_rows: int  # NO feature record at all for the fixture (unexpected)
    fallback_rows: int  # record exists but a required feature is unavailable
    unexpected_missing_rows: int  # missing record OR unavailable without a declared reason
    fallback_rate: float  # (missing + fallback) / total
    missing_by_feature: dict[str, int] = field(default_factory=dict)
    missing_by_season: dict[str, int] = field(default_factory=dict)
    missing_by_team: dict[str, int] = field(default_factory=dict)
    reasons: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def team_of(feature: str, home_id: str, away_id: str) -> str:
    """Which team a produced feature describes."""
    if feature.startswith("away_"):
        return away_id
    return home_id


def build_report(rows, required: list[str]) -> FeatureAvailabilityReport:
    """rows: EvalRow-like (features, unavailable_reasons, season, home_id, away_id)."""
    avail = miss = fallback = unexpected = 0
    by_feature: Counter = Counter()
    by_season: Counter = Counter()
    by_team: Counter = Counter()
    reasons: Counter = Counter()
    for r in rows:
        if not r.features:
            miss += 1
            unexpected += 1
            by_season[r.season] += 1
            reasons["missing_record"] += 1
            continue
        absent = [f for f in required if r.features.get(f) is None]
        if not absent:
            avail += 1
            continue
        fallback += 1
        by_season[r.season] += 1
        row_unexpected = False
        for f in absent:
            by_feature[f] += 1
            by_team[team_of(f, r.home_id, r.away_id)] += 1
            why = r.unavailable_reasons.get(f)
            reasons[why or "undeclared"] += 1
            if why not in DECLARED_REASONS:
                row_unexpected = True
        unexpected += row_unexpected
    total = len(rows)
    rate = (miss + fallback) / total if total else 0.0
    return FeatureAvailabilityReport(
        total,
        avail,
        miss,
        fallback,
        unexpected,
        round(rate, 6),
        dict(by_feature),
        dict(by_season),
        dict(by_team),
        dict(reasons),
    )


def enforce(report: FeatureAvailabilityReport, mode: RunMode | str, max_rate: float, model_id: str) -> None:
    """STRICT/FINAL: no unexpected missing rows and rate <= threshold.
    RESEARCH: unexpected rows count toward the threshold. DEVELOPMENT: only the threshold."""
    pol = policy(mode)
    if report.fallback_rate > max_rate:
        raise FeatureAvailabilityError(
            f"{model_id}: fallback rate {report.fallback_rate:.3f} exceeds the {RunMode(mode).value} "
            f"threshold {max_rate:.3f} (missing_by_season={report.missing_by_season})"
        )
    if not pol.tolerate_unexpected_missing_features and report.unexpected_missing_rows:
        raise FeatureAvailabilityError(
            f"{model_id}: {report.unexpected_missing_rows} rows have unexpected missing features "
            f"({report.reasons}); {RunMode(mode).value} mode allows only declared startup NaNs"
        )
