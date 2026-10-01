"""S12: global fixture ingestion from a commercial/provider data source (separate from
`src.data`'s football-data.co.uk CSV pipeline). No commercial vendor is wired in yet --
`docs/data_sources/commercial_migration_plan.md` (S0-S7 hardening) leaves provider selection to
the project owner. This package is the adapter/upsert/coverage INFRASTRUCTURE, tested against a
mock provider, ready for a real adapter to be written against `FixtureProvider` later.
"""

from .cache import CacheEntry, ResponseCache
from .coverage import CoverageCell, CoverageMatrix
from .provider import FixtureProvider, League, ProviderError, RateLimitedError, RawFixture, Season
from .rate_limit import RateLimiter, with_backoff
from .sync import SyncResult, sync_league_season
from .upsert import UnknownStatusError, UpsertResult, build_fixture, resolve_status, upsert_fixture

__all__ = [
    "CacheEntry",
    "CoverageCell",
    "CoverageMatrix",
    "FixtureProvider",
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
    "resolve_status",
    "sync_league_season",
    "upsert_fixture",
    "with_backoff",
]
