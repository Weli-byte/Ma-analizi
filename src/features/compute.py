"""Leakage-safe feature computation for one fixture at one information cutoff."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from src.config import FeaturesConfig

from .history import MatchHistory, TeamMatch
from .registry import AVAIL_SUFFIX, TEAM_FEATURES, produced_names

DEFAULT_CONFIG = FeaturesConfig(
    feature_version="fv2", rest_days_cap=30, result_lag_hours=3, cutoff_offset_hours=0
)


class FixtureLike(Protocol):
    fixture_id: str
    season: str
    kickoff_utc: datetime
    home_id: str
    away_id: str


@dataclass(frozen=True)
class FeatureResult:
    values: dict[str, float | None]  # includes *_available flags
    available_at: dict[str, datetime]  # latest source timestamp behind each present feature
    reasons: dict[str, str] = field(default_factory=dict)  # feature -> why it is unavailable


def _streak(ms: list[TeamMatch], won: bool) -> int:
    n = 0
    for m in reversed(ms):
        if (m.points == 3) if won else (m.points == 0):
            n += 1
        else:
            break
    return n


def _team_features(
    history: MatchHistory,
    team: str,
    cutoff: datetime,
    fixture: FixtureLike,
    cfg: FeaturesConfig,
) -> dict[str, tuple[float | None, datetime | None, str | None]]:
    ms = history.eligible(team, cutoff, exclude=fixture.fixture_id)
    out: dict[str, tuple[float | None, datetime | None, str | None]] = {}
    if ms:
        empty_reason = "insufficient_history"
    elif history.has_prior_season(cutoff, fixture.season):
        empty_reason = "new_team"
    else:
        empty_reason = "dataset_start"

    def put(name: str, value: float | None, used: list[TeamMatch]) -> None:
        if value is None or not used:
            out[name] = (None, None, empty_reason)
        else:
            out[name] = (value, max(m.available_at for m in used), None)

    for n in (3, 5, 10):
        last = ms[-n:]
        put(f"form_points_{n}", float(sum(m.points for m in last)) if len(ms) >= n else None, last)
    last5 = ms[-5:]
    enough5 = len(ms) >= 5
    put("goals_for_avg_5", sum(m.gf for m in last5) / 5 if enough5 else None, last5)
    put("goals_against_avg_5", sum(m.ga for m in last5) / 5 if enough5 else None, last5)

    if ms:
        raw = (fixture.kickoff_utc - ms[-1].kickoff_utc).total_seconds() / 86400
        put("rest_days_raw", raw, ms[-1:])
        put("rest_days_capped", min(raw, cfg.rest_days_cap), ms[-1:])
        put("season_break_flag", 0.0 if ms[-1].season == fixture.season else 1.0, ms[-1:])
        put("win_streak", float(_streak(ms, True)), ms[-1:])
        put("loss_streak", float(_streak(ms, False)), ms[-1:])
    else:
        for name in (
            "rest_days_raw",
            "rest_days_capped",
            "season_break_flag",
            "win_streak",
            "loss_streak",
        ):
            put(name, None, [])

    # opponent strength: mean ppg of the last 5 opponents, rated at the SAME cutoff
    if enough5:
        rated = [r for m in last5 if (r := history.ppg(m.opp_id, cutoff, 5)) is not None]
        if len(rated) >= 3:
            value = sum(r[0] for r in rated) / len(rated)
            avail = max(max(m.available_at for m in last5), *(r[1] for r in rated))
            out["opp_ppg_5"] = (value, avail, None)
        else:
            out["opp_ppg_5"] = (None, None, "insufficient_history")
    else:
        out["opp_ppg_5"] = (None, None, empty_reason)

    # venue split (window 10, need >=5 matches at that venue)
    for side, is_home in (("home", True), ("away", False)):
        venue = [m for m in ms if m.is_home == is_home][-10:]
        rate = sum(m.points == 3 for m in venue) / len(venue) if len(venue) >= 5 else None
        put(f"{side}_win_rate", rate, venue)
    return out


def compute_features(
    fixture: FixtureLike,
    history: MatchHistory,
    cutoff: datetime,
    cfg: FeaturesConfig = DEFAULT_CONFIG,
) -> FeatureResult:
    if cutoff > fixture.kickoff_utc:
        raise ValueError("information_cutoff must not be after kickoff")
    home = _team_features(history, fixture.home_id, cutoff, fixture, cfg)
    away = _team_features(history, fixture.away_id, cutoff, fixture, cfg)
    picked: dict[str, tuple[float | None, datetime | None, str | None]] = {}
    for base in TEAM_FEATURES:
        picked[f"home_{base}"] = home[base]
        picked[f"away_{base}"] = away[base]
    picked["home_win_rate"] = home["home_win_rate"]  # home team, home matches
    picked["away_win_rate"] = away["away_win_rate"]  # away team, away matches

    values: dict[str, float | None] = {}
    available_at: dict[str, datetime] = {}
    reasons: dict[str, str] = {}
    for name in produced_names():
        v, at, why = picked[name]
        values[name] = v
        values[name + AVAIL_SUFFIX] = 0.0 if v is None else 1.0
        if at is not None:
            available_at[name] = at
        if why is not None:
            reasons[name] = why
    return FeatureResult(values, available_at, reasons)
