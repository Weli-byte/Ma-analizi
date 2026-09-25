"""Leakage-safe feature computation for one fixture at one information cutoff."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .history import MatchHistory, TeamMatch
from .registry import AVAIL_SUFFIX, TEAM_FEATURES, produced_names


class FixtureLike(Protocol):
    fixture_id: str
    kickoff_utc: datetime
    home_id: str
    away_id: str


@dataclass(frozen=True)
class FeatureResult:
    values: dict[str, float | None]  # includes *_avail flags
    available_at: dict[str, datetime]  # latest source timestamp behind each present feature


def _streak(ms: list[TeamMatch], won: bool) -> int:
    n = 0
    for m in reversed(ms):
        if (m.points == 3) if won else (m.points == 0):
            n += 1
        else:
            break
    return n


def _team_features(
    history: MatchHistory, team: str, cutoff: datetime, kickoff: datetime, fixture_id: str
) -> dict[str, tuple[float | None, datetime | None]]:
    ms = history.eligible(team, cutoff, exclude=fixture_id)
    out: dict[str, tuple[float | None, datetime | None]] = {}

    def put(name: str, value: float | None, used: list[TeamMatch]) -> None:
        ok = value is not None and used
        out[name] = (value, max(m.available_at for m in used)) if ok else (None, None)

    for n in (3, 5, 10):
        last = ms[-n:]
        put(f"form_points_{n}", sum(m.points for m in last) if len(ms) >= n else None, last)
    last5 = ms[-5:]
    enough5 = len(ms) >= 5
    put("goals_for_avg_5", sum(m.gf for m in last5) / 5 if enough5 else None, last5)
    put("goals_against_avg_5", sum(m.ga for m in last5) / 5 if enough5 else None, last5)
    last10 = ms[-10:]
    has_xg = len(ms) >= 10 and all(
        m.xg_for is not None and m.xg_against is not None for m in last10
    )
    put("xg_avg_10", sum(m.xg_for for m in last10) / 10 if has_xg else None, last10)
    put("xga_avg_10", sum(m.xg_against for m in last10) / 10 if has_xg else None, last10)
    put(
        "rest_days",
        (kickoff - ms[-1].kickoff_utc).total_seconds() / 86400 if ms else None,
        ms[-1:],
    )
    put("win_streak", float(_streak(ms, True)) if ms else None, ms[-1:])
    put("loss_streak", float(_streak(ms, False)) if ms else None, ms[-1:])

    # opponent strength: mean ppg of last 5 opponents, as rated at the SAME cutoff
    if enough5:
        rated = [r for m in last5 if (r := history.ppg(m.opp_id, cutoff, 5)) is not None]
        if len(rated) >= 3:
            value = sum(r[0] for r in rated) / len(rated)
            avail = max(max(m.available_at for m in last5), *(r[1] for r in rated))
            out["opp_ppg_5"] = (value, avail)
        else:
            out["opp_ppg_5"] = (None, None)
    else:
        out["opp_ppg_5"] = (None, None)

    # venue split (window 10, need >=5)
    for side, is_home in (("home", True), ("away", False)):
        venue = [m for m in ms if m.is_home == is_home][-10:]
        rate = sum(m.points == 3 for m in venue) / len(venue) if len(venue) >= 5 else None
        put(f"{side}_win_rate", rate, venue)
    return out


def compute_features(
    fixture: FixtureLike, history: MatchHistory, cutoff: datetime
) -> FeatureResult:
    if cutoff > fixture.kickoff_utc:
        raise ValueError("information_cutoff must not be after kickoff")
    home = _team_features(history, fixture.home_id, cutoff, fixture.kickoff_utc, fixture.fixture_id)
    away = _team_features(history, fixture.away_id, cutoff, fixture.kickoff_utc, fixture.fixture_id)
    picked: dict[str, tuple[float | None, datetime | None]] = {}
    for base in TEAM_FEATURES:
        picked[f"home_{base}"] = home[base]
        picked[f"away_{base}"] = away[base]
    picked["home_win_rate"] = home["home_win_rate"]  # home team, home matches
    picked["away_win_rate"] = away["away_win_rate"]  # away team, away matches

    values: dict[str, float | None] = {}
    available_at: dict[str, datetime] = {}
    for name in produced_names():
        v, at = picked[name]
        values[name] = v
        values[name + AVAIL_SUFFIX] = 0.0 if v is None else 1.0
        if at is not None:
            available_at[name] = at
    return FeatureResult(values, available_at)
