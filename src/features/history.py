"""Cutoff-driven match history. The ONLY way features see the past.

A match becomes usable only once its RESULT is available:
    result_available_at_utc <= information_cutoff   and   status == FINISHED.
Postponed / cancelled / abandoned / scheduled matches never enter the history. The current
fixture and everything later are invisible by construction. For historical data the availability
time is INFERRED (kickoff + provisional lag, ADR 0006), not observed.
"""

from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType

from src.schemas import FixtureStatus


@dataclass(frozen=True)
class MatchRecord:
    fixture_id: str
    season: str
    kickoff_utc: datetime
    home_id: str
    away_id: str
    home_goals: int | None
    away_goals: int | None
    result_available_at_utc: datetime | None
    status: FixtureStatus = FixtureStatus.FINISHED
    # post-match stats / market data, loaded ONLY so the leakage audit can prove features ignore them
    extras: Mapping[str, float] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class TeamMatch:
    fixture_id: str
    season: str
    kickoff_utc: datetime
    available_at: datetime
    is_home: bool
    opp_id: str
    gf: int
    ga: int

    @property
    def points(self) -> int:
        return 3 if self.gf > self.ga else 1 if self.gf == self.ga else 0


class MatchHistory:
    def __init__(self, matches: list[MatchRecord]):
        per_team: dict[str, list[TeamMatch]] = {}
        all_avail: list[tuple[datetime, str]] = []
        for m in matches:
            avail = m.result_available_at_utc
            if (
                m.status != FixtureStatus.FINISHED
                or avail is None
                or m.home_goals is None
                or m.away_goals is None
            ):
                continue  # only finished matches with a known result time are history
            per_team.setdefault(m.home_id, []).append(
                TeamMatch(
                    m.fixture_id,
                    m.season,
                    m.kickoff_utc,
                    avail,
                    True,
                    m.away_id,
                    m.home_goals,
                    m.away_goals,
                )
            )
            per_team.setdefault(m.away_id, []).append(
                TeamMatch(
                    m.fixture_id,
                    m.season,
                    m.kickoff_utc,
                    avail,
                    False,
                    m.home_id,
                    m.away_goals,
                    m.home_goals,
                )
            )
            all_avail.append((avail, m.season))
        self._matches: dict[str, list[TeamMatch]] = {}
        self._keys: dict[str, list[datetime]] = {}
        self._cum_points: dict[str, list[int]] = {}
        for team, ms in per_team.items():
            ms.sort(key=lambda x: (x.available_at, x.fixture_id))
            self._matches[team] = ms
            self._keys[team] = [x.available_at for x in ms]
            cum = [0]
            for x in ms:
                cum.append(cum[-1] + x.points)
            self._cum_points[team] = cum
        all_avail.sort()
        self._all_keys = [a for a, _ in all_avail]
        self._all_seasons = [s for _, s in all_avail]

    def eligible(self, team: str, cutoff: datetime, exclude: str | None = None) -> list[TeamMatch]:
        """Team's matches whose result was available at cutoff, oldest first."""
        idx = bisect_right(self._keys.get(team, []), cutoff)
        return [m for m in self._matches.get(team, [])[:idx] if m.fixture_id != exclude]

    def has_prior_season(self, cutoff: datetime, season: str) -> bool:
        """Is any match of an EARLIER season available at cutoff? (diagnostic for NaN reasons)"""
        idx = bisect_right(self._all_keys, cutoff)
        return any(s < season for s in self._all_seasons[:idx])

    def ppg(self, team: str, cutoff: datetime, min_matches: int) -> tuple[float, datetime] | None:
        """Points per game over all matches available at cutoff, plus latest availability."""
        idx = bisect_right(self._keys.get(team, []), cutoff)
        if idx < min_matches or idx == 0:
            return None
        return self._cum_points[team][idx] / idx, self._keys[team][idx - 1]
