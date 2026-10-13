"""UEFA-style coefficients computed from the European-cup results we hold (ADR 0056).

The official UEFA coefficients are not used: their publication terms are unverified and the numbers cannot be
fetched from the dev PC. The same quantity is derivable, leak-free, from the openfootball cup results (CC0):

  club points   2 per win, 1 per draw (90-minute result), summed over the last five SEASONS before `as_of`
  country       per season: points of the country's clubs / number of its clubs that played; summed over five seasons
  differences   no qualifying rounds, no bonus points, no extra-time results: it is an APPROXIMATION of the ranking,
                good for ordering, not a reproduction of the official figures.

Only matches whose result was available at `as_of` are used, so a prior never sees the future.
"""

import math
from datetime import datetime, timedelta

from .data import StatMatch

CUPS = ("UCL", "UEL", "UECL")
WINDOW_DAYS = 5 * 365


def _country(team_id: str) -> str:
    return team_id.split("_", 1)[0]


def coefficients(matches: list[StatMatch], as_of: datetime) -> tuple[dict[str, float], dict[str, float]]:
    """(club coefficient, country coefficient) from cup matches available at `as_of` in the last five seasons."""
    pts: dict[tuple[str, str], float] = {}  # (season, team) -> points
    since = as_of - timedelta(days=WINDOW_DAYS)
    for m in matches:
        if m.league not in CUPS or m.available_at > as_of or m.kickoff_utc < since:
            continue
        for team, mine, theirs in (
            (m.home_id, m.home_goals, m.away_goals),
            (m.away_id, m.away_goals, m.home_goals),
        ):
            gained = 2.0 if mine > theirs else 1.0 if mine == theirs else 0.0
            pts[(m.season, team)] = pts.get((m.season, team), 0.0) + gained
    club: dict[str, float] = {}
    per_country: dict[tuple[str, str], list[float]] = {}
    for (season, team), p in pts.items():
        club[team] = club.get(team, 0.0) + p
        per_country.setdefault((season, _country(team)), []).append(p)
    country: dict[str, float] = {}
    for (_season, cc), ps in per_country.items():
        country[cc] = country.get(cc, 0.0) + sum(ps) / len(ps)
    return club, country


def uefa_priors(
    matches: list[StatMatch], as_of: datetime, country_weight: float, club_weight: float
) -> dict[str, float]:
    """team_id -> log-strength prior g (0 = average): where a team's strength is SHRUNK TO when it has little data.

    g = country_weight * z_country + club_weight * z_club
      z_country: standardised log of the country coefficient (the league's difficulty); a country without any cup
                 points gets the lowest value seen
      z_club:    the club's percentile among clubs with cup points, centred on 0 (median -> 0); a club without
                 points sits at the bottom (-0.5)
    Without enough cup data (fewer than three countries) no prior is produced."""
    if country_weight == 0 and club_weight == 0:
        return {}
    club, country = coefficients(matches, as_of)
    if len(country) < 3 or not club:
        return {}
    logs = {cc: math.log(c + 1.0) for cc, c in country.items()}
    mean = sum(logs.values()) / len(logs)
    sd = math.sqrt(sum((v - mean) ** 2 for v in logs.values()) / len(logs)) or 1.0
    z_country = {cc: (v - mean) / sd for cc, v in logs.items()}
    low = min(z_country.values())
    ranked = sorted(club.values())
    n = len(ranked)

    def pct(v: float) -> float:
        return sum(1 for x in ranked if x < v) / n + 0.5 * sum(1 for x in ranked if x == v) / n

    out: dict[str, float] = {}
    for t in {m.home_id for m in matches} | {m.away_id for m in matches}:
        zc = z_country.get(_country(t), low)
        zk = pct(club[t]) - 0.5 if t in club else -0.5
        out[t] = country_weight * zc + club_weight * zk
    return out
