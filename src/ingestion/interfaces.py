"""Capability interfaces for real data providers (S13+, ADR 0028).

`FixtureProvider` stays in `provider.py` (S12). This module adds the other capabilities as
Protocols plus `ProviderMeta`, so every provider states -- in code, not marketing -- what it
supports, how its timestamps are to be read, its rate limits, its licence and its provenance. A
capability a provider does not offer is `NOT_SUPPORTED` (never an empty list that could be read as
"nothing happened"); one that needs a credential we lack is `NOT_CONFIGURED`.

Swapping or adding a provider must not touch the forecasting engine: the engine only sees these
interfaces and the `PlayerAvailability` record.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from .provider import FixtureProvider

__all__ = [
    "AvailabilityStatus",
    "Capability",
    "EventProvider",
    "FixtureProvider",
    "InjuryProvider",
    "LineupProvider",
    "OddsProvider",
    "PlayerAvailability",
    "ProviderMeta",
    "StatisticsProvider",
    "Support",
    "XGProvider",
]


class Capability(StrEnum):
    FIXTURES = "fixtures"
    LINEUPS = "lineups"
    INJURIES = "injuries"
    EVENTS = "events"
    ODDS = "odds"
    STATISTICS = "statistics"
    XG = "xg"


class Support(StrEnum):
    SUPPORTED = "SUPPORTED"
    NOT_SUPPORTED = "NOT_SUPPORTED"
    NOT_CONFIGURED = "NOT_CONFIGURED"


class AvailabilityStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    DOUBTFUL = "DOUBTFUL"
    INJURED = "INJURED"
    SUSPENDED = "SUSPENDED"
    UNAVAILABLE = "UNAVAILABLE"  # not selectable for another reason (left, not registered, ...)


@dataclass(frozen=True)
class ProviderMeta:
    name: str
    capabilities: dict[Capability, Support]
    coverage: str  # leagues / seasons / entities the provider really covers
    timestamp_semantics: str  # what each timestamp it returns means (observed vs effective)
    rate_limit: str
    license: str  # terms as far as verified
    license_status: str  # RESEARCH_ONLY | COMMERCIAL_OK | UNVERIFIED
    provenance: str  # endpoint(s) the data comes from
    verified_on: str | None = None  # date a REAL response confirmed the claimed capabilities

    def supports(self, cap: Capability) -> bool:
        return self.capabilities.get(cap) == Support.SUPPORTED


@dataclass(frozen=True)
class PlayerAvailability:
    """One player's availability, with provenance. `None` = the provider did not give it."""

    player: str
    team_id: str | None  # repo team id (resolved through TeamDirectory); None if unresolved
    team_raw_name: str
    status: AvailabilityStatus
    availability_pct: int | None  # provider's chance-of-playing figure, when it exposes one
    detail: str | None  # provider's own free-text note
    source: str
    observed_at: datetime  # when WE received it
    effective_at: datetime | None  # when the provider says it became true (None if not stated)
    confidence: float | None  # only if the provider exposes a confidence; never invented
    provenance: dict = field(default_factory=dict)  # endpoint + raw provider fields kept for audit


class LineupProvider(Protocol):
    meta: ProviderMeta

    def list_lineups(self, provider_fixture_id: str) -> list[dict]: ...


class InjuryProvider(Protocol):
    meta: ProviderMeta

    def list_availability(self, observed_at: datetime | None = None) -> list[PlayerAvailability]: ...


class EventProvider(Protocol):
    meta: ProviderMeta

    def list_events(self, provider_fixture_id: str) -> list[dict]: ...


class OddsProvider(Protocol):
    meta: ProviderMeta

    def list_odds(self, provider_fixture_id: str) -> list[dict]: ...


class StatisticsProvider(Protocol):
    meta: ProviderMeta

    def list_statistics(self, provider_fixture_id: str) -> list[dict]: ...


class XGProvider(Protocol):
    meta: ProviderMeta

    def list_xg(self, provider_fixture_id: str) -> list[dict]: ...
