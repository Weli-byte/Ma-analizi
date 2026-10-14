"""Injuries and suspensions shown next to a forecast, INFORMATION ONLY (ADR 0057).

Source: API-Football free plan (`/injuries?date=`, a rolling window [yesterday, tomorrow], every league in one call,
100 requests/day; see `src/ingestion/api_football.py`). The player list never enters a probability: no history exists
to calibrate an injury effect on, so using it would be a guess. Each stored file is immutable, carries `observed_at`
(the entries have no report time) and the SHA-256 of the raw response. A club the directory cannot resolve is dropped
and counted, never guessed.

`python -m src.markets.injuries` collects today and tomorrow into `artifacts/injuries/<day>/inj-<stamp>.json`.
"""

import hashlib
import json
import os
import urllib.error
import urllib.request
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from src.cli_utils import configure_output, load_dotenv
from src.config import config_dir_for
from src.data.teams import TeamDirectory
from src.ingestion.api_football import BASE, SOURCE
from src.ingestion.endpoints import endpoint
from src.mlops.oplog import logged_urlopen

ROOT = Path(__file__).resolve().parents[2]
KEY_ENV = "API_FOOTBALL_KEY"
SOURCES = (SOURCE, "the-odds-api", "openfootball-cups", "openfootball")
WEB_LABEL = {"DOUBTFUL": "Şüpheli", "INJURED": "Sakat", "SUSPENDED": "Cezalı", "UNAVAILABLE": "Kadro dışı"}


def status_of(api_type: str | None, reason: str | None) -> str:
    """DOUBTFUL / SUSPENDED / UNAVAILABLE (not in the squad for a non-medical reason) / INJURED (everything else
    the provider lists as missing the fixture: it is an injury unless the reason says otherwise)."""
    why = (reason or "").lower()
    if (api_type or "").lower() == "questionable":
        return "DOUBTFUL"
    if "suspen" in why or "card" in why:
        return "SUSPENDED"
    if any(
        w in why for w in ("roster", "not registered", "national team", "international", "personal", "coach")
    ):
        return "UNAVAILABLE"
    return "INJURED"


def fetch(key: str, day: date, timeout: float = 30.0) -> bytes:
    url = f"{endpoint('API_FOOTBALL_BASE_URL', BASE)}/injuries?date={day.isoformat()}"
    req = urllib.request.Request(url, headers={"x-apisports-key": key})
    try:
        return logged_urlopen(SOURCE, "injuries", req, timeout)
    except (urllib.error.URLError, TimeoutError) as e:
        raise RuntimeError(f"api-football: {type(e).__name__}") from None  # the message never carries the key


def parse(raw: bytes, directory: TeamDirectory, observed_at: datetime) -> dict:
    """Pure: {"rows": [...], "unresolved_clubs": n, ...}. A plan/error payload raises (never an empty 'no injuries')."""
    data = json.loads(raw.decode("utf-8"))
    if data.get("errors"):
        raise RuntimeError(f"api-football: {data['errors']}")
    rows, unresolved = [], 0
    for r in data.get("response", []):
        kickoff = datetime.fromisoformat(r["fixture"]["date"]).astimezone(UTC)
        team = directory.resolve_any_country(SOURCES, r["team"]["name"], kickoff.date())
        if team.team_id is None:
            unresolved += 1
            continue
        rows.append(
            {
                "team_id": team.team_id,
                "team_name": r["team"]["name"],
                "player": r["player"]["name"],
                "status": status_of(r["player"].get("type"), r["player"].get("reason")),
                "reason": r["player"].get("reason"),
                "api_type": r["player"].get("type"),
                "kickoff_utc": kickoff.isoformat(),
                "league_id": r["league"]["id"],
            }
        )
    return {
        "source": SOURCE,
        "observed_at": observed_at.isoformat(),
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "unresolved_clubs": unresolved,
        "rows": rows,
    }


def collect(root: Path, key: str, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(UTC)
    directory = TeamDirectory.load(config_dir_for(root) / "team_aliases.yaml")
    lines = []
    for day in (now.date(), now.date() + timedelta(days=1)):
        try:
            body = parse(fetch(key, day), directory, now)
        except RuntimeError as e:
            lines.append(f"{day}: FAILED ({e})")
            continue
        out = root / "artifacts" / "injuries" / day.isoformat()
        out.mkdir(parents=True, exist_ok=True)
        previous = sorted(out.glob("inj-*.json"))
        if (
            previous
            and json.loads(previous[-1].read_text(encoding="utf-8"))["raw_sha256"] == body["raw_sha256"]
        ):
            lines.append(f"{day}: unchanged since the last observation, nothing stored")
            continue
        path = out / f"inj-{now.strftime('%Y%m%dT%H%M%SZ')}.json"
        path.write_text(json.dumps(body, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
        lines.append(
            f"{day}: {len(body['rows'])} entries kept, {body['unresolved_clubs']} club(s) unresolved"
        )
    return lines


def latest_for(root: Path, home_id: str, away_id: str, kickoff: datetime) -> dict | None:
    """Newest stored observation for the kickoff day: {"observed_at", "home": [...], "away": [...]} or None when
    nothing was observed (the page then says so instead of implying nobody is injured)."""
    day = kickoff.astimezone(UTC).date()
    files = sorted((Path(root) / "artifacts" / "injuries" / day.isoformat()).glob("inj-*.json"))
    if not files:
        return None
    body = json.loads(files[-1].read_text(encoding="utf-8"))
    near = [
        r
        for r in body["rows"]
        if abs(datetime.fromisoformat(r["kickoff_utc"]) - kickoff) < timedelta(hours=6)
    ]

    def side(team_id: str) -> list[dict]:
        seen, out = set(), []
        for r in near:  # the provider lists a player once per competition: keep one row per player
            if r["team_id"] == team_id and r["player"] not in seen:
                seen.add(r["player"])
                out.append(r)
        return out

    return {"observed_at": body["observed_at"], "home": side(home_id), "away": side(away_id)}


def main(argv=None) -> int:
    configure_output()
    load_dotenv()
    key = os.environ.get(KEY_ENV)
    if not key:
        print(f"NOT_CONFIGURED: ${KEY_ENV} not set")
        return 2
    print("\n".join(collect(ROOT, key)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
