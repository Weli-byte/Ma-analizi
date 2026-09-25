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
    SCHEDULED = "scheduled"
    LIVE = "live"
    FINISHED = "finished"
    POSTPONED = "postponed"
    CANCELLED = "cancelled"


class PredictionStatus(StrEnum):
    CREATED = "created"
    LOCKED = "locked"
    EVALUATED = "evaluated"
    INVALID = "invalid"


class ExperimentType(StrEnum):
    HISTORICAL_BACKTEST = "historical_backtest"
    PROSPECTIVE = "prospective"


class ImmutableModel(BaseModel):
    """Base for records that must never mutate after creation."""

    model_config = ConfigDict(frozen=True, extra="forbid")
