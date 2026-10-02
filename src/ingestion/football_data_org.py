"""S12: a REAL `FixtureProvider` adapter for football-data.org's free tier (API v4).

Chosen because it is free with no payment method required (10 calls/minute, 12 competitions --
confirmed via `https://www.football-data.org/pricing`, 2026-10-01) -- the project owner has no
budget right now (see ADR 0023's amendment / `docs/data_sources/licensing.md`). This repo's own
confirmed research into their docs (`https://docs.football-data.org/general/v4/*`, same date)
verified the exact field shapes used below; no field name here is guessed.

Classification stays RESEARCH_ONLY (`docs/data_sources/licensing.md`) until the project owner
explicitly reviews football-data.org's full terms of service for commercial/redistribution
rights -- their homepage states the free tier is "free forever" but does not itself state
commercial-use or redistribution terms; same honesty rule this repo already applies to every
other source in that table.

A real API key is required (`configs/provider.yaml`-style: `enabled: true` + an env var naming
it) -- `enabled: false` by default, exactly like `src/llm/providers.py`'s pattern. Register at
https://www.football-data.org/client/register (free, email only, no payment).
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime

from .interfaces import Capability, ProviderMeta, Support
from .provider import FixtureProvider, League, ProviderError, RateLimitedError, RawFixture, Season

BASE_URL = "https://api.football-data.org/v4"

# football-data.org v4 status vocabulary -> this repo's FixtureStatus (upsert.py's status_map).
# AWARDED (a match decided administratively, e.g. forfeit) maps to ABANDONED -- the closest
# existing FixtureStatus; there is no separate "awarded" concept in schemas.common.FixtureStatus.
STATUS_MAP = {
    "SCHEDULED": "NS",
    "TIMED": "NS",
    "IN_PLAY": "1H",
    "PAUSED": "HT",
    "FINISHED": "FT",
    "SUSPENDED": "PST",
    "POSTPONED": "PST",
    "CANCELLED": "CANC",
    "AWARDED": "AWD",
}  # fmt: skip  -- re-expressed through upsert.DEFAULT_STATUS_MAP's own vocabulary, not a parallel one


def _get(path: str, api_key: str, params: dict | None = None, timeout: float = 15.0) -> dict:
    """Isolated so tests monkeypatch exactly this, never the real network (same seam as
    `src/llm/providers.py::_post_json`)."""
    url = f"{BASE_URL}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"X-Auth-Token": api_key}, method="GET")  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise RateLimitedError(f"{path}: HTTP 429 rate limited") from e
        raise ProviderError(f"{path}: HTTP {e.code} {e.reason}") from e
    except urllib.error.URLError as e:
        raise ProviderError(f"{path}: {e}") from e


META = ProviderMeta(
    name="football-data-org",
    capabilities={
        Capability.FIXTURES: Support.SUPPORTED,
        Capability.LINEUPS: Support.NOT_SUPPORTED,  # free tier: `lineup`/`bench` absent (checked 2026-10-02)
        Capability.INJURIES: Support.NOT_SUPPORTED,
        Capability.EVENTS: Support.NOT_SUPPORTED,
        Capability.ODDS: Support.NOT_SUPPORTED,  # `odds` key present but not populated on the free tier
        Capability.STATISTICS: Support.NOT_SUPPORTED,
        Capability.XG: Support.NOT_SUPPORTED,
    },
    coverage="12 free-tier competitions (PL, PD, BL1, SA, FL1, CL, DED, PPL, ...)",
    timestamp_semantics="utcDate = scheduled kickoff; lastUpdated = provider record update time",
    rate_limit="10 requests/minute (free tier)",
    license="free tier, commercial terms unverified",
    license_status="RESEARCH_ONLY",
    provenance="https://api.football-data.org/v4/competitions/{id}/matches, /matches/{id}",
    verified_on="2026-10-02",
)


class FootballDataOrgProvider(FixtureProvider):
    meta = META

    name = "football-data-org"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def list_leagues(self) -> list[League]:
        data = _get("/competitions", self.api_key)
        return [
            League(str(c["id"]), c["name"], c["area"].get("code") or c["area"]["name"])
            for c in data.get("competitions", [])
        ]

    def list_seasons(self, league_id: str) -> list[Season]:
        data = _get(f"/competitions/{league_id}", self.api_key)
        out = []
        for s in data.get("seasons", []):
            start = date.fromisoformat(s["startDate"])
            end = date.fromisoformat(s["endDate"])
            out.append(Season(league_id, _season_label(start, end), start, end))
        return out

    def list_fixtures(self, league_id: str, season: str) -> list[RawFixture]:
        year = _season_start_year(season)
        data = _get(f"/competitions/{league_id}/matches", self.api_key, params={"season": year})
        fixtures = []
        for m in data.get("matches", []):
            status = STATUS_MAP.get(m["status"], m["status"])  # unknown codes pass through as-is;
            # upsert.resolve_status raises UnknownStatusError rather than guessing (same contract
            # as every other status_map consumer).
            full_time = (m.get("score") or {}).get("fullTime") or {}
            fixtures.append(
                RawFixture(
                    provider_fixture_id=str(m["id"]),
                    league_id=league_id,
                    season=season,
                    kickoff_utc=datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00")),
                    home_team_raw_name=m["homeTeam"]["name"],
                    away_team_raw_name=m["awayTeam"]["name"],
                    status_raw=status,
                    home_goals=full_time.get("home"),
                    away_goals=full_time.get("away"),
                    extra={"provider_match_id": m["id"]},
                )
            )
        return fixtures

    # lineups/injuries/events/statistics/odds: NOT overridden -- inherit FixtureProvider's
    # NotImplementedError bodies (S13+ scope; football-data.org's free tier doesn't expose most
    # of these anyway, so there is nothing honest to implement here yet).


def _season_label(start: date, end: date) -> str:
    return f"{start.year}-{str(end.year)[2:]}" if start.year != end.year else str(start.year)


def _season_start_year(season: str) -> int:
    return int(season.split("-")[0])
