from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict


def _require_tz(v: datetime) -> datetime:
    if v.tzinfo is None or v.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware (UTC)")
    return v


UtcDatetime = Annotated[datetime, AfterValidator(_require_tz)]


class Outcome(StrEnum):
    HOME = "H"
    DRAW = "D"
    AWAY = "A"


class FixtureStatus(StrEnum):
    """Match lifecycle (ADR 0006)."""

    SCHEDULED = "scheduled"
    POSTPONED = "postponed"
    IN_PROGRESS = "in_progress"
    FINISHED = "finished"
    ABANDONED = "abandoned"
    CANCELLED = "cancelled"
    RESCHEDULED = "rescheduled"


class SeasonStatus(StrEnum):
    """Data-completeness category of a league-season (ADR 0012)."""

    HISTORICAL_COMPLETE = "historical_complete"
    CURRENT_PARTIAL = "current_partial"
    FUTURE_FIXTURE = "future_fixture"
    INCOMPLETE_HISTORICAL = "incomplete_historical"  # season is over but matches are missing (error)


class PredictionStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    LOCKED = "locked"
    EVALUATED = "evaluated"
    VOID = "void"


class ExperimentType(StrEnum):
    HISTORICAL_BACKTEST = "historical_backtest"
    PROSPECTIVE = "prospective"


class ImmutableModel(BaseModel):
    """Base for records that must never mutate after creation (attribute level; nested containers
    are frozen via schemas.frozen types)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
