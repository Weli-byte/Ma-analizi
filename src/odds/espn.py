"""ESPN public scoreboard -> real DraftKings 1X2 moneylines (keyless, UNOFFICIAL, RESEARCH_ONLY).

Verified live on 2026-10-02: `site.api.espn.com/apis/site/v2/sports/soccer/{eng.1|esp.1}/scoreboard`
lists the current matchweek's events; each competition carries `odds[0]` (provider DraftKings) with
`moneyline.{home,draw,away}.{open,close}.odds` as American prices. `close` is the CURRENT line as of
the request (the match has not been played), not a final closing price. ESPN states no quote
timestamp (the payload was searched for one on 2026-10-02: none), so quotes are `approximate`: they
are kept as a time series and a market reference, and can NOT drive edge/EV/CLV (ADR 0030).
Endpoint terms are not verified: research use only, not a licensed odds feed.
"""

import hashlib
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime

from src.ingestion.interfaces import Capability, ProviderMeta, Support

from .quotes import OddsQuote, american_to_decimal

BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
SOURCE = "espn"
LEAGUE_CODES = {"PL": ("eng.1", "EPL", "ENG"), "PD": ("esp.1", "LALIGA", "ESP")}
SIDES = (("home", "H"), ("draw", "D"), ("away", "A"))

META = ProviderMeta(
    name=SOURCE,
    capabilities={
        Capability.FIXTURES: Support.NOT_SUPPORTED,
        Capability.LINEUPS: Support.SUPPORTED,  # /summary rosters, verified on a finished match
        Capability.INJURIES: Support.NOT_SUPPORTED,  # its injuries endpoint returned an empty list
        Capability.EVENTS: Support.NOT_SUPPORTED,
        Capability.ODDS: Support.SUPPORTED,
        Capability.STATISTICS: Support.NOT_SUPPORTED,
        Capability.XG: Support.NOT_SUPPORTED,
    },
    coverage="eng.1 and esp.1: DraftKings 1X2 moneyline (current matchweek) and match-summary lineups "
    "(empty until announced; announcement timing not yet observed)",
    timestamp_semantics="no quote timestamp from the source (only our fetch time), so quotes are "
    "'approximate', never 'exact'; 'close' = current line, not a final closing price",
    rate_limit="undocumented; one request per league per collection run",
    license="unofficial public endpoint, terms not verified",
    license_status="RESEARCH_ONLY",
    provenance=f"{BASE}/{{league}}/scoreboard",
    verified_on="2026-10-02",
)


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def parse_event(event: dict, observed_at: datetime, raw_sha256: str = "") -> dict | None:
    """-> {"event_id", "kickoff_utc", "home_name", "away_name", "status", "quotes": [OddsQuote]}.
    `quotes` is empty when the event carries no complete 1X2 moneyline (never partially filled)."""
    comp = event["competitions"][0]
    teams = {c["homeAway"]: c["team"]["displayName"] for c in comp["competitors"]}
    fixture_id = f"espn-{event['id']}"
    quotes: list[OddsQuote] = []
    odds = (comp.get("odds") or [None])[0]
    ml = (odds or {}).get("moneyline") or {}
    if odds and all(side in ml and "odds" in (ml[side].get("close") or {}) for side, _ in SIDES):
        book = odds["provider"]["name"]
        prov = {
            "endpoint": f"{BASE}/<league>/scoreboard",
            "espn_event_id": event["id"],
            "raw_sha256": raw_sha256,
        }
        for side, sel in SIDES:
            close = ml[side]["close"]["odds"]
            quotes.append(
                OddsQuote(
                    source=SOURCE, bookmaker=book, fixture_id=fixture_id, selection=sel,
                    decimal_odds=american_to_decimal(close), snapshot_type="pre_match",
                    observed_at=observed_at, timestamp_quality="approximate", raw_price=str(close),
                    provenance={**prov, "field": f"moneyline.{side}.close"},
                )
            )  # fmt: skip
            open_ = (ml[side].get("open") or {}).get("odds")
            if open_ is not None:  # the source gives no time for the opening price: never 'exact'
                quotes.append(
                    OddsQuote(
                        source=SOURCE, bookmaker=book, fixture_id=fixture_id, selection=sel,
                        decimal_odds=american_to_decimal(open_), snapshot_type="opening",
                        observed_at=observed_at, timestamp_quality="unknown", raw_price=str(open_),
                        provenance={**prov, "field": f"moneyline.{side}.open"},
                    )
                )  # fmt: skip
    return {
        "event_id": event["id"], "fixture_id": fixture_id, "kickoff_utc": _ts(event["date"]),
        "home_name": teams["home"], "away_name": teams["away"],
        "status": event["status"]["type"]["name"], "quotes": quotes,
    }  # fmt: skip


class EspnOddsFeed:
    meta = META

    def __init__(self, timeout: float = 20.0):
        self.timeout = timeout

    def fetch(self, league: str, observed_at: datetime | None = None) -> list[dict]:
        code = LEAGUE_CODES[league][0]
        req = urllib.request.Request(
            f"{BASE}/{code}/scoreboard", headers={"User-Agent": "football-forecast-research"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:  # noqa: S310 - fixed https URL
                raw = r.read()
        except (urllib.error.URLError, TimeoutError) as e:
            raise RuntimeError(f"espn scoreboard request failed: {type(e).__name__}: {e}") from e
        observed = observed_at or datetime.now(UTC)
        sha = hashlib.sha256(raw).hexdigest()
        return [parse_event(e, observed, sha) for e in json.loads(raw.decode("utf-8"))["events"]]
