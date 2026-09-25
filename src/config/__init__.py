"""Config loading: configs/{data,model,evaluation,provider}.yaml -> typed models."""

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs"


class _Cfg(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DataConfig(_Cfg):
    raw_dir: str
    processed_dir: str
    data_version: str
    leagues: list[str]
    seasons: list[str]


class ModelConfig(_Cfg):
    seed: int = 42
    feature_version: str
    models: list[str]


class EvaluationConfig(_Cfg):
    split_strategy: Literal["expanding", "rolling"]
    min_train_seasons: int = Field(ge=1)
    metrics: list[str]
    calibration_bins: int = Field(ge=2)
    final_test_touched: Literal[False] = False  # final test set is untouchable
    # chronological split by season (S3 baselines: fit on train, report on validation)
    train_seasons: list[str] = Field(default_factory=list)
    validation_seasons: list[str] = Field(default_factory=list)
    final_test_seasons: list[str] = Field(default_factory=list)  # untouchable until final eval

    @model_validator(mode="after")
    def _disjoint_and_ordered(self) -> "EvaluationConfig":
        groups = [self.train_seasons, self.validation_seasons, self.final_test_seasons]
        flat = [s for g in groups for s in g]
        if len(flat) != len(set(flat)):
            raise ValueError("train/validation/final_test seasons must be disjoint")
        if flat != sorted(flat):
            raise ValueError("seasons must be chronological: train < validation < final_test")
        return self


class ProviderEntry(_Cfg):
    enabled: bool = False
    api_key_env: str  # NAME of env var; never the key itself
    model: str


class ProviderConfig(_Cfg):
    providers: dict[str, ProviderEntry]

    @model_validator(mode="after")
    def _no_literal_secrets(self) -> "ProviderConfig":
        for name, p in self.providers.items():
            if not p.api_key_env.isupper() or " " in p.api_key_env:
                raise ValueError(f"{name}.api_key_env must be an ENV VAR NAME, not a secret")
        return self

    def api_key(self, provider: str) -> str | None:
        return os.environ.get(self.providers[provider].api_key_env)


_MODELS = {
    "data": DataConfig,
    "model": ModelConfig,
    "evaluation": EvaluationConfig,
    "provider": ProviderConfig,
}


def load_config(name: str, config_dir: Path = CONFIG_DIR):
    if name not in _MODELS:
        raise KeyError(f"unknown config {name!r}; expected one of {sorted(_MODELS)}")
    path = config_dir / f"{name}.yaml"
    with path.open(encoding="utf-8") as f:
        return _MODELS[name].model_validate(yaml.safe_load(f))
