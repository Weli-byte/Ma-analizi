"""S12: the `FixtureProvider` interface every commercial/global data source implements.

No concrete commercial vendor is wired in this sprint -- `docs/data_sources/commercial_migration_plan.md`
(S0-S7 hardening) is explicit that no provider has been chosen yet (open action for the project
owner). This module defines the CONTRACT so a real adapter (API-Football, Sportmonks, ...) can be
written against it later without touching `sync.py`/`upsert.py`/`coverage.py`, and so tests can
exercise the full ingestion pipeline against the real adapters (ADR 0023); there is no mock provider --
tests run the real football-data.org adapter over a loopback socket against REAL captured responses.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol


class ProviderError(RuntimeError):
    """The provider call itself failed (network/HTTP/auth/rate-limit) -- distinct from a
    legitimate EMPTY result (a league with zero fixtures in a season is not an error)."""


class RateLimitedError(ProviderError):
    """The provider explicitly signaled a rate limit (e.g. HTTP 429) -- `rate_limit.with_backoff`
    retries on this specifically; other `ProviderError`s are not assumed transient."""


@dataclass(frozen=True)
class League:
    league_id: str  # the PROVIDER's own id, not this repo's internal league_id (team_resolution
    # already distinguishes "provider's name/id" from "canonical id" for teams; same idea here)
    name: str
    country: str


@dataclass(frozen=True)
class Season:
    league_id: str
    season: str  # this repo's existing season string shape, e.g. "2023-24" (schemas.fixture)
    start_date: date
    end_date: date


@dataclass(frozen=True)
class RawFixture:
    """Provider-native fixture data, BEFORE team-identity resolution or schema validation --
    `upsert.py` turns this into a `schemas.Fixture` (canonical team_ids, validated status)."""

    provider_fixture_id: str
    league_id: str
    season: str
    kickoff_utc: datetime
    home_team_raw_name: str
    away_team_raw_name: str
    status_raw: str  # provider's own status vocabulary; upsert.py maps it to FixtureStatus
    home_goals: int | None = None
    away_goals: int | None = None
    extra: dict = field(default_factory=dict)  # provider-specific fields kept for audit, unused


class FixtureProvider(Protocol):
    """Required: leagues/seasons/fixtures. Optional (S13+ scope): lineups/injuries/events/
    statistics/odds -- declared here as an interface only, per the sprint's own instruction
    ("ayrica lineups/injuries/events/statistics/odds icin interface birak"); a provider that
    doesn't support one of them raises `NotImplementedError`, not a silent empty result (an
    empty result must always mean "the provider legitimately has nothing," never "unsupported")."""

    name: str

    def list_leagues(self) -> list[League]: ...
    def list_seasons(self, league_id: str) -> list[Season]: ...
    def list_fixtures(self, league_id: str, season: str) -> list[RawFixture]: ...

    def list_lineups(self, provider_fixture_id: str) -> list[dict]:
        raise NotImplementedError("lineups: S13+ scope")

    def list_injuries(self, provider_fixture_id: str) -> list[dict]:
        raise NotImplementedError("injuries: S13+ scope")

    def list_events(self, provider_fixture_id: str) -> list[dict]:
        raise NotImplementedError("events: S14+ scope")

    def list_statistics(self, provider_fixture_id: str) -> list[dict]:
        raise NotImplementedError("statistics: S14+ scope")

    def list_odds(self, provider_fixture_id: str) -> list[dict]:
        raise NotImplementedError("odds: S15 scope (timestamped odds)")
