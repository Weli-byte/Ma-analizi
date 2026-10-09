"""openfootball/football.json: results + fixtures, PUBLIC DOMAIN (ADR 0038).

The repository states: "The football.json schema, data and scripts are dedicated to the public domain. Use
as you please with no restrictions whatsoever." (read 2026-10-08). It is the only source in this project
whose terms explicitly allow commercial use. Season files for the running season are refreshed daily by the
maintainers' GitHub Action; completeness depends on community contributions.

Limits (honest): scores and dates only (no lineups/injuries/odds/events); kick-off times are LOCAL clock
times without a zone, converted to UTC with the league's zone (INFERRED, flagged in `extra`); there is no
result timestamp, so a result is available from the moment WE first observe it (same rule as every other
source, see results.py).
"""

import json
import re
import urllib.request
from datetime import date, time

from src.data.timezones import InvalidLocalTime, date_only_to_utc, local_to_utc
from src.mlops.oplog import logged_urlopen

from .endpoints import endpoint
from .provider import ProviderError, RawFixture

BASE = "https://raw.githubusercontent.com/openfootball/football.json/master"
SOURCE = "openfootball"
FILES = {
    "EPL": ("en.1", "Europe/London", "ENG"),
    "LALIGA": ("es.1", "Europe/Madrid", "ESP"),
    "BUNDESLIGA": ("de.1", "Europe/Berlin", "GER"),
    "SERIEA": ("it.1", "Europe/Rome", "ITA"),
    "LIGUE1": ("fr.1", "Europe/Paris", "FRA"),
    "SUPERLIG": (
        "tr.1",
        "Europe/Istanbul",
        "TUR",
    ),  # openfootball has gaps for this league (2021-24, 2026-27)
}


def season_dir(season_start_year: int) -> str:
    return f"{season_start_year}-{str(season_start_year + 1)[2:]}"


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def parse(raw: bytes, repo_league: str, season: str) -> list[RawFixture]:
    """Pure parser. A match with a full-time score is FT, otherwise SCHEDULED (never a guessed score)."""
    data = json.loads(raw.decode("utf-8-sig"))
    tz = FILES[repo_league][1]
    out = []
    for m in data["matches"]:
        day = date.fromisoformat(m["date"])
        hh, mm = (int(x) for x in m["time"].split(":")) if m.get("time") else (0, 0)
        try:
            kickoff = local_to_utc(day, time(hh, mm), tz)
        except InvalidLocalTime:  # DST gap/fold: only the date is trustworthy
            kickoff = date_only_to_utc(day)
        score = m.get("score")
        # Two real shapes: {"ht": [..], "ft": [h, a]} and (for 0-0 results) a bare [h, a]. Bare lists were
        # cross-checked on 2026-10-08 against football-data.co.uk (Leeds 0-0 Crystal Palace, 2026-09-20).
        ft = score.get("ft") if isinstance(score, dict) else score if isinstance(score, list) else None
        out.append(
            RawFixture(
                provider_fixture_id=f"{m['date']}-{_slug(m['team1'])}-{_slug(m['team2'])}",
                league_id=repo_league,
                season=season,
                kickoff_utc=kickoff,
                home_team_raw_name=m["team1"],
                away_team_raw_name=m["team2"],
                status_raw="FT" if ft else "SCHEDULED",
                home_goals=ft[0] if ft else None,
                away_goals=ft[1] if ft else None,
                extra={
                    "kickoff_time_zone_inferred": tz,
                    "time_given": bool(m.get("time")),
                    "round": m.get("round"),
                    "score_shape": type(score).__name__,
                },
            )  # fmt: skip
        )
    return out


class OpenFootballProvider:
    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout

    def list_fixtures(self, repo_league: str, season_start_year: int) -> list[RawFixture]:
        code = FILES[repo_league][0]
        url = f"{endpoint('OPENFOOTBALL_BASE_URL', BASE)}/{season_dir(season_start_year)}/{code}.json"
        try:
            raw = logged_urlopen(SOURCE, "fixtures", urllib.request.Request(url), self.timeout)
        except Exception as e:  # noqa: BLE001 - re-raised as the provider error type, name only
            raise ProviderError(f"openfootball: {type(e).__name__}") from None
        return parse(raw, repo_league, f"{season_start_year}-{str(season_start_year + 1)[2:]}")
