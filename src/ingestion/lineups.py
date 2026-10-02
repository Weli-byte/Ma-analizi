"""Lineups from ESPN's public match summary (keyless, UNOFFICIAL, RESEARCH_ONLY) -- ADR 0031.

Verified on a REAL finished Premier League match on 2026-10-02: `summary.rosters[]` has one entry per
side (`homeAway`, `formation`, `team`) with 20 players each, of which 11 have `starter: true`, plus
jersey, position and formation place. For a match that has not been played yet the same field is EMPTY
until the lineup is announced (seen on the real upcoming Arsenal v Leeds match); WHEN ESPN fills it
before kickoff has not been observed yet and is measured by the snapshot stages, not assumed.

Rules: a lineup is OBSERVED only if BOTH sides have exactly 11 starters; anything else is UNKNOWN
(`not_announced_yet`) -- never a partial or guessed lineup. ESPN states no announcement time, so only
`observed_at` (our fetch time) is recorded. Only starter/jersey/position/formation fields are read:
in-match fields (`subbedIn`, stats) exist on finished matches and are deliberately ignored.
"""

import hashlib
import json
import urllib.error
import urllib.request
from datetime import UTC, date, datetime

from src.data.teams import TeamDirectory

from .provider import ProviderError

BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
SOURCE = "espn"
LEAGUE_CODES = {"EPL": ("eng.1", "ENG"), "LALIGA": ("esp.1", "ESP")}  # repo league -> (ESPN code, country)
NOTE = "ESPN gives no announcement time: only observed_at is known; starters/formation only, no in-match data"


def _side(entry: dict) -> dict | None:
    players = entry.get("roster") or []
    starters = [p for p in players if p.get("starter")]
    if len(starters) != 11:
        return None

    def row(p: dict) -> dict:
        return {
            "player": p["athlete"]["displayName"],
            "jersey": p.get("jersey"),
            "position": (p.get("position") or {}).get("abbreviation"),
        }

    return {
        "formation": entry.get("formation"),
        "starters": [
            {**row(p), "formation_place": p.get("formationPlace")}
            for p in sorted(starters, key=lambda p: int(p.get("formationPlace") or 99))
        ],
        "substitutes": [row(p) for p in players if not p.get("starter")],
    }


def parse_lineups(summary: dict, observed_at: datetime, raw_sha256: str = "", event_id: str = "") -> dict:
    """Pure parser of an ESPN `summary` payload -> an availability block (OBSERVED | UNKNOWN)."""
    sides = {r.get("homeAway"): _side(r) for r in summary.get("rosters", [])}
    if not sides.get("home") or not sides.get("away"):
        return {
            "status": "UNKNOWN", "reason": "not_announced_yet", "source": SOURCE,
            "observed_at": observed_at.isoformat(), "event_id": event_id,
        }  # fmt: skip
    return {
        "status": "OBSERVED", "source": SOURCE, "event_id": event_id,
        "observed_at": observed_at.isoformat(), "raw_response_sha256": raw_sha256,
        "home": sides["home"], "away": sides["away"], "note": NOTE,
    }  # fmt: skip


def _get(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "football-forecast-research"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed https host
            return r.read()
    except (urllib.error.URLError, TimeoutError) as e:
        raise ProviderError(f"espn: {type(e).__name__}: {e}") from e


class EspnLineupProvider:
    def __init__(self, directory: TeamDirectory, timeout: float = 20.0):
        self.directory = directory
        self.timeout = timeout

    def find_event_id(self, repo_league: str, home_id: str, away_id: str, kickoff: datetime) -> str | None:
        """The ESPN event of this fixture (matched on resolved team ids and the match day), or None."""
        code, country = LEAGUE_CODES[repo_league]
        day: date = kickoff.astimezone(UTC).date()
        raw = _get(f"{BASE}/{code}/scoreboard?dates={day:%Y%m%d}", self.timeout)
        for ev in json.loads(raw.decode("utf-8")).get("events", []):
            teams = {
                c["homeAway"]: self.directory.resolve(SOURCE, c["team"]["displayName"], country, day).team_id
                for c in ev["competitions"][0]["competitors"]
            }
            if teams.get("home") == home_id and teams.get("away") == away_id:
                return ev["id"]
        return None

    def fetch_lineups(self, repo_league: str, event_id: str, observed_at: datetime | None = None) -> dict:
        code, _ = LEAGUE_CODES[repo_league]
        raw = _get(f"{BASE}/{code}/summary?event={event_id}", self.timeout)
        observed = observed_at or datetime.now(UTC)
        return parse_lineups(
            json.loads(raw.decode("utf-8")), observed, hashlib.sha256(raw).hexdigest(), event_id
        )
