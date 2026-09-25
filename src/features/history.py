"""Cutoff-driven match history. The ONLY way features see the past.

A match becomes usable only once its result is available:
    available_at = kickoff + RESULT_LAG   and   available_at <= cutoff.
So anything that happens after the prediction cutoff (including the current fixture itself)
is invisible by construction.
"""

from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta

RESULT_LAG = timedelta(hours=3)  # conservative: match over + result published


@dataclass(frozen=True)
class MatchRecord:
    fixture_id: str
    kickoff_utc: datetime
    home_id: str
    away_id: str
    home_goals: int
    away_goals: int
    home_xg: float | None = None
    away_xg: float | None = None


@dataclass(frozen=True)
class TeamMatch:
    fixture_id: str
    kickoff_utc: datetime
    available_at: datetime
    is_home: bool
    opp_id: str
    gf: int
    ga: int
    xg_for: float | None
    xg_against: float | None

    @property
    def points(self) -> int:
        return 3 if self.gf > self.ga else 1 if self.gf == self.ga else 0


class MatchHistory:
    def __init__(self, matches: list[MatchRecord]):
        per_team: dict[str, list[TeamMatch]] = {}
        for m in matches:
            avail = m.kickoff_utc + RESULT_LAG
            per_team.setdefault(m.home_id, []).append(
                TeamMatch(
                    m.fixture_id,
                    m.kickoff_utc,
                    avail,
                    True,
                    m.away_id,
                    m.home_goals,
                    m.away_goals,
                    m.home_xg,
                    m.away_xg,
                )
            )
            per_team.setdefault(m.away_id, []).append(
                TeamMatch(
                    m.fixture_id,
                    m.kickoff_utc,
                    avail,
                    False,
                    m.home_id,
                    m.away_goals,
                    m.home_goals,
                    m.away_xg,
                    m.home_xg,
                )
            )
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

    def eligible(self, team: str, cutoff: datetime, exclude: str | None = None) -> list[TeamMatch]:
        """Team's matches whose result was available at cutoff, oldest first."""
        idx = bisect_right(self._keys.get(team, []), cutoff)
        return [m for m in self._matches.get(team, [])[:idx] if m.fixture_id != exclude]

    def ppg(self, team: str, cutoff: datetime, min_matches: int) -> tuple[float, datetime] | None:
        """Points per game over all matches available at cutoff, plus latest availability."""
        idx = bisect_right(self._keys.get(team, []), cutoff)
        if idx < min_matches or idx == 0:
            return None
        return self._cum_points[team][idx] / idx, self._keys[team][idx - 1]
