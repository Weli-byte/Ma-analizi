from pydantic import Field, model_validator

from .common import FixtureStatus, ImmutableModel, Outcome, UtcDatetime


class Fixture(ImmutableModel):
    fixture_id: str = Field(min_length=1)
    league_id: str = Field(min_length=1)
    season: str = Field(pattern=r"^\d{4}(-\d{2,4})?$")  # 2023 or 2023-24
    kickoff_utc: UtcDatetime
    home_id: str = Field(min_length=1)
    away_id: str = Field(min_length=1)
    status: FixtureStatus = FixtureStatus.SCHEDULED
    home_goals: int | None = Field(default=None, ge=0)
    away_goals: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _check(self) -> "Fixture":
        if self.home_id == self.away_id:
            raise ValueError("home_id and away_id must differ")
        scored = self.home_goals is not None and self.away_goals is not None
        if (self.home_goals is None) != (self.away_goals is None):
            raise ValueError("home_goals and away_goals must be set together")
        if self.status == FixtureStatus.FINISHED and not scored:
            raise ValueError("finished fixture requires a score")
        if self.status in (FixtureStatus.SCHEDULED, FixtureStatus.CANCELLED) and scored:
            raise ValueError(f"{self.status} fixture cannot have a score")
        return self

    @property
    def outcome(self) -> Outcome | None:
        if self.status != FixtureStatus.FINISHED:
            return None
        assert self.home_goals is not None and self.away_goals is not None
        if self.home_goals > self.away_goals:
            return Outcome.HOME
        if self.home_goals < self.away_goals:
            return Outcome.AWAY
        return Outcome.DRAW
