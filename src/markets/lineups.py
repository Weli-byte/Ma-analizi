"""Starting line-ups shown next to a forecast, INFORMATION ONLY (ADR 0081).

Source: ESPN's public match summary (`/summary?event=<id>`, keyless, UNOFFICIAL, RESEARCH_ONLY like the ESPN fixtures of ADR 0070). A line-up exists only once the clubs announce it
(about an hour before kick-off), so the collector looks at the matches that start within the next 100 minutes, every few minutes (it runs inside the shadow cloud loop). The line-up never
enters a probability: the features of the models do not include line-ups (they stay UNKNOWN in every record), and no history exists to calibrate a line-up effect on.
Each stored file is immutable, carries `observed_at` and the SHA-256 of the raw response; a changed line-up before kick-off is stored as a new file; a club the directory cannot resolve is
dropped and counted, never guessed.

`python -m src.markets.lineups` collects into `artifacts/lineups/<fixture_key>/lineup-<stamp>.json` (the fixture key is the one of the forecast artifacts)."""

import hashlib
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.cli_utils import configure_output
from src.config import config_dir_for
from src.data.teams import TeamDirectory
from src.ingestion import espn_fixtures
from src.mlops.oplog import logged_urlopen

ROOT = Path(__file__).resolve().parents[2]
SLUGS = {
    "EPL": "eng.1", "LALIGA": "esp.1", "BUNDESLIGA": "ger.1", "SERIEA": "ita.1", "LIGUE1": "fra.1", "SUPERLIG": "tur.1",
    "UCL": "uefa.champions", "UEL": "uefa.europa", "UECL": "uefa.europa.conf",
}  # fmt: skip
SOURCES = (espn_fixtures.SOURCE, "openfootball", "openfootball-cups", "football-data-org", "the-odds-api")
WINDOW_BEFORE = timedelta(minutes=100)     # a match is looked at from this long before kick-off
WINDOW_AFTER = timedelta(minutes=10)       # ... until a few minutes after (a line-up announced late is still wanted for the page)
KEEP_DAYS = 730      # the stored line-ups are the evidence of the as-of line-up history (ADR 0086); the site bundle ships only the last days


def fetch_summary(slug: str, event_id: str, timeout: float = 30.0) -> bytes:
    url = f"{espn_fixtures.BASE}/{slug}/summary?event={event_id}"
    try:
        return logged_urlopen(espn_fixtures.SOURCE, "summary", urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout)
    except (urllib.error.URLError, TimeoutError) as e:
        raise RuntimeError(f"espn {slug} {event_id}: {type(e).__name__}") from None


def _player(p: dict) -> dict:
    a = p.get("athlete") or {}
    pos = p.get("position") or {}
    return {"name": a.get("displayName") or a.get("fullName"), "jersey": p.get("jersey"), "position": pos.get("abbreviation"), "formation_place": p.get("formationPlace")}


def parse_rosters(raw: bytes) -> dict | None:
    """{"home": {...}, "away": {...}} or None while no complete line-up (11 starters per side) is published. Pure."""
    data = json.loads(raw.decode("utf-8"))
    out: dict = {}
    for r in data.get("rosters") or []:
        side = r.get("homeAway")
        starters = [_player(p) for p in r.get("roster") or [] if p.get("starter")]
        if side in ("home", "away") and len(starters) == 11:
            subs = [_player(p) for p in r.get("roster") or [] if not p.get("starter")]
            out[side] = {"team": (r.get("team") or {}).get("displayName"), "formation": r.get("formation"), "starters": starters, "substitutes": subs}
    return out if set(out) == {"home", "away"} else None


def fixture_key(home_id: str, away_id: str, kickoff: datetime) -> str:
    return f"{home_id}__{away_id}__{kickoff.astimezone(UTC).date().isoformat()}"


def collect(root: Path, now: datetime | None = None, fetch_day=None, fetch_sum=None) -> list[str]:
    now = now or datetime.now(UTC)
    fetch_day = fetch_day or espn_fixtures.fetch_day
    fetch_sum = fetch_sum or fetch_summary
    directory = TeamDirectory.load(config_dir_for(root) / "team_aliases.yaml")
    lines = []
    for league, slug in SLUGS.items():
        events = {}
        for day in {now.date(), (now + WINDOW_BEFORE).date()}:
            try:
                for ev in fetch_day(slug, day):
                    parsed = espn_fixtures.parse_event(ev)
                    if parsed:
                        events[parsed["event_id"]] = parsed
            except Exception as e:  # noqa: BLE001 - a failing day is reported, never filled in
                lines.append(f"{league}: scoreboard FAILED ({type(e).__name__})")
        due = [m for m in events.values() if now - WINDOW_AFTER <= m["kickoff_utc"] <= now + WINDOW_BEFORE and not m["completed"]]
        for m in sorted(due, key=lambda x: x["kickoff_utc"]):
            h = directory.resolve_any_country(SOURCES, m["home"], m["kickoff_utc"].date())
            a = directory.resolve_any_country(SOURCES, m["away"], m["kickoff_utc"].date())
            if h.team_id is None or a.team_id is None:
                lines.append(f"{league}: {m['home']} - {m['away']}: club not in the directory, skipped")
                continue
            try:
                raw = fetch_sum(slug, m["event_id"])
                rosters = parse_rosters(raw)
            except (RuntimeError, ValueError) as e:
                lines.append(f"{league}: {m['home']} - {m['away']}: FAILED ({e})")
                continue
            if rosters is None:
                lines.append(f"{league}: {m['home']} - {m['away']}: no line-up published yet")
                continue
            key = fixture_key(h.team_id, a.team_id, m["kickoff_utc"])
            out = Path(root) / "artifacts" / "lineups" / key
            digest = hashlib.sha256(json.dumps(rosters, sort_keys=True).encode("utf-8")).hexdigest()
            previous = sorted(out.glob("lineup-*.json"))
            if previous and json.loads(previous[-1].read_text(encoding="utf-8"))["lineup_sha256"] == digest:
                lines.append(f"{league}: {m['home']} - {m['away']}: unchanged")
                continue
            out.mkdir(parents=True, exist_ok=True)
            body = {"source": espn_fixtures.SOURCE, "source_terms": "UNOFFICIAL, RESEARCH_ONLY (ADR 0081)", "observed_at": now.isoformat(), "kickoff_utc": m["kickoff_utc"].isoformat(), "league": league,
                    "home_id": h.team_id, "away_id": a.team_id, "espn_event_id": m["event_id"], "raw_sha256": hashlib.sha256(raw).hexdigest(), "lineup_sha256": digest, "lineups": rosters}  # fmt: skip
            (out / f"lineup-{now.strftime('%Y%m%dT%H%M%SZ')}.json").write_text(json.dumps(body, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
            lines.append(f"{league}: {m['home']} - {m['away']}: line-up stored ({rosters['home']['formation']} / {rosters['away']['formation']})")
    prune(Path(root), now)
    return lines


def prune(root: Path, now: datetime) -> int:
    """Remove line-up folders of matches older than KEEP_DAYS (the page only needs the recent ones, the as-of history needs all)."""
    n = 0
    base = Path(root) / "artifacts" / "lineups"
    for d in base.glob("*__*__*") if base.exists() else []:
        try:
            day = datetime.fromisoformat(d.name.rsplit("__", 1)[1]).replace(tzinfo=UTC)
        except ValueError:
            continue
        if now - day > timedelta(days=KEEP_DAYS):
            for f in d.glob("*"):
                f.unlink()
            d.rmdir()
            n += 1
    return n


def latest_for(root: Path, home_id: str, away_id: str, kickoff: datetime) -> dict | None:
    """The newest stored line-up of the fixture: {"observed_at", "home": {...}, "away": {...}} or None (the page then says it is not announced yet)."""
    files = sorted((Path(root) / "artifacts" / "lineups" / fixture_key(home_id, away_id, kickoff)).glob("lineup-*.json"))
    if not files:
        return None
    body = json.loads(files[-1].read_text(encoding="utf-8"))
    return {"observed_at": body["observed_at"], "home": body["lineups"]["home"], "away": body["lineups"]["away"]}


def main(argv=None) -> int:
    configure_output()
    print("\n".join(collect(ROOT)) or "no match inside the window")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
