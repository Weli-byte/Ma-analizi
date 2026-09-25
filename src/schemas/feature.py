from pydantic import Field, model_validator

from src.versioning import FEATURE_VERSION_RE, check

from .common import ImmutableModel, UtcDatetime


class FeatureSpec(ImmutableModel):
    """Feature contract: every feature declares its source and leakage rule."""

    name: str = Field(min_length=1)
    source: str = Field(min_length=1)
    window: str = Field(min_length=1)
    aggregation: str = Field(min_length=1)
    available_at: str = Field(min_length=1)  # e.g. "t-1", "T-lineup"
    leakage_rule: str = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)  # lineage: upstream tables/columns


class FeatureSnapshot(ImmutableModel):
    fixture_id: str = Field(min_length=1)
    feature_version: str
    kickoff_utc: UtcDatetime
    information_cutoff: UtcDatetime
    generated_at: UtcDatetime
    values: dict[str, float | None]  # None = insufficient history (NaN + flag)
    available_at: dict[str, UtcDatetime] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> "FeatureSnapshot":
        check(FEATURE_VERSION_RE, self.feature_version, "feature_version")
        if self.information_cutoff > self.kickoff_utc:
            raise ValueError("information_cutoff must not be after kickoff")
        if self.generated_at < self.information_cutoff:
            raise ValueError("generated_at must not precede information_cutoff")
        unknown = set(self.available_at) - set(self.values)
        if unknown:
            raise ValueError(f"available_at for unknown features: {sorted(unknown)}")
        late = [k for k, t in self.available_at.items() if t > self.information_cutoff]
        if late:
            raise ValueError(f"leakage: features available after cutoff: {sorted(late)}")
        return self
