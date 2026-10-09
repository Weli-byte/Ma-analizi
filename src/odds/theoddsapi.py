"""The Odds API adapter -- the source of EXACT odds (ADR 0030): its v4 `h2h` market carries the
provider's own `last_update` for the quote, so `source_latency_s` is MEASURED.

STATUS: VERIFIED against a REAL response on 2026-10-08 (GitHub Actions run of `scripts/probe_theoddsapi.py`
and `tests/integration/test_theoddsapi_live.py`): sport keys `soccer_epl` / `soccer_spain_la_liga` exist,
events carry `home_team`, `away_team`, `commence_time`, `bookmakers[].markets[].last_update`,
`outcomes[{name, price}]` with the draw named "Draw". The free plan reported 500 credits; one league call
(`regions=uk,eu`, one market) cost 2. The API host is UNREACHABLE from some ISPs (TLS interference seen
from Turkey: `WRONG_VERSION_NUMBER`), so collection runs in the cloud (`.github/workflows/odds-exact.yml`).

Key handling: the provider accepts the key ONLY as the `apiKey` query parameter. It is never logged or
put in an exception: errors are scrubbed. Credits remaining come from the `x-requests-remaining` header.
Free-plan terms (redistribution) are not verified.
"""

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime

from src.ingestion.interfaces import Capability, ProviderMeta, Support
from src.llm.providers.base import scrub

from .quotes import MAX_CLOCK_SKEW_S, OddsQuote

BASE = "https://api.the-odds-api.com/v4"
SOURCE = "the-odds-api"
KEY_ENV = "THE_ODDS_API_KEY"
SPORT_KEYS = {  # all confirmed against the real /v4/sports response on 2026-10-09
    "PL": "soccer_epl",
    "PD": "soccer_spain_la_liga",
    "BL1": "soccer_germany_bundesliga",
    "SA": "soccer_italy_serie_a",
    "FL1": "soccer_france_ligue_one",
    "TR": "soccer_turkey_super_league",
}

META = ProviderMeta(
    name=SOURCE,
    capabilities={
        Capability.FIXTURES: Support.NOT_SUPPORTED,
        Capability.LINEUPS: Support.NOT_SUPPORTED,
        Capability.INJURIES: Support.NOT_SUPPORTED,
        Capability.EVENTS: Support.NOT_SUPPORTED,
        Capability.ODDS: Support.SUPPORTED,  # needs THE_ODDS_API_KEY; verified on a real response 2026-10-08
        Capability.STATISTICS: Support.NOT_SUPPORTED,
        Capability.XG: Support.NOT_SUPPORTED,
    },
    coverage="bookmakers per region for upcoming matches of the configured soccer sports (h2h market)",
    timestamp_semantics="markets[].last_update = the provider's own quote update time (documented); "
    "the bookmaker-level last_update is documented as deprecated and is not used",
    rate_limit="credit based; x-requests-remaining / x-requests-used / x-requests-last headers",
    license="free plan terms not verified (commercial redistribution not assumed)",
    license_status="UNVERIFIED",
    provenance=f"{BASE}/sports/{{sport}}/odds",
    verified_on="2026-10-08",
)


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def parse_event(event: dict, received_at: datetime, raw_sha256: str = "") -> dict:
    """Same shape as `espn.parse_event`. An outcome name that is neither the home team, the away team
    nor 'Draw' raises (never a partial / guessed line)."""
    fixture_id = f"oddsapi-{event['id']}"
    home, away = event["home_team"], event["away_team"]
    quotes: list[OddsQuote] = []
    for book in event.get("bookmakers", []):
        for market in book.get("markets", []):
            if market.get("key") != "h2h":
                continue
            ts_raw = market.get("last_update")
            provider_ts = _ts(ts_raw) if ts_raw else None
            latency = (received_at - provider_ts).total_seconds() if provider_ts else None
            exact = provider_ts is not None and latency >= -MAX_CLOCK_SKEW_S
            for out in market["outcomes"]:
                sel = (
                    "H"
                    if out["name"] == home
                    else "A"
                    if out["name"] == away
                    else "D"
                    if out["name"] == "Draw"
                    else None
                )
                if sel is None:
                    raise ValueError(f"unexpected h2h outcome {out['name']!r} for {home} v {away}")
                quotes.append(
                    OddsQuote(
                        # provider keys are unique; titles are not (two feeds are titled "Betfair")
                        source=SOURCE, bookmaker=f"{book['title']} ({book['key']})",
                        fixture_id=fixture_id, selection=sel,
                        decimal_odds=float(out["price"]), snapshot_type="pre_match", observed_at=received_at,
                        provider_timestamp=provider_ts if exact else None,
                        timestamp_quality="exact" if exact else "approximate",
                        source_latency_s=max(latency, 0.0) if exact else None,
                        raw_price=str(out["price"]),
                        provenance={"endpoint": f"{BASE}/sports/<sport>/odds", "event_id": event["id"],
                                    "bookmaker_key": book["key"], "raw_sha256": raw_sha256},
                    )
                )  # fmt: skip
    return {
        "event_id": event["id"], "fixture_id": fixture_id, "kickoff_utc": _ts(event["commence_time"]),
        "home_name": home, "away_name": away, "status": "STATUS_SCHEDULED", "quotes": quotes,
    }  # fmt: skip


class TheOddsApiFeed:
    meta = META

    def __init__(self, api_key: str, bookmakers: list[str] | None = None, timeout: float = 20.0):
        self.api_key = api_key
        self.bookmakers = bookmakers
        self.timeout = timeout
        self.requests_remaining: str | None = None

    def url(self, league: str) -> str:
        """`bookmakers=` (max 10 keys) costs ONE credit per call; `regions=` costs one per region and
        returns every bookmaker of the region (~120 quotes per event)."""
        params = {"markets": "h2h", "oddsFormat": "decimal", "dateFormat": "iso"}
        if self.bookmakers:
            params["bookmakers"] = ",".join(self.bookmakers)
        else:
            params["regions"] = "uk,eu"
        params["apiKey"] = self.api_key  # last: the only place the key appears is this query parameter
        return f"{BASE}/sports/{SPORT_KEYS[league]}/odds/?{urllib.parse.urlencode(params)}"

    def fetch(self, league: str, received_at: datetime | None = None) -> list[dict]:
        url = self.url(league)
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as r:  # noqa: S310 - fixed https host
                raw = r.read()
                self.requests_remaining = r.headers.get("x-requests-remaining")
        except (urllib.error.URLError, TimeoutError) as e:
            raise RuntimeError(
                f"the-odds-api request failed: {scrub(f'{type(e).__name__}: {e}', self.api_key)}"
            ) from None
        received = received_at or datetime.now(UTC)
        sha = hashlib.sha256(raw).hexdigest()
        return [parse_event(e, received, sha) for e in json.loads(raw.decode("utf-8"))]
