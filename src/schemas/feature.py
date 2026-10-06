from collections.abc import Mapping
from typing import Annotated, Literal, get_args

from pydantic import AfterValidator, Field, PlainSerializer, model_validator

from src.versioning import DATA_VERSION_RE, FEATURE_VERSION_RE, check

from .common import ImmutableModel, UtcDatetime
from .frozen import FrozenFloatMap, FrozenStrMap, deep_freeze, thaw

FrozenTimeMap = Annotated[
    Mapping[str, UtcDatetime],
    AfterValidator(deep_freeze),
    PlainSerializer(thaw, return_type=dict),
]

UnavailableReason = Literal[
    "dataset_start",  # no history because the dataset itself has no earlier season yet
    "new_team",  # team has no earlier match although earlier seasons exist (promoted/new)
    "insufficient_history",  # some history, fewer matches than the window needs
    "source_missing",  # the data source does not provide the underlying field
]


class FeatureSpec(ImmutableModel):
    """Feature contract: every feature declares its source and leakage rule."""

    name: str = Field(min_length=1)
    source: str = Field(min_length=1)
    window: str = Field(min_length=1)
    aggregation: str = Field(min_length=1)
    available_at: str = Field(min_length=1)  # e.g. "t-1", "T-lineup"
    leakage_rule: str = Field(min_length=1)
    depends_on: tuple[str, ...] = ()  # lineage: upstream tables/columns
    status: Literal["active", "experimental"] = "active"  # experimental = never fed to models


class FeatureSnapshot(ImmutableModel):
    fixture_id: str = Field(min_length=1)
    feature_version: str
    data_version: str
    kickoff_utc: UtcDatetime
    information_cutoff: UtcDatetime
    generated_at: UtcDatetime
    values: FrozenFloatMap  # None = unavailable (NaN + availability flag)
    available_at: FrozenTimeMap = Field(default_factory=dict)  # feature -> latest source time
    unavailable_reasons: FrozenStrMap = Field(default_factory=dict)  # feature -> reason code

    @model_validator(mode="after")
    def _check(self) -> "FeatureSnapshot":
        check(FEATURE_VERSION_RE, self.feature_version, "feature_version")
        check(DATA_VERSION_RE, self.data_version, "data_version")
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
        valid = get_args(UnavailableReason)
        bad = [k for k, r in self.unavailable_reasons.items() if k not in self.values or r not in valid]
        if bad:
            raise ValueError(f"invalid unavailable_reasons entries: {sorted(bad)}")
        return self
