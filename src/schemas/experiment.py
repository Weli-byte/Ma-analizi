from typing import Any, Self

from pydantic import Field, computed_field, model_validator

from src.runmode import RunMode
from src.versioning import DATA_VERSION_RE, FEATURE_VERSION_RE, MODEL_ID_RE, check, config_hash

from .common import ExperimentType, ImmutableModel, UtcDatetime
from .frozen import FrozenMap, thaw


class ExperimentRecord(ImmutableModel):
    """One experiment (one model run). Provenance is mandatory outside DEVELOPMENT mode."""

    experiment_id: str = Field(min_length=1)
    experiment_type: ExperimentType = ExperimentType.HISTORICAL_BACKTEST
    run_mode: RunMode
    git_sha: str = Field(pattern=r"^([0-9a-f]{40}|unknown)$")
    git_dirty: bool
    dirty_files: tuple[str, ...] = ()
    python_version: str = Field(min_length=1)
    platform: str = Field(min_length=1)
    dependency_lock_hash: str = Field(min_length=1)
    data_version: str
    feature_version: str
    config: FrozenMap
    model_name: str
    model_version: str
    seed: int = 0
    split_id: str = Field(min_length=1)
    train_rows: int = Field(ge=0)
    validation_rows: int = Field(ge=0)
    final_test_rows: int = Field(ge=0)
    metrics: FrozenMap
    created_at_utc: UtcDatetime

    @model_validator(mode="after")
    def _check(self) -> Self:
        check(DATA_VERSION_RE, self.data_version, "data_version")
        check(FEATURE_VERSION_RE, self.feature_version, "feature_version")
        check(MODEL_ID_RE, self.model_name, "model_name")
        if self.run_mode != RunMode.DEVELOPMENT:
            if self.git_sha == "unknown":
                raise ValueError("git_sha 'unknown' is only allowed in development mode")
            if self.dependency_lock_hash == "missing":
                raise ValueError("dependency_lock_hash is required outside development mode")
        if self.run_mode in (RunMode.STRICT, RunMode.FINAL) and self.git_dirty:
            raise ValueError(f"{self.run_mode.value} experiments require a clean working tree")
        if self.git_dirty and not self.dirty_files and self.git_sha != "unknown":
            raise ValueError("git_dirty=true requires the list of changed files")
        if self.final_test_rows and self.run_mode != RunMode.FINAL:
            raise ValueError("final_test_rows > 0 is only allowed in final mode")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def config_hash(self) -> str:
        cfg: dict[str, Any] = thaw(self.config)
        return config_hash(cfg)
