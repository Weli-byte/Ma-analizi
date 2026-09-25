from typing import Any, Self

from pydantic import Field, computed_field, model_validator

from src.versioning import DATA_VERSION_RE, check, config_hash

from .common import ExperimentType, ImmutableModel, UtcDatetime


class ExperimentRecord(ImmutableModel):
    run_id: str = Field(min_length=1)
    experiment_type: ExperimentType = ExperimentType.HISTORICAL_BACKTEST
    config: dict[str, Any]
    dataset_version: str
    git_sha: str = Field(pattern=r"^([0-9a-f]{7,40}|unknown)$")
    seed: int = 0
    created_at: UtcDatetime
    metrics: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> Self:
        check(DATA_VERSION_RE, self.dataset_version, "dataset_version")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def config_hash(self) -> str:
        return config_hash(self.config)
