"""S14 live events and normalized snapshots (ADR 0029).

Only events a feed ACTUALLY supplies are produced. A feed that cannot report cards says so through
`capabilities`; the absence of a card event then means "unknown", never "no cards". An event
derived from a score change between polls is labelled (`derived_from_score_change`) and carries no
minute, scorer or penalty/own-goal detail it does not have.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from src.versioning import canonical_json


class EventType(StrEnum):
    GOAL = "goal"
    PENALTY_GOAL = "penalty_goal"
    OWN_GOAL = "own_goal"
    YELLOW_CARD = "yellow_card"
    RED_CARD = "red_card"
    SUBSTITUTION = "substitution"
    VAR = "var"
    STATUS_CHANGE = "status_change"


class MatchStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    IN_PLAY = "IN_PLAY"
    HALF_TIME = "HALF_TIME"
    FINISHED = "FINISHED"
    UNKNOWN = "UNKNOWN"  # postponed/suspended/unmapped provider codes: never guessed


ALL_EVENT_KINDS = ("goals", "cards", "substitutions", "var", "minute", "status", "score")


@dataclass(frozen=True)
class LiveEvent:
    fixture_id: str
    type: EventType
    team: str | None  # "home" | "away" | None (unknown/not applicable)
    minute: int | None  # match minute the provider gives; None if it does not
    player: str | None
    detail: str | None
    source: str
    observed_at: datetime  # when we received it
    source_time: datetime | None  # the provider's own time for it, if any
    derived_from_score_change: bool = False
    score_after: tuple[int, int] | None = None
    provenance: dict = field(default_factory=dict)

    @property
    def event_id(self) -> str:
        """Content-derived and independent of when we observed it, so re-polling the same real
        event never creates a duplicate."""
        payload = {
            "fixture": self.fixture_id, "type": self.type.value, "team": self.team,
            "minute": self.minute, "player": self.player, "score_after": self.score_after,
            "source": self.source, "derived": self.derived_from_score_change,
        }  # fmt: skip
        return hashlib.sha256(canonical_json(payload).encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id, "fixture_id": self.fixture_id, "type": self.type.value,
            "team": self.team, "minute": self.minute, "player": self.player, "detail": self.detail,
            "source": self.source, "observed_at": self.observed_at.isoformat(),
            "source_time": self.source_time.isoformat() if self.source_time else None,
            "derived_from_score_change": self.derived_from_score_change,
            "score_after": list(self.score_after) if self.score_after else None,
            "provenance": self.provenance,
        }  # fmt: skip


@dataclass(frozen=True)
class LiveSnapshot:
    """One normalized poll of one fixture."""

    fixture_id: str
    source: str
    observed_at: datetime
    kickoff_utc: datetime
    status: MatchStatus
    status_source: str  # "reported" or "inferred_from_clock"
    status_raw: str | None
    score: tuple[int, int] | None  # None = the feed reported no score
    minute_reported: int | None
    events: tuple[LiveEvent, ...]
    capabilities: dict[str, bool]  # which event kinds this feed can supply at all
    raw_sha256: str
