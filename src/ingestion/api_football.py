"""API-Football (api-sports.io), FREE plan: what a REAL account was verified to give on 2026-10-08.

Verified with real calls (key in `API_FOOTBALL_KEY`, header `x-apisports-key`; the host IS reachable
from the owner's network):
- `/status`: plan Free, 100 requests/day, 10/minute.
- Fixtures and lineups: only SEASONS 2022-2024 ("Free plans do not have access to this season, try from
  2022 to 2024") -- not the current season, so there are NO current lineups from this plan.
- `/injuries?date=YYYY-MM-DD`: only dates in a rolling window [yesterday, tomorrow]
  ("try from 2026-10-07 to 2026-10-09"), WITHOUT a season filter; one call returns every league of that
  date (208 entries on 2026-10-09) and each entry is tied to its fixture (`fixture.id`, `fixture.date`).
  Entries carry `type` (`Missing Fixture` / `Questionable`) and `reason`, but NO report time: `effective_at`
  is None and the cutoff rule uses our fetch time (the state as of `observed_at`).
So it is a CURRENT-injuries source for fixtures kicking off within about a day (it covers La Liga, which
FPL does not), and a historical source of lineups for 2022-2024 only. League ids: EPL 39 (note id 235 is
the RUSSIAN Premier League), La Liga 140. Terms of the free plan are not verified for commercial use.
"""

import hashlib
import json
import urllib.error
import urllib.request
from datetime import UTC, date, datetime

from src.data.teams import TeamDirectory
from src.mlops.oplog import logged_urlopen

from .endpoints import endpoint
from .interfaces import AvailabilityStatus, Capability, PlayerAvailability, ProviderMeta, Support
from .provider import ProviderError

BASE = "https://v3.football.api-sports.io"
SOURCE = "api-football"
LEAGUE_IDS = {"EPL": (39, "ENG"), "LALIGA": (140, "ESP")}  # repo league -> (api-football league id, country)

META = ProviderMeta(
    name=SOURCE,
    capabilities={
        Capability.FIXTURES: Support.NOT_SUPPORTED,  # free plan: seasons 2022-2024 only
        Capability.LINEUPS: Support.NOT_SUPPORTED,  # free plan: no current-season lineups (2022-2024 only)
        Capability.INJURIES: Support.SUPPORTED,  # [yesterday, tomorrow] only, all leagues, fixture-linked
        Capability.EVENTS: Support.NOT_SUPPORTED,
        Capability.ODDS: Support.NOT_SUPPORTED,
        Capability.STATISTICS: Support.NOT_SUPPORTED,
        Capability.XG: Support.NOT_SUPPORTED,
    },
    coverage="/injuries for dates within [yesterday, tomorrow], all leagues; fixtures/lineups 2022-2024 only",
    timestamp_semantics="no report time per injury: only our fetch time (observed_at); fixture.date=kickoff",
    rate_limit="100 requests/day, 10/minute (free plan)",
    license="free plan terms not verified for commercial use",
    license_status="RESEARCH_ONLY",
    provenance=f"{BASE}/injuries?date=YYYY-MM-DD",
    verified_on="2026-10-08",
)


def status_for(type_: str | None, reason: str | None) -> AvailabilityStatus:
    r = (reason or "").lower()
    if (type_ or "").lower() == "questionable":
        return AvailabilityStatus.DOUBTFUL
    if "suspen" in r or "red card" in r or "yellow card" in r:
        return AvailabilityStatus.SUSPENDED
    if "injur" in r or "illness" in r or "knock" in r or "surgery" in r or "fracture" in r:
        return AvailabilityStatus.INJURED
    return AvailabilityStatus.UNAVAILABLE  # e.g. loan agreement, national duty: not selectable, not injured


def parse_injuries(
    raw: bytes, directory: TeamDirectory, observed: datetime, wanted: dict[int, str] | None = None
) -> list[PlayerAvailability]:
    """Pure parser. `wanted`: api-football league id -> country code for team-name resolution; other
    leagues in the response are ignored. A plan/error payload raises (never an empty 'no injuries')."""
    data = json.loads(raw.decode("utf-8"))
    if data.get("errors"):
        raise ProviderError(f"api-football: {data['errors']}")
    sha = hashlib.sha256(raw).hexdigest()
    wanted = wanted or {lid: c for lid, c in LEAGUE_IDS.values()}
    out = []
    for r in data.get("response", []):
        league = r["league"]["id"]
        if league not in wanted:
            continue
        day = datetime.fromisoformat(r["fixture"]["date"]).astimezone(UTC).date()
        res = directory.resolve(SOURCE, r["team"]["name"], wanted[league], day)
        out.append(
            PlayerAvailability(
                player=r["player"]["name"],
                team_id=res.team_id,
                team_raw_name=r["team"]["name"],
                status=status_for(r["player"].get("type"), r["player"].get("reason")),
                availability_pct=None,
                detail=r["player"].get("reason"),
                source=SOURCE,
                observed_at=observed,
                effective_at=None,
                confidence=None,
                provenance={
                    "endpoint": f"{BASE}/injuries",
                    "api_type": r["player"].get("type"),
                    "fixture_id": r["fixture"]["id"],
                    "fixture_date": r["fixture"]["date"],
                    "league_id": league,
                    "raw_response_sha256": sha,
                },
            )  # fmt: skip
        )
    return out


class ApiFootballProvider:
    meta = META

    def __init__(self, api_key: str, directory: TeamDirectory, timeout: float = 25.0):
        self.api_key = api_key
        self.directory = directory
        self.timeout = timeout
        self.last_raw_sha256: str | None = None

    def injuries_for_date(self, day: date, observed_at: datetime | None = None) -> list[PlayerAvailability]:
        url = f"{endpoint('API_FOOTBALL_BASE_URL', BASE)}/injuries?date={day.isoformat()}"
        req = urllib.request.Request(url, headers={"x-apisports-key": self.api_key})
        try:
            raw = logged_urlopen(SOURCE, "injuries", req, self.timeout)
        except (urllib.error.URLError, TimeoutError) as e:
            raise ProviderError(
                f"api-football: {type(e).__name__}"
            ) from None  # message never carries the key
        self.last_raw_sha256 = hashlib.sha256(raw).hexdigest()
        return parse_injuries(raw, self.directory, observed_at or datetime.now(UTC))
