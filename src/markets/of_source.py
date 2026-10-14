"""Markets history and fixtures from openfootball/football.json (PUBLIC DOMAIN, ADR 0044).

This is the licence-clean source for the product path: scores and dates for EPL and La Liga, no corners / cards
/ shots (those exist only in the research-only football-data.co.uk CSVs). Files are fetched from the public
repository; a successful fetch refreshes a local cache, and when the network fails the cache is used and the
run says so (`stale_files`). The `data_version` is derived from the exact bytes used.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.config import MarketsConfig
from src.data.teams import TeamDirectory
from src.ingestion.openfootball import ALL_FILES, FILES, OpenFootballProvider, season_dir
from src.ingestion.provider import ProviderError, RawFixture

from .data import STATS, StatMatch


@dataclass
class OpenFootballWorld:
    history: list[StatMatch]
    upcoming: list[tuple[str, RawFixture]]  # (repo league, scheduled fixture)
    data_version: str
    unresolved: int
    stale_files: list[str]
    skipped_no_score: int
    missing_files: list[str]  # season files the source does not publish (HTTP 404)


def _cache_path(root: Path, league: str, year: int) -> Path:
    return (
        root
        / "artifacts"
        / "markets"
        / "_cache"
        / "openfootball"
        / season_dir(year)
        / f"{ALL_FILES[league][0]}.json"
    )


def _fetch(
    root: Path, provider: OpenFootballProvider, league: str, year: int
) -> tuple[list[RawFixture], bytes, bool]:
    """(fixtures, raw bytes, stale). Refreshes the cache on success; falls back to it on a network error."""
    import urllib.error
    import urllib.request

    from src.ingestion.endpoints import endpoint
    from src.ingestion.openfootball import BASE, parse
    from src.mlops.oplog import logged_urlopen

    cache = _cache_path(root, league, year)
    url = f"{endpoint('OPENFOOTBALL_BASE_URL', BASE)}/{season_dir(year)}/{ALL_FILES[league][0]}.json"
    try:
        raw = logged_urlopen("openfootball", "fixtures", urllib.request.Request(url), provider.timeout)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(raw)
        stale = False
    except urllib.error.HTTPError as e:
        if e.code == 404:  # the source does not publish this season file: a gap, not a failure
            return [], b"", False
        if not cache.exists():
            raise ProviderError(
                f"openfootball {league} {season_dir(year)}: HTTP {e.code} and no cache"
            ) from None
        raw, stale = cache.read_bytes(), True
    except Exception:  # noqa: BLE001 - any network failure: use the cache if there is one
        if not cache.exists():
            raise ProviderError(
                f"openfootball {league} {season_dir(year)}: unreachable and no cache"
            ) from None
        raw, stale = cache.read_bytes(), True
    return parse(raw, league, season_dir(year)), raw, stale


def cup_matches(
    root: Path,
    directory: TeamDirectory,
    cfg: MarketsConfig,
    last_year: int,
    now: datetime,
    blobs: list,
    stale: list,
) -> tuple[list[StatMatch], int]:
    """FINISHED European-cup matches (90-minute scores) as history. A cached copy is used when the network
    fails; a season the repository does not have is skipped silently (documented gaps)."""
    import urllib.error

    from src.ingestion.openfootball_cups import (
        COMPETITIONS,
        FIRST_SEASON,
        LAST_SEASON,
        SOURCE,
        fetch,
        is_neutral_stage,
        parse,
    )

    empty = dict.fromkeys(STATS)
    out: list[StatMatch] = []
    unresolved = 0
    for comp in COMPETITIONS:
        for year in range(
            max(cfg.history_start_year, FIRST_SEASON[comp]), min(last_year, LAST_SEASON[comp]) + 1
        ):
            cache = root / "artifacts" / "markets" / "_cache" / "openfootball_cups" / f"{comp}_{year}.txt"
            was_stale = False
            try:
                raw = fetch(comp, year)
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_bytes(raw)
            except (urllib.error.URLError, TimeoutError, OSError):
                if not cache.exists():
                    continue
                raw, was_stale = cache.read_bytes(), True
            blobs.append(hashlib.sha256(raw).hexdigest())
            if was_stale:
                stale.append(f"{comp} {year}")
            for m in parse(raw.decode("utf-8"), comp, year):
                if m.goals is None or m.kickoff_utc > now:
                    continue
                day = m.kickoff_utc.date()
                h = directory.resolve(SOURCE, m.home, m.home_cc, day)
                a = directory.resolve(SOURCE, m.away, m.away_cc, day)
                if h.team_id is None or a.team_id is None:
                    unresolved += 1
                    continue
                out.append(
                    StatMatch(
                        f"cup-{comp}-{m.season}-{h.team_id}-{a.team_id}-{day.isoformat()}",
                        m.season,
                        comp,
                        m.kickoff_utc,
                        m.kickoff_utc + timedelta(hours=cfg.result_lag_hours),
                        h.team_id,
                        a.team_id,
                        m.goals[0],
                        m.goals[1],
                        empty,
                        empty,
                        None,
                        is_neutral_stage(comp, m.season, m.stage),
                    )  # fmt: skip
                )
    return out, unresolved


def live_matches(root: Path, known: set, cfg: MarketsConfig, now: datetime, blobs: list) -> list[StatMatch]:
    """Results collected by `live_results` that the open source does not have (yet). A match already in the history (the
    open source caught up) is skipped. A result becomes available when WE first saw it (never before kickoff + lag)."""
    empty = dict.fromkeys(STATS)
    out = []
    for path in sorted((Path(root) / "artifacts" / "results").glob("*.json")):
        raw = path.read_bytes()
        blobs.append(hashlib.sha256(raw).hexdigest())
        store = json.loads(raw.decode("utf-8"))
        for r in store["matches"].values():
            ko = datetime.fromisoformat(r["kickoff_utc"])
            key = (r["home_id"], r["away_id"], ko.date())
            if key in known or ko > now:
                continue
            known.add(key)
            avail = max(datetime.fromisoformat(r["first_seen"]), ko + timedelta(hours=cfg.result_lag_hours))
            out.append(
                StatMatch(
                    f"live-{r['league']}-{ko.date().isoformat()}-{r['home_id']}-{r['away_id']}",
                    store.get("season", ""),
                    r["league"],
                    ko,
                    avail,
                    r["home_id"],
                    r["away_id"],
                    r["home_goals"],
                    r["away_goals"],
                    empty,
                    empty,
                    None,
                )
            )
    return out


def load_world(
    root: Path,
    directory: TeamDirectory,
    cfg: MarketsConfig,
    now: datetime | None = None,
    end_year: int | None = None,
) -> OpenFootballWorld:
    """`end_year`: last season start year to FETCH. Evaluation passes the last validation season so the
    locked final-test seasons are never even downloaded."""
    now = now or datetime.now(UTC)
    provider = OpenFootballProvider()
    last_year = end_year if end_year is not None else (now.year if now.month >= 7 else now.year - 1)
    empty = dict.fromkeys(STATS)
    history, upcoming, blobs, stale, missing = [], [], [], [], []
    unresolved = skipped = 0
    for league, (_, _, country) in ALL_FILES.items():
        for year in range(cfg.history_start_year, last_year + 1):
            fx, raw, was_stale = _fetch(root, provider, league, year)
            if not raw:
                missing.append(f"{league} {season_dir(year)}")
                continue
            blobs.append(hashlib.sha256(raw).hexdigest())
            if was_stale:
                stale.append(f"{league} {season_dir(year)}")
            for f in fx:
                if f.status_raw == "SCHEDULED":
                    if league not in FILES:
                        continue  # history-only league: no fixtures are forecast
                    if f.kickoff_utc > now:
                        upcoming.append((league, f))
                    else:
                        skipped += 1  # kicked off but no score published: not history, not a fixture
                    continue
                h = directory.resolve("openfootball", f.home_team_raw_name, country, f.kickoff_utc.date())
                a = directory.resolve("openfootball", f.away_team_raw_name, country, f.kickoff_utc.date())
                if h.team_id is None or a.team_id is None:
                    unresolved += 1
                    continue
                history.append(
                    StatMatch(
                        f"of-{f.provider_fixture_id}",
                        f.season,
                        league,
                        f.kickoff_utc,
                        f.kickoff_utc + timedelta(hours=cfg.result_lag_hours),
                        h.team_id,
                        a.team_id,
                        f.home_goals,
                        f.away_goals,
                        empty,
                        empty,
                        None,
                    )  # fmt: skip
                )
    cup_history, cup_unresolved = cup_matches(root, directory, cfg, last_year, now, blobs, stale)
    history.extend(cup_history)
    unresolved += cup_unresolved
    if (
        end_year is None
    ):  # an evaluation (end_year set) never sees live results: the final-test seasons stay locked
        history.extend(
            live_matches(
                root, {(m.home_id, m.away_id, m.kickoff_utc.date()) for m in history}, cfg, now, blobs
            )
        )
    history.sort(key=lambda m: m.kickoff_utc)
    upcoming.sort(key=lambda x: x[1].kickoff_utc)
    dv = "dv-of-" + hashlib.sha256("".join(sorted(blobs)).encode()).hexdigest()[:12]
    return OpenFootballWorld(history, upcoming, dv, unresolved, stale, skipped, missing)
