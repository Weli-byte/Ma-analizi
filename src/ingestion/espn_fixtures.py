"""ESPN public scoreboard as a FREE fixture / result list for competitions the other free sources do not cover (UNOFFICIAL, RESEARCH_ONLY).

Why (2026-10-22): The Odds API stopped listing the Europa League and the Conference League (`/v4/sports` no longer contains `soccer_uefa_europa_league` /
`soccer_uefa_europa_conference_league`, confirmed by the probe-cups workflow run 39859449706), football-data.org answers HTTP 403 for the Europa League with the free key, and
openfootball has no league-phase file for 2025-26. ESPN's public scoreboard lists both competitions (verified live on 2026-10-22: `uefa.europa` and `uefa.europa.conf`, 18 events
per matchday, finished results with scores, scheduled matches up to the next matchday).

Behaviour that matters: the scoreboard answers ONE DAY per request (`?dates=YYYYMMDD`; a range answers HTTP 400), so a window is scanned day by day. This module only READS the list of
matches (no odds, no lineups); the endpoint terms are unverified, so the data is research use only, exactly like the ESPN odds collector of ADR 0030. A failing day is reported, never
filled in, and a club name that the directory cannot resolve is skipped by the caller (never guessed)."""

import json
import time
import urllib.request
from datetime import UTC, date, datetime, timedelta

from src.mlops.oplog import logged_urlopen

from .provider import ProviderError

BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
SOURCE = "espn"
SLUGS = {"UEL": "uefa.europa", "UECL": "uefa.europa.conf", "UCL": "uefa.champions"}


def fetch_day(slug: str, day: date, timeout: float = 30.0) -> list[dict]:
    url = f"{BASE}/{slug}/scoreboard?dates={day.strftime('%Y%m%d')}"
    try:
        raw = logged_urlopen(SOURCE, "scoreboard", urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout)
        return json.loads(raw.decode("utf-8")).get("events", [])
    except Exception as e:  # noqa: BLE001 - re-raised as the provider error type, name only
        raise ProviderError(f"espn {slug} {day}: {type(e).__name__}") from None


def parse_event(ev: dict) -> dict | None:
    """-> {event_id, kickoff_utc, home, away, status, completed, home_goals, away_goals}; None for a payload that is not a two-club match."""
    try:
        comp = ev["competitions"][0]
        sides = {c["homeAway"]: c for c in comp["competitors"]}
        home, away = sides["home"], sides["away"]
        ko = datetime.fromisoformat(ev["date"].replace("Z", "+00:00")).astimezone(UTC)
        status = ev["status"]["type"]
        completed = bool(status.get("completed"))
        def goals(s):
            return int(s["score"]) if completed and str(s.get("score", "")).strip().lstrip("-").isdigit() else None
        return {"event_id": str(ev["id"]), "kickoff_utc": ko, "home": home["team"]["displayName"], "away": away["team"]["displayName"], "status": status.get("name"),
                "completed": completed, "home_goals": goals(home), "away_goals": goals(away)}  # fmt: skip
    except (KeyError, IndexError, ValueError, TypeError):
        return None


def scan(slug: str, start: date, end: date, delay: float = 0.15) -> tuple[list[dict], list[str]]:
    """Every parsed match of the days start..end (inclusive), de-duplicated by event id, plus the days that failed."""
    out: dict[str, dict] = {}
    errors: list[str] = []
    d = start
    while d <= end:
        try:
            for ev in fetch_day(slug, d):
                row = parse_event(ev)
                if row is not None:
                    out[row["event_id"]] = row
        except ProviderError as e:
            errors.append(str(e))
        d += timedelta(days=1)
        time.sleep(delay)
    return sorted(out.values(), key=lambda r: r["kickoff_utc"]), errors
