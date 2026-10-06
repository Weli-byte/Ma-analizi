"""Fantasy Premier League public API -> player availability (real injuries/suspensions, PL only).

Verified live on 2026-10-02: `bootstrap-static` returns every PL player with `status`
(a/d/i/s/u), `news`, `news_added` (UTC timestamp of the news item) and
`chance_of_playing_next_round`. Unofficial and undocumented: terms are NOT verified, so this source
is `RESEARCH_ONLY` and must never be presented as a licensed commercial feed (ADR 0028).

Timestamp semantics: `effective_at` = `news_added` (when FPL published that status);
`observed_at` = when this process received the response. The response always reflects the CURRENT
state, so a status that changed after a past cutoff cannot be reconstructed -- callers must apply
the cutoff rule `effective_at <= information_cutoff` and treat the rest as not yet knowable.
`availability_pct` is FPL's "chance of playing NEXT ROUND", not necessarily this fixture.
"""

import hashlib
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from src.data.teams import TeamDirectory
from src.mlops.oplog import logged_urlopen

from .interfaces import AvailabilityStatus, Capability, PlayerAvailability, ProviderMeta, Support
from .provider import ProviderError

URL = "https://fantasy.premierleague.com/api/bootstrap-static/"
SOURCE = "fpl"
STATUS_MAP = {
    "a": AvailabilityStatus.AVAILABLE,
    "d": AvailabilityStatus.DOUBTFUL,
    "i": AvailabilityStatus.INJURED,
    "s": AvailabilityStatus.SUSPENDED,
    "u": AvailabilityStatus.UNAVAILABLE,
    "n": AvailabilityStatus.UNAVAILABLE,
}

META = ProviderMeta(
    name=SOURCE,
    capabilities={
        Capability.FIXTURES: Support.NOT_SUPPORTED,
        Capability.LINEUPS: Support.NOT_SUPPORTED,
        Capability.INJURIES: Support.SUPPORTED,
        Capability.EVENTS: Support.NOT_SUPPORTED,
        Capability.ODDS: Support.NOT_SUPPORTED,
        Capability.STATISTICS: Support.NOT_SUPPORTED,
        Capability.XG: Support.NOT_SUPPORTED,
    },
    coverage="English Premier League players of the current FPL season only",
    timestamp_semantics="effective_at = news_added (provider publication time); observed_at = our "
    "fetch time; response is the current state, history is not available",
    rate_limit="undocumented; one request per snapshot run is made",
    license="unofficial public endpoint, terms not verified",
    license_status="RESEARCH_ONLY",
    provenance=URL,
    verified_on="2026-10-02",
)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


class FplInjuryProvider:
    meta = META

    def __init__(self, directory: TeamDirectory, raw_dir: Path | None = None, timeout: float = 20.0):
        self.directory = directory
        self.raw_dir = raw_dir  # raw responses are archived here (audit store) when set
        self.timeout = timeout
        self.last_raw_sha256: str | None = None

    def _fetch(self) -> bytes:
        req = urllib.request.Request(URL, headers={"User-Agent": "football-forecast-research"})
        try:
            return logged_urlopen("fpl", "bootstrap-static", req, self.timeout)
        except (urllib.error.URLError, TimeoutError) as e:
            raise ProviderError(f"fpl: {type(e).__name__}: {e}") from e

    def list_availability(self, observed_at: datetime | None = None) -> list[PlayerAvailability]:
        """Every player whose status is not plain AVAILABLE. A player absent from this list is
        'no flag from FPL' -- NOT proof that the player will play."""
        raw = self._fetch()
        observed = observed_at or datetime.now(UTC)
        self.last_raw_sha256 = hashlib.sha256(raw).hexdigest()
        if self.raw_dir is not None:
            self.raw_dir.mkdir(parents=True, exist_ok=True)
            stamp = observed.strftime("%Y%m%dT%H%M%SZ")
            (self.raw_dir / f"bootstrap_{stamp}_{self.last_raw_sha256[:12]}.json").write_bytes(raw)
        return parse_bootstrap(raw, self.directory, observed, self.last_raw_sha256)


def parse_bootstrap(
    raw: bytes, directory: TeamDirectory, observed: datetime, raw_sha256: str | None = None
) -> list[PlayerAvailability]:
    """Pure parser of a `bootstrap-static` response (real captures are parsed by the same code)."""
    data = json.loads(raw.decode("utf-8"))
    teams = {t["id"]: t["name"] for t in data["teams"]}
    out: list[PlayerAvailability] = []
    for p in data["elements"]:
        code = p.get("status")
        if code not in STATUS_MAP:
            raise ProviderError(f"fpl: unknown player status {code!r} (extend STATUS_MAP)")
        status = STATUS_MAP[code]
        if status == AvailabilityStatus.AVAILABLE:
            continue
        team_name = teams[p["team"]]
        res = directory.resolve(SOURCE, team_name, "ENG", observed.date())
        out.append(
            PlayerAvailability(
                player=p["web_name"],
                team_id=res.team_id,
                team_raw_name=team_name,
                status=status,
                availability_pct=p.get("chance_of_playing_next_round"),
                detail=p.get("news") or None,
                source=SOURCE,
                observed_at=observed,
                effective_at=_parse_ts(p.get("news_added")),
                confidence=None,  # FPL exposes a chance-of-playing %, not a confidence score
                provenance={
                    "endpoint": URL,
                    "fpl_player_id": p["id"],
                    "raw_status": code,
                    "raw_response_sha256": raw_sha256,
                },
            )
        )
    return out
