"""Live feeds -> normalized `LiveSnapshot` (ADR 0029). Parsers are pure functions over a provider
payload (real captures are parsed by the same code); `poll` adds the HTTP call.

Verified on REAL data (2026-10-02):
- OpenLigaDB (community, keyless): per-match `goals` with minute, scorer, penalty/own-goal flags
  and running score, `matchIsFinished`, `matchResults`. No current minute, no cards, no
  substitutions, no VAR. Bundesliga / 2. Bundesliga / 3. Liga etc.
- football-data.org (free tier): `status`, `score.fullTime/halfTime`. No goal list, no cards, no
  substitutions. NOT yet seen on a real in-play match (none was running at implementation time):
  the in-play shape (`IN_PLAY`/`PAUSED`, running `fullTime`) follows the v4 docs and is verified
  at the first real live match.

Nothing is assumed: a status the feed does not state is inferred from the clock and labelled
`inferred_from_clock`; a score the feed does not state is `None`.
"""

import hashlib
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime

from src.ingestion.interfaces import Capability, ProviderMeta, Support

from .events import EventType, LiveEvent, LiveSnapshot, MatchStatus

OLDB_BASE = "https://api.openligadb.de"
FDORG_BASE = "https://api.football-data.org/v4"
HALF_TIME_BREAK_MIN = 15  # assumed length of the interval when inferring the minute from the clock

OLDB_META = ProviderMeta(
    name="openligadb",
    capabilities={
        Capability.FIXTURES: Support.NOT_SUPPORTED,
        Capability.LINEUPS: Support.NOT_SUPPORTED,
        Capability.INJURIES: Support.NOT_SUPPORTED,
        Capability.EVENTS: Support.SUPPORTED,  # goals only (minute, scorer, penalty, own goal)
        Capability.ODDS: Support.NOT_SUPPORTED,
        Capability.STATISTICS: Support.NOT_SUPPORTED,
        Capability.XG: Support.NOT_SUPPORTED,
    },
    coverage="German leagues (Bundesliga, 2. Bundesliga, 3. Liga, cups); events = goals only",
    timestamp_semantics="goal.matchMinute = minute of the goal; lastUpdateDateTime = last edit by the "
    "community editors (entry may lag the real event)",
    rate_limit="no documented limit; polled at most once per 30 s per match",
    license="community project, terms not verified",
    license_status="RESEARCH_ONLY",
    provenance=f"{OLDB_BASE}/getmatchdata/{{matchID}}",
    verified_on="2026-10-02",
)


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def infer_minute(
    kickoff: datetime, now: datetime, status: MatchStatus, last_event_minute: int | None
) -> tuple[int | None, str | None]:
    """(minute, source). A reported minute is preferred by the caller; this is the clock fallback,
    always labelled. Half-time break length is an assumption (HALF_TIME_BREAK_MIN)."""
    if status == MatchStatus.HALF_TIME:
        return 45, "inferred_from_clock"
    elapsed = (now - kickoff).total_seconds() / 60
    if elapsed < 0:
        return None, None
    if elapsed <= 45:
        minute = int(elapsed)
    elif elapsed <= 45 + HALF_TIME_BREAK_MIN:
        minute = 45
    else:
        minute = min(int(elapsed - HALF_TIME_BREAK_MIN), 120)
    if last_event_minute is not None and last_event_minute > minute:
        return last_event_minute, "last_event"
    return minute, "inferred_from_clock"


# ------------------------------------------------------------------------------- OpenLigaDB
def parse_openligadb_match(payload: dict, observed_at: datetime, raw_sha256: str = "") -> LiveSnapshot:
    fixture_id = f"oldb-{payload['matchID']}"
    kickoff = _ts(payload["matchDateTimeUTC"])
    finished = bool(payload["matchIsFinished"])
    goals = sorted(payload.get("goals") or [], key=lambda g: (g["scoreTeam1"] + g["scoreTeam2"], g["goalID"]))

    events, prev = [], (0, 0)
    for g in goals:
        score = (g["scoreTeam1"], g["scoreTeam2"])
        d_home, d_away = score[0] - prev[0], score[1] - prev[1]
        team = "home" if d_home > 0 and d_away == 0 else "away" if d_away > 0 and d_home == 0 else None
        etype = (
            EventType.OWN_GOAL
            if g.get("isOwnGoal")
            else EventType.PENALTY_GOAL
            if g.get("isPenalty")
            else EventType.GOAL
        )
        detail = "overtime" if g.get("isOvertime") else None
        events.append(
            LiveEvent(
                fixture_id, etype, team, g.get("matchMinute"), g.get("goalGetterName"), detail,
                "openligadb", observed_at, None, False, score,
                {"goalID": g["goalID"], "scoringTeamId": g.get("scoringTeamId")},
            )
        )  # fmt: skip
        prev = score

    if finished:
        status, status_source = MatchStatus.FINISHED, "reported"
        final = next((r for r in payload["matchResults"] if r["resultTypeKind"] == "After90Minutes"), None)
        score = (final["pointsTeam1"], final["pointsTeam2"]) if final else (prev if goals else None)
    elif observed_at < kickoff:
        status, status_source, score = MatchStatus.NOT_STARTED, "inferred_from_clock", None
    else:
        status, status_source = MatchStatus.IN_PLAY, "inferred_from_clock"
        score = prev  # (0, 0) when the editors have listed no goal yet; may lag the real match
    caps = {"goals": True, "cards": False, "substitutions": False, "var": False,
            "minute": False, "status": False, "score": True}  # fmt: skip
    return LiveSnapshot(
        fixture_id, "openligadb", observed_at, kickoff, status, status_source,
        "matchIsFinished=true" if finished else "matchIsFinished=false", score, None,
        tuple(events), caps, raw_sha256,
    )  # fmt: skip


# ---------------------------------------------------------------------------- football-data.org
FDORG_STATUS = {
    "SCHEDULED": MatchStatus.NOT_STARTED,
    "TIMED": MatchStatus.NOT_STARTED,
    "IN_PLAY": MatchStatus.IN_PLAY,
    "LIVE": MatchStatus.IN_PLAY,
    "PAUSED": MatchStatus.HALF_TIME,
    "FINISHED": MatchStatus.FINISHED,
    "AWARDED": MatchStatus.FINISHED,
}  # POSTPONED / SUSPENDED / CANCELLED / unknown codes -> UNKNOWN (never guessed)


def parse_fdorg_match(payload: dict, observed_at: datetime, raw_sha256: str = "") -> LiveSnapshot:
    fixture_id = f"fdorg-{payload['id']}"
    raw_status = payload["status"]
    status = FDORG_STATUS.get(raw_status, MatchStatus.UNKNOWN)
    full = (payload.get("score") or {}).get("fullTime") or {}
    score = (
        (full["home"], full["away"])
        if full.get("home") is not None and full.get("away") is not None
        else None
    )
    minute = payload.get("minute")
    caps = {"goals": False, "cards": False, "substitutions": False, "var": False,
            "minute": minute is not None, "status": True, "score": True}  # fmt: skip
    return LiveSnapshot(
        fixture_id, "football-data-org", observed_at, _ts(payload["utcDate"]), status, "reported",
        raw_status, score, int(minute) if minute is not None else None, (), caps, raw_sha256,
    )  # fmt: skip


def derive_goal_events(prev_score: tuple[int, int] | None, snap: LiveSnapshot) -> list[LiveEvent]:
    """For feeds without a goal list: a score increase between two polls proves goal(s) happened
    SOMEWHERE in the poll interval. Minute/scorer/penalty detail are unknown and left None."""
    if snap.score is None or prev_score is None or snap.capabilities.get("goals"):
        return []
    out = []
    for side, i in (("home", 0), ("away", 1)):
        for k in range(prev_score[i] + 1, snap.score[i] + 1):
            after = (k, snap.score[1]) if side == "home" else (snap.score[0], k)
            out.append(
                LiveEvent(snap.fixture_id, EventType.GOAL, side, None, None,
                          "derived from a score change between polls", snap.source,
                          snap.observed_at, None, True, after, {"prev_score": list(prev_score)})
            )  # fmt: skip
    return out


# ----------------------------------------------------------------------------------- HTTP
def _get(url: str, headers: dict, timeout: float = 20.0) -> bytes:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed https hosts
            return r.read()
    except (urllib.error.URLError, TimeoutError) as e:
        raise RuntimeError(f"live feed request failed: {type(e).__name__}: {e}") from e


class OpenLigaDBFeed:
    meta = OLDB_META

    def poll(self, match_id: str, now: datetime | None = None) -> LiveSnapshot:
        raw = _get(f"{OLDB_BASE}/getmatchdata/{match_id}", {"User-Agent": "football-forecast-research"})
        return parse_openligadb_match(json.loads(raw.decode("utf-8")), now or datetime.now(UTC), _sha(raw))

    def list_current(self, league: str, now: datetime | None = None) -> list[dict]:
        """Matches of the current matchday that are in progress by the clock and not finished."""
        now = now or datetime.now(UTC)
        raw = _get(f"{OLDB_BASE}/getmatchdata/{league}", {"User-Agent": "football-forecast-research"})
        out = []
        for m in json.loads(raw.decode("utf-8")):
            ko = _ts(m["matchDateTimeUTC"])
            if not m["matchIsFinished"] and 0 <= (now - ko).total_seconds() <= 3.5 * 3600:
                out.append(m)
        return out


class FootballDataOrgLiveFeed:
    def __init__(self, api_key: str):
        self.api_key = api_key

    def _h(self) -> dict:
        return {"X-Auth-Token": self.api_key}

    def poll(self, match_id: str, now: datetime | None = None) -> LiveSnapshot:
        raw = _get(f"{FDORG_BASE}/matches/{match_id}", self._h())
        return parse_fdorg_match(json.loads(raw.decode("utf-8")), now or datetime.now(UTC), _sha(raw))

    def list_live(self, competition: str) -> list[dict]:
        raw = _get(f"{FDORG_BASE}/competitions/{competition}/matches?status=IN_PLAY,PAUSED", self._h())
        return json.loads(raw.decode("utf-8")).get("matches", [])
