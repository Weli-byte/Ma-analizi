"""Markets history and fixtures from openfootball/football.json (PUBLIC DOMAIN, ADR 0044).

This is the licence-clean source for the product path: scores and dates for EPL and La Liga, no corners / cards
/ shots (those exist only in the research-only football-data.co.uk CSVs). Files are fetched from the public
repository; a successful fetch refreshes a local cache, and when the network fails the cache is used and the
run says so (`stale_files`). The `data_version` is derived from the exact bytes used.
"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.config import MarketsConfig
from src.data.teams import TeamDirectory
from src.ingestion.openfootball import FILES, OpenFootballProvider, season_dir
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
        / f"{FILES[league][0]}.json"
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
    url = f"{endpoint('OPENFOOTBALL_BASE_URL', BASE)}/{season_dir(year)}/{FILES[league][0]}.json"
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
    for league, (_, _, country) in FILES.items():
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
    history.sort(key=lambda m: m.kickoff_utc)
    upcoming.sort(key=lambda x: x[1].kickoff_utc)
    dv = "dv-of-" + hashlib.sha256("".join(sorted(blobs)).encode()).hexdigest()[:12]
    return OpenFootballWorld(history, upcoming, dv, unresolved, stale, skipped, missing)
