"""Highlightly football API (direct host): match statistics and the Super Lig history gap (ADR 0050).

Verified with real calls on 2026-10-11 (key in `HIGHLIGHTLY_API_KEY`, header `x-rapidapi-key`; the host is
unreachable from the owner's network, so calls run on GitHub's runners):
- `/matches?leagueId=&season=&limit=100&offset=` returns {data, pagination{totalCount, offset, limit},
  plan{tier}}, newest match first. A match has id, date, state{description, score{current: "h - a"}}, homeTeam,
  awayTeam and league. `season` is the start year (2025 = 2025-26).
- `/statistics/{matchId}` returns [{team{id, name}, statistics[{displayName, value}]}] for home then away.
  Corners, Yellow cards, Shots on target and Shots off target were present for every sampled Premier League match
  2022-2025; Red cards and Expected Goals only for some. A missing statistic is None, never 0.
- Rate limit headers `x-ratelimit-requests-limit/remaining` (BASIC plan: 100 requests per day).
Terms (highlightly.net/terms, read 2026-10-09): clause 6.1 allows storing and using the data in your products and
forbids reselling or proxying API access; clause 4.1 says the free Basic plan "is not subject to the same terms as
paid plans", so published use needs a paid plan (PRO, USD 9.49/month).
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from src.mlops.oplog import record_call

from .endpoints import endpoint
from .provider import ProviderError

BASE = "https://soccer.highlightly.net"
SOURCE = "highlightly"
LEAGUE_IDS = {  # read from the real /leagues responses
    "EPL": 33973,
    "LALIGA": 119924,
    "BUNDESLIGA": 67162,
    "SERIEA": 115669,
    "LIGUE1": 52695,
    "SUPERLIG": 173537,
}
STAT_FIELDS = {
    "Corners": "corners",
    "Yellow cards": "yellow_cards",
    "Red cards": "red_cards",
    "Shots on target": "shots_on_target",
    "Shots off target": "shots_off_target",
    "Expected Goals": "expected_goals",
}


@dataclass
class Page:
    data: list[dict]
    total: int
    plan: str | None
    remaining: int | None


def parse_statistics(raw: list[dict]) -> dict[int, dict]:
    """team id -> {field: value|None}. Absent statistics stay None (never imputed as 0)."""
    out = {}
    for entry in raw:
        vals = {f: None for f in STAT_FIELDS.values()}
        for s in entry.get("statistics", []):
            f = STAT_FIELDS.get(s.get("displayName"))
            if f is not None:
                vals[f] = s.get("value")
        out[entry["team"]["id"]] = vals if entry.get("statistics") else None
    return out


def parse_score(state: dict) -> tuple[int, int] | None:
    cur = ((state or {}).get("score") or {}).get("current")
    if not cur or "-" not in cur:
        return None
    h, a = (x.strip() for x in cur.split("-", 1))
    return (int(h), int(a)) if h.isdigit() and a.isdigit() else None


class HighlightlyClient:
    def __init__(self, api_key: str, timeout: float = 30.0):
        self.api_key = api_key
        self.timeout = timeout
        self.remaining: int | None = None
        self.calls = 0

    def _get(self, path: str, label: str, **params):
        url = f"{endpoint('HIGHLIGHTLY_BASE_URL', BASE)}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"x-rapidapi-key": self.api_key})
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:  # noqa: S310 - fixed https host
                raw = r.read()
                rem = r.headers.get("x-ratelimit-requests-remaining")
                status = r.status
        except urllib.error.HTTPError as e:
            record_call(SOURCE, label, False, (time.monotonic() - t0) * 1000, e.code, f"HTTP {e.code}")
            raise ProviderError(f"highlightly: HTTP {e.code}") from None  # message never carries the key
        except (urllib.error.URLError, TimeoutError) as e:
            record_call(SOURCE, label, False, (time.monotonic() - t0) * 1000, None, type(e).__name__)
            raise ProviderError(f"highlightly: {type(e).__name__}") from None
        record_call(SOURCE, label, True, (time.monotonic() - t0) * 1000, status, None, len(raw))
        self.calls += 1
        if rem is not None and rem.isdigit():
            self.remaining = int(rem)
        return json.loads(raw.decode("utf-8"))

    def matches(self, league_id: int, season: int, offset: int = 0, limit: int = 100) -> Page:
        d = self._get("/matches", "matches", leagueId=league_id, season=season, limit=limit, offset=offset)
        return Page(
            d["data"], d["pagination"]["totalCount"], (d.get("plan") or {}).get("tier"), self.remaining
        )

    def statistics(self, match_id: int) -> dict[int, dict]:
        return parse_statistics(self._get(f"/statistics/{match_id}", "statistics"))
