"""S12: orchestrate one league-season sync -- discover fixtures, upsert each one, record
coverage, cache/audit the raw response. Ties `provider.py`/`upsert.py`/`coverage.py`/`cache.py`
together; contains no provider-specific logic itself.
"""

from dataclasses import dataclass
from datetime import datetime

from src.data.teams import TeamDirectory
from src.schemas.fixture import Fixture

from .cache import ResponseCache
from .coverage import CoverageMatrix
from .provider import FixtureProvider, ProviderError, RawFixture
from .rate_limit import RateLimiter, with_backoff
from .upsert import UpsertResult, upsert_fixture

ENDPOINT = "fixtures"


class IngestionProviderNotConfigured(RuntimeError):
    pass


def resolve_ingestion_provider(ingestion_cfg, name: str) -> tuple[FixtureProvider, str, list[str]]:
    """`configs/ingestion.yaml` (`IngestionConfig`, S12) -> (adapter, api_key, leagues). Same
    pattern as `src.llm.runner.resolve_provider` (S8): raises if disabled or the named env var
    is unset, never silently skips a provider the caller asked for."""
    if name not in ingestion_cfg.providers:
        raise IngestionProviderNotConfigured(f"no ingestion.yaml entry for {name!r}")
    entry = ingestion_cfg.providers[name]
    if not entry.enabled:
        raise IngestionProviderNotConfigured(f"provider {name!r} is disabled in ingestion.yaml")
    api_key = ingestion_cfg.api_key(name)
    if not api_key:
        raise IngestionProviderNotConfigured(
            f"provider {name!r} enabled but ${entry.api_key_env} is unset in the environment"
        )
    if name == "football-data-org":
        from .football_data_org import FootballDataOrgProvider

        return FootballDataOrgProvider(api_key), api_key, list(entry.leagues)
    raise IngestionProviderNotConfigured(f"no adapter implementation registered for {name!r}")


@dataclass(frozen=True)
class SyncResult:
    league_id: str
    season: str
    fetched: int
    upserted: int
    unchanged: int
    pending_team_resolution: tuple[str, ...]
    error: str | None = None


def sync_league_season(
    provider: FixtureProvider,
    league_id: str,
    season: str,
    directory: TeamDirectory,
    country: str,
    existing_by_fixture_id: dict[str, Fixture] | None = None,
    coverage: CoverageMatrix | None = None,
    rate_limiter: RateLimiter | None = None,
    cache: ResponseCache | None = None,
    status_map: dict | None = None,
    ingested_at: datetime | None = None,
) -> tuple[SyncResult, list[UpsertResult]]:
    existing_by_fixture_id = existing_by_fixture_id or {}
    coverage = coverage if coverage is not None else CoverageMatrix()

    def fetch() -> list[RawFixture]:
        if rate_limiter is not None:
            rate_limiter.acquire()
        return provider.list_fixtures(league_id, season)

    try:
        if cache is not None:
            cache_key = f"{provider.name}:{league_id}:{season}:{ENDPOINT}"
            def fetch_and_serialize():
                return {"fixtures": [_rawfixture_to_dict(f) for f in with_backoff(fetch)]}

            entry = cache.get_or_fetch(cache_key, fetch_and_serialize)
            # Cache stores raw dicts (JSON-serializable); if the fetch happened just now the
            # dicts are already plain, if served from cache they are too -- rebuild RawFixtures
            # either way so downstream code always sees the typed shape.
            raw_fixtures = [_rawfixture_from_dict(d) for d in entry.payload["fixtures"]]
        else:
            raw_fixtures = with_backoff(fetch)
    except ProviderError as e:
        coverage.record_error(league_id, season, ENDPOINT, str(e))
        return SyncResult(league_id, season, 0, 0, 0, (), error=str(e)), []

    coverage.record_success(league_id, season, ENDPOINT, ingested_at)

    results: list[UpsertResult] = []
    upserted = unchanged = 0
    pending: list[str] = []
    for raw in raw_fixtures:
        existing = existing_by_fixture_id.get(raw.provider_fixture_id)
        result = upsert_fixture(existing, raw, directory, provider.name, country, status_map, ingested_at)
        results.append(result)
        if result.fixture is None:
            pending.extend(result.team_resolution_pending)
        elif result.changed:
            upserted += 1
        else:
            unchanged += 1

    sync_result = SyncResult(
        league_id, season, len(raw_fixtures), upserted, unchanged, tuple(sorted(set(pending)))
    )
    return sync_result, results


def _rawfixture_to_dict(f: RawFixture) -> dict:
    return {**f.__dict__, "kickoff_utc": f.kickoff_utc.isoformat()}


def _rawfixture_from_dict(d: dict) -> RawFixture:
    kickoff = d["kickoff_utc"]
    if isinstance(kickoff, str):
        kickoff = datetime.fromisoformat(kickoff)
    return RawFixture(**{**d, "kickoff_utc": kickoff})
