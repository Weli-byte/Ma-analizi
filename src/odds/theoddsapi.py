"""The Odds API adapter -- the route to EXACT odds (ADR 0030): its v4 `h2h` market carries the
provider's own `last_update` for the quote, so `source_latency_s` can be MEASURED.

STATUS: NOT_CONFIGURED / UNVERIFIED. No key exists on this machine, so no real response has ever
been seen: the field names below follow the provider's v4 documentation and have NOT been checked
against a real payload (`verified_on=None`). It becomes operational only after
`pytest -m live tests/integration/test_theoddsapi_live.py` receives and parses a real response.
Until then `collect` reports NOT_CONFIGURED and every odds-based edge/EV/CLV stays unavailable.

Key handling: the provider accepts the key ONLY as the `apiKey` query parameter (documented). It is
never logged or put in an exception: errors are scrubbed. Free-tier credit limits are not stated in
the docs we could read; the `x-requests-remaining` header is recorded per call instead of assumed.
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
SPORT_KEYS = {"PL": "soccer_epl", "PD": "soccer_spain_la_liga"}  # confirm against /v4/sports on first call

META = ProviderMeta(
    name=SOURCE,
    capabilities={
        Capability.FIXTURES: Support.NOT_SUPPORTED,
        Capability.LINEUPS: Support.NOT_SUPPORTED,
        Capability.INJURIES: Support.NOT_SUPPORTED,
        Capability.EVENTS: Support.NOT_SUPPORTED,
        Capability.ODDS: Support.NOT_CONFIGURED,  # needs THE_ODDS_API_KEY; unverified until a real response
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
    verified_on=None,
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
                        source=SOURCE, bookmaker=book["title"], fixture_id=fixture_id, selection=sel,
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

    def __init__(self, api_key: str, timeout: float = 20.0):
        self.api_key = api_key
        self.timeout = timeout
        self.requests_remaining: str | None = None

    def fetch(self, league: str, received_at: datetime | None = None) -> list[dict]:
        query = urllib.parse.urlencode(
            {
                "regions": "uk,eu",
                "markets": "h2h",
                "oddsFormat": "decimal",
                "dateFormat": "iso",
                "apiKey": self.api_key,
            }
        )
        url = f"{BASE}/sports/{SPORT_KEYS[league]}/odds/?{query}"
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
