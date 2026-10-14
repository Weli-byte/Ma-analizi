"""Results that the open history (openfootball) does not have yet (ADR 0058).

Why: on 2026-10-14 the openfootball files stopped at 2026-09-20 - three weeks of finished matches were missing, so the
model did not know them and the track record could not score a single forecast. This module collects the missing results
and stores them under `artifacts/results/<league>.json`; `of_source.load_world` merges them into the history (a match the
open source later publishes is not counted twice).

Sources (both free, both already used by the project):
- football-data.org free tier: PL, PD, BL1, SA, FL1, DED (Eredivisie), PPL (Primeira Liga), CL (Champions League);
  one call per competition for the season. The 90-minute score is taken (`regularTime` when the match went beyond 90).
- The Odds API `/scores` (2 credits per call, `daysFrom=3`): the leagues football-data.org does not cover (Super Lig,
  Europa League, Conference League), called ONLY when a stored fixture of that league kicked off 2.5+ hours ago and has no
  result yet. Its score is the provider's final score; for a knockout match it may include extra time, so a score is
  accepted only while the competition is in its league phase (before 1 February).
Each result keeps `first_seen`: the moment WE first observed it is when it became available (the same rule as every other
source here). A club the directory cannot resolve is counted and skipped, never guessed.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.cli_utils import configure_output, load_dotenv
from src.config import config_dir_for
from src.data.teams import TeamDirectory
from src.ingestion.football_data_org import _get
from src.ingestion.openfootball import ALL_FILES

ROOT = Path(__file__).resolve().parents[2]
FD_CODES = {
    "EPL": "PL",
    "LALIGA": "PD",
    "BUNDESLIGA": "BL1",
    "SERIEA": "SA",
    "LIGUE1": "FL1",
    "NED1": "DED",
    "POR1": "PPL",
    "UCL": "CL",
}
ODDS_ONLY = ("SUPERLIG", "UEL", "UECL")
SOURCES = ("football-data-org", "the-odds-api", "openfootball", "openfootball-cups")
ODDS_BASE = "https://api.the-odds-api.com/v4"
CUPS = ("UCL", "UEL", "UECL")


def season_label(now: datetime) -> str:
    y = now.year if now.month >= 7 else now.year - 1
    return f"{y}-{str(y + 1)[2:]}"


def results_path(root: Path, league: str) -> Path:
    return Path(root) / "artifacts" / "results" / f"{league}.json"


def load_store(root: Path, league: str) -> dict:
    p = results_path(root, league)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"league": league, "matches": {}}


def save_store(root: Path, league: str, store: dict) -> None:
    p = results_path(root, league)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(store, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")


def _resolve(directory: TeamDirectory, league: str, name: str, day):
    country = ALL_FILES[league][2] if league in ALL_FILES else None
    return directory.resolve_any_country(SOURCES, name, day, country=country).team_id


def score_90(match: dict) -> tuple[int, int] | None:
    """football-data.org: the 90-minute score, or None when it cannot be told."""
    score = match.get("score") or {}
    if score.get("duration", "REGULAR") == "REGULAR":
        ft = score.get("fullTime") or {}
    else:
        ft = score.get("regularTime") or {}
    if ft.get("home") is None or ft.get("away") is None:
        return None
    return int(ft["home"]), int(ft["away"])


def merge(store: dict, rows: list[dict], now: datetime) -> int:
    """Add results not stored yet (keeps `first_seen` of known ones); returns the number added."""
    added = 0
    for r in rows:
        key = f"{r['home_id']}|{r['away_id']}|{r['kickoff_utc'][:10]}"
        if key not in store["matches"]:
            store["matches"][key] = {**r, "first_seen": now.isoformat()}
            added += 1
        elif r.get("referee") and not store["matches"][key].get("referee"):
            store["matches"][key]["referee"] = r["referee"]  # added later; the result itself never changes
    return added


def from_football_data(
    league: str, payload: dict, directory: TeamDirectory, now: datetime
) -> tuple[list[dict], int]:
    rows, unresolved = [], 0
    for m in payload.get("matches", []):
        if m.get("status") != "FINISHED":
            continue
        goals = score_90(m)
        ko = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00")).astimezone(UTC)
        if goals is None or ko > now:
            continue
        h = _resolve(directory, league, m["homeTeam"]["name"], ko.date())
        a = _resolve(directory, league, m["awayTeam"]["name"], ko.date())
        if h is None or a is None:
            unresolved += 1
            continue
        rows.append(
            {
                "league": league, "home_id": h, "away_id": a, "kickoff_utc": ko.isoformat(),
                "home_goals": goals[0], "away_goals": goals[1], "source": "football-data.org",
                "score_basis": "90 minutes", "referee": referee_of(m),
            }
        )  # fmt: skip
    return rows, unresolved


def referee_of(m: dict) -> dict | None:
    ref = next((r for r in m.get("referees") or [] if r.get("type") == "REFEREE"), None)
    return {"name": ref["name"], "nationality": ref.get("nationality")} if ref and ref.get("name") else None


def upcoming_from_football_data(
    league: str, payload: dict, directory: TeamDirectory, now: datetime
) -> list[dict]:
    """Scheduled matches (the fixture list when the odds provider is unavailable) with the referee once appointed."""
    out = []
    for m in payload.get("matches", []):
        if m.get("status") not in ("SCHEDULED", "TIMED"):
            continue
        ko = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00")).astimezone(UTC)
        if ko <= now:
            continue
        h = _resolve(directory, league, m["homeTeam"]["name"], ko.date())
        a = _resolve(directory, league, m["awayTeam"]["name"], ko.date())
        if h is None or a is None:
            continue
        out.append({"fixture_id": f"fd-{m['id']}", "home_id": h, "away_id": a, "kickoff_utc": ko.isoformat(), "referee": referee_of(m)})  # fmt: skip
    return sorted(out, key=lambda x: x["kickoff_utc"])


def scorers_from_football_data(league: str, payload: dict, directory: TeamDirectory) -> list[dict]:
    """The competition's top scorers (goals, assists, penalties, matches) with the club resolved to our id."""
    out = []
    for s in payload.get("scorers", []):
        team = _resolve(directory, league, (s.get("team") or {}).get("name", ""), None)
        out.append(
            {
                "player": s["player"]["name"], "team_id": team, "team_name": (s.get("team") or {}).get("name"),
                "goals": s.get("goals") or 0, "assists": s.get("assists"), "penalties": s.get("penalties"),
                "played": s.get("playedMatches"), "position": s["player"].get("section"),
            }
        )  # fmt: skip
    return out


def from_odds_scores(
    league: str, events: list[dict], directory: TeamDirectory, now: datetime
) -> tuple[list[dict], int]:
    rows, unresolved = [], 0
    for ev in events:
        if not ev.get("completed") or not ev.get("scores"):
            continue
        ko = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00")).astimezone(UTC)
        if league in CUPS and ko.month in (2, 3, 4, 5, 6):
            continue  # knockout phase: the provider score may include extra time
        by = {s["name"]: s["score"] for s in ev["scores"]}
        if ev["home_team"] not in by or ev["away_team"] not in by:
            continue
        h = _resolve(directory, league, ev["home_team"], ko.date())
        a = _resolve(directory, league, ev["away_team"], ko.date())
        if h is None or a is None:
            unresolved += 1
            continue
        rows.append(
            {
                "league": league, "home_id": h, "away_id": a, "kickoff_utc": ko.isoformat(),
                "home_goals": int(by[ev["home_team"]]), "away_goals": int(by[ev["away_team"]]),
                "source": "the-odds-api /scores", "score_basis": "provider final score (league phase: 90 minutes)",
            }
        )  # fmt: skip
    return rows, unresolved


def pending_fixtures(root: Path, league: str, now: datetime) -> int:
    """Stored odds-store fixtures of the league that kicked off 2.5+ hours ago and have no result stored."""
    store = load_store(root, league)
    n = 0
    seen = set()
    for base in ("odds", "odds_remote"):
        for p in (Path(root) / "artifacts" / base).glob("*/meta.json"):
            m = json.loads(p.read_text(encoding="utf-8"))
            if m.get("league") != league or m["fixture_id"] in seen:
                continue
            seen.add(m["fixture_id"])
            ko = datetime.fromisoformat(m["kickoff_utc"])
            key = f"{m['home_id']}|{m['away_id']}|{m['kickoff_utc'][:10]}"
            if (
                now - ko > timedelta(hours=2.5)
                and now - ko < timedelta(days=3)
                and key not in store["matches"]
            ):
                n += 1
    return n


def odds_scores(key: str, league: str) -> list[dict]:
    from src.odds.theoddsapi import SPORT_KEYS

    code = {"SUPERLIG": "TR"}.get(league, league)
    q = urllib.parse.urlencode({"apiKey": key, "daysFrom": 3, "dateFormat": "iso"})
    url = f"{ODDS_BASE}/sports/{SPORT_KEYS[code]}/scores/?{q}"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310 - fixed https host
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            note = json.loads(e.read().decode("utf-8", "replace")).get("message", "")[:120]
        except ValueError:
            note = ""
        raise RuntimeError(
            f"the-odds-api scores failed: HTTP {e.code} {note}".strip()
        ) from None  # never the key
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise RuntimeError(
            f"the-odds-api scores failed: {type(e).__name__}"
        ) from None  # never carries the key


def crests_from_football_data(league: str, payload: dict, directory: TeamDirectory) -> dict[str, str]:
    """team_id -> crest URL from a football-data.org matches payload (`homeTeam.crest`): the club logos of every
    competition the free tier covers, without spending another request (ADR 0059)."""
    out: dict[str, str] = {}
    for m in payload.get("matches", []):
        for side in ("homeTeam", "awayTeam"):
            t = m.get(side) or {}
            if t.get("crest") and t.get("name"):
                tid = _resolve(directory, league, t["name"], None)
                if tid:
                    out.setdefault(tid, t["crest"])
    return out


CREST_HOSTS = ("https://crests.football-data.org/",)


def download_crests(root: Path, limit: int = 120) -> str:
    """Download the crests listed in the results stores that are not on disk yet (`artifacts/crests/<team_id>.<ext>`).
    Only the football-data.org crest host is fetched. Logos are trademarks: they stay in the private data repository and
    are shown only with FORECAST_UI_LOGOS=1 (identification use)."""
    out = Path(root) / "artifacts" / "crests"
    out.mkdir(parents=True, exist_ok=True)
    have = {p.stem for p in out.iterdir()}
    wanted: dict[str, str] = {}
    for p in sorted((Path(root) / "artifacts" / "results").glob("*.json")):
        st = json.loads(p.read_text(encoding="utf-8"))
        wanted.update({t: u for t, u in st.get("crests", {}).items() if t not in have})
        if st.get("emblem") and f"league_{st['league']}" not in have:
            wanted[f"league_{st['league']}"] = st["emblem"]
    got = failed = 0
    for name, url in list(wanted.items())[:limit]:
        ext = url.rsplit(".", 1)[-1].lower()
        if not url.startswith(CREST_HOSTS) or ext not in ("png", "svg"):
            continue
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (crest cache)"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310 - fixed https host, checked above
                (out / f"{name}.{ext}").write_bytes(r.read())
            got += 1
        except (urllib.error.URLError, TimeoutError):
            failed += 1
    return f"crests: {got} downloaded, {failed} failed, {max(0, len(wanted) - limit)} left for the next run"


def fd_call(path: str, key: str, params: dict) -> dict:
    """One football-data.org call; after a 429 (10 calls/minute on the free tier) wait a minute and try once more."""
    from src.ingestion.provider import RateLimitedError

    try:
        return _get(path, key, params=params)
    except RateLimitedError:
        time.sleep(61)
        return _get(path, key, params=params)


def collect(
    root: Path, now: datetime | None = None, fd_key: str | None = None, odds_key: str | None = None
) -> list[str]:
    now = now or datetime.now(UTC)
    directory = TeamDirectory.load(config_dir_for(root) / "team_aliases.yaml")
    lines = []
    if fd_key:
        season = int(season_label(now)[:4])
        for league, code in FD_CODES.items():
            try:
                payload = fd_call(f"/competitions/{code}/matches", fd_key, {"season": season})
            except Exception as e:  # noqa: BLE001 - one competition failing must not stop the others
                lines.append(f"{league}: football-data.org FAILED ({type(e).__name__})")
                continue
            rows, unresolved = from_football_data(league, payload, directory, now)
            store = load_store(root, league)
            store["season"] = season_label(now)
            added = merge(store, rows, now)
            store["upcoming"] = upcoming_from_football_data(league, payload, directory, now)
            store["crests"] = {
                **store.get("crests", {}),
                **crests_from_football_data(league, payload, directory),
            }
            emblem = next(
                (m["competition"].get("emblem") for m in payload.get("matches", []) if m.get("competition")),
                None,
            )
            if emblem:
                store["emblem"] = emblem
            store["upcoming_observed_at"] = now.isoformat()
            time.sleep(6.5)  # the free tier allows 10 calls per minute
            try:
                sc = fd_call(f"/competitions/{code}/scorers", fd_key, {"season": season, "limit": 60})
                store["scorers"] = scorers_from_football_data(league, sc, directory)
                store["scorers_observed_at"] = now.isoformat()
            except Exception as e:  # noqa: BLE001 - the scorers list is optional information
                lines.append(f"{league}: scorers FAILED ({type(e).__name__})")
            save_store(root, league, store)
            lines.append(f"{league}: {len(rows)} finished, {added} new, {unresolved} club(s) unresolved")
            time.sleep(6.5)  # the free tier allows 10 calls per minute
        lines.append(download_crests(root))
    else:
        lines.append("football-data.org: NOT_CONFIGURED (FOOTBALL_DATA_ORG_API_KEY not set)")
    if odds_key:
        for league in ODDS_ONLY:
            from src.odds.run import key_paused, mark_quota, quota_exhausted

            if quota_exhausted(root, now) or key_paused(root, odds_key, now):
                lines.append(f"{league}: odds provider quota reached recently: scores call skipped")
                continue
            need = pending_fixtures(root, league, now)
            if not need:
                lines.append(f"{league}: no fixture waiting for a result (no credit spent)")
                continue
            try:
                events = odds_scores(odds_key, league)
            except RuntimeError as e:
                if "401" in str(e):
                    mark_quota(root, odds_key, now)  # the next run uses the second key, if there is one
                lines.append(f"{league}: {e}")
                continue
            rows, unresolved = from_odds_scores(league, events, directory, now)
            store = load_store(root, league)
            store["season"] = season_label(now)
            added = merge(store, rows, now)
            save_store(root, league, store)
            lines.append(
                f"{league}: {need} waiting, {len(rows)} completed, {added} new, {unresolved} unresolved"
            )
    else:
        from src.odds.run import odds_keys

        if odds_keys():
            lines.append("the-odds-api scores: every key is paused (quota reached) - skipped")
        else:
            lines.append("the-odds-api scores: NOT_CONFIGURED (THE_ODDS_API_KEY not set)")
    return lines


def main(argv=None) -> int:
    configure_output()
    load_dotenv()
    from src.odds.run import active_key

    now = datetime.now(UTC)
    odds_key = active_key(ROOT, now)
    print(
        "\n".join(collect(ROOT, now, fd_key=os.environ.get("FOOTBALL_DATA_ORG_API_KEY"), odds_key=odds_key))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
