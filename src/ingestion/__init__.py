"""S12: global fixture ingestion from a commercial/provider data source (separate from
`src.data`'s football-data.co.uk CSV pipeline). No commercial vendor is wired in yet --
`docs/data_sources/commercial_migration_plan.md` (S0-S7 hardening) leaves provider selection to
the project owner. This package is the adapter/upsert/coverage INFRASTRUCTURE plus REAL free-tier
adapters (football-data.org, FPL, ESPN lineups); there is no mock provider.
"""

from .cache import CacheEntry, ResponseCache
from .coverage import CoverageCell, CoverageMatrix
from .provider import FixtureProvider, League, ProviderError, RateLimitedError, RawFixture, Season
from .rate_limit import RateLimiter, with_backoff
from .sync import IngestionProviderNotConfigured, SyncResult, resolve_ingestion_provider, sync_league_season
from .upsert import UnknownStatusError, UpsertResult, build_fixture, resolve_status, upsert_fixture

__all__ = [
    "CacheEntry",
    "CoverageCell",
    "CoverageMatrix",
    "FixtureProvider",
    "IngestionProviderNotConfigured",
    "League",
    "ProviderError",
    "RateLimitedError",
    "RateLimiter",
    "RawFixture",
    "ResponseCache",
    "Season",
    "SyncResult",
    "UnknownStatusError",
    "UpsertResult",
    "build_fixture",
    "resolve_ingestion_provider",
    "resolve_status",
    "sync_league_season",
    "upsert_fixture",
    "with_backoff",
]
