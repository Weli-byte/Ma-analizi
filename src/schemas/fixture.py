from typing import Literal

from pydantic import Field, model_validator

from .common import FixtureStatus, ImmutableModel, Outcome, UtcDatetime

_NO_SCORE = {FixtureStatus.SCHEDULED, FixtureStatus.CANCELLED, FixtureStatus.POSTPONED,
             FixtureStatus.RESCHEDULED}  # fmt: skip


class Fixture(ImmutableModel):
    """A match and its lifecycle.

    `kickoff_utc` is the SCHEDULED kickoff (also exposed as `scheduled_kickoff_utc`).
    `result_available_at_utc` says when the result may be used by features; for historical data it
    is INFERRED (source has no publication time) and must be labelled so.
    """

    fixture_id: str = Field(min_length=1)
    league_id: str = Field(min_length=1)
    season: str = Field(pattern=r"^\d{4}(-\d{2,4})?$")  # 2023 or 2023-24
    kickoff_utc: UtcDatetime
    home_id: str = Field(min_length=1)
    away_id: str = Field(min_length=1)
    status: FixtureStatus = FixtureStatus.SCHEDULED
    home_goals: int | None = Field(default=None, ge=0)
    away_goals: int | None = Field(default=None, ge=0)
    actual_kickoff_utc: UtcDatetime | None = None
    finished_at_utc: UtcDatetime | None = None
    result_available_at_utc: UtcDatetime | None = None
    result_available_at_source: Literal["observed", "inferred"] | None = None

    @property
    def scheduled_kickoff_utc(self):
        return self.kickoff_utc

    @model_validator(mode="after")
    def _check(self) -> "Fixture":
        if self.home_id == self.away_id:
            raise ValueError("home_id and away_id must differ")
        scored = self.home_goals is not None and self.away_goals is not None
        if (self.home_goals is None) != (self.away_goals is None):
            raise ValueError("home_goals and away_goals must be set together")
        if self.status in _NO_SCORE and scored:
            raise ValueError(f"{self.status} fixture cannot have a score")
        if self.status == FixtureStatus.FINISHED:
            if not scored:
                raise ValueError("finished fixture requires a score")
            if self.result_available_at_utc is None or self.result_available_at_source is None:
                raise ValueError("finished fixture requires result_available_at_utc and its source")
            if self.result_available_at_utc < self.kickoff_utc:
                raise ValueError("result cannot be available before the scheduled kickoff")
            if self.finished_at_utc is not None and self.result_available_at_utc < self.finished_at_utc:
                raise ValueError("result_available_at_utc must not precede finished_at_utc")
        elif self.result_available_at_utc is not None or self.finished_at_utc is not None:
            raise ValueError(f"{self.status} fixture cannot have finished/result timestamps")
        if self.actual_kickoff_utc is not None and self.finished_at_utc is not None:
            if self.finished_at_utc < self.actual_kickoff_utc:
                raise ValueError("finished_at_utc must not precede actual_kickoff_utc")
        return self

    @property
    def outcome(self) -> Outcome | None:
        if self.status != FixtureStatus.FINISHED:
            return None
        if self.home_goals is None or self.away_goals is None:
            raise ValueError("finished fixture without score")
        if self.home_goals > self.away_goals:
            return Outcome.HOME
        if self.home_goals < self.away_goals:
            return Outcome.AWAY
        return Outcome.DRAW
