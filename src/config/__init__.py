"""Typed configuration. Every field here must be consumed by code or be explicitly reserved
(tests/test_config_consumption.py enforces this)."""

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class _Cfg(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# ---------------------------------------------------------------- data
class TeamResolutionConfig(_Cfg):
    auto_register_new_teams: bool = False  # False: unknown names go to the review queue
    suggest_cutoff: float = Field(default=0.6, ge=0.0, le=1.0)  # similarity for SUGGESTIONS only


class AcknowledgedAnomaly(_Cfg):
    """A known source anomaly a human reviewed; it stays in the report but does not block STRICT."""

    check: str  # quality check id, e.g. Q17
    match: str  # substring of the reported item
    reason: str


class DataConfig(_Cfg):
    raw_dir: str
    processed_dir: str
    provenance_dir: str
    expected_checksums: str
    leagues: list[str]
    seasons: list[str]
    as_of: str | None = None  # ISO date; None -> today (UTC). Only affects future-fixture logic.
    team_resolution: TeamResolutionConfig = TeamResolutionConfig()
    acknowledged_anomalies: list[AcknowledgedAnomaly] = []


class LeagueFormat(_Cfg):
    """Competition format. Nothing about a league (teams, rounds, timezone) is hardcoded."""

    source_code: str
    country: str
    name: str
    tier: int
    source_timezone: str  # timezone of kickoff times in the SOURCE files
    n_teams: int = Field(ge=2)
    rounds: int = Field(default=2, ge=1)  # 2 = double round-robin
    season_start_month: int = Field(ge=1, le=12)  # earliest month a season may start (year Y)
    season_end_month: int = Field(ge=1, le=12)  # latest month it may end (year Y+1)
    max_plausible_goals: int = 10  # single-team goals above this are flagged as anomalous

    @property
    def matches_per_team(self) -> int:
        return (self.n_teams - 1) * self.rounds

    @property
    def matches_per_season(self) -> int:
        return self.n_teams * (self.n_teams - 1) * self.rounds // 2


class LeaguesConfig(_Cfg):
    leagues: dict[str, LeagueFormat]

    def by_source_code(self, code: str) -> tuple[str, LeagueFormat] | None:
        for lid, lf in self.leagues.items():
            if lf.source_code == code:
                return lid, lf
        return None


class SourceEntry(_Cfg):
    id: str
    provider: str
    origin: Literal["official", "archive", "manual", "other"]
    url_template: str
    snapshot: str | None = None  # archive snapshot timestamp, e.g. 20260901


class SourcesConfig(_Cfg):
    primary: SourceEntry
    fallbacks: list[SourceEntry] = Field(default_factory=list)
    allow_fallback: bool = False  # explicit opt-in; fallback provenance is always recorded
    retries: int = Field(default=3, ge=1)
    backoff_seconds: float = Field(default=2.0, ge=0)
    timeout_seconds: float = Field(default=60.0, gt=0)


# ------------------------------------------------------------ features
class FeaturesConfig(_Cfg):
    feature_version: str
    rest_days_cap: float = Field(gt=0)
    result_lag_hours: float = Field(ge=0)  # provisional research policy, see ADR 0006
    cutoff_offset_hours: float = Field(default=0.0, ge=0)


# --------------------------------------------------------------- model
class ModelConfig(_Cfg):
    seed: int = 42
    feature_version: str
    models: list[str]


# ---------------------------------------------------------- evaluation
class FallbackThresholds(_Cfg):
    development: float = Field(ge=0, le=1)
    research: float = Field(ge=0, le=1)
    strict: float = Field(ge=0, le=1)
    final: float = Field(ge=0, le=1)


class EvaluationConfig(_Cfg):
    split_strategy: Literal["expanding", "rolling"]
    min_train_seasons: int = Field(ge=1)
    rolling_window_seasons: int = Field(default=3, ge=1)
    metrics: list[Literal["log_loss", "brier", "rps", "ece", "accuracy"]]
    calibration_bins: int = Field(ge=2)
    bootstrap_samples: int = Field(ge=0)
    bootstrap_seed: int = 0
    max_fallback_rate: FallbackThresholds
    train_seasons: list[str]
    validation_seasons: list[str]
    final_test_seasons: list[str]  # technically locked; see src/evaluation/context.py

    @model_validator(mode="after")
    def _disjoint_and_ordered(self) -> "EvaluationConfig":
        groups = [self.train_seasons, self.validation_seasons, self.final_test_seasons]
        flat = [s for g in groups for s in g]
        if not self.train_seasons or not self.validation_seasons or not self.final_test_seasons:
            raise ValueError("train, validation and final_test seasons must all be non-empty")
        if len(flat) != len(set(flat)):
            raise ValueError("train/validation/final_test seasons must be disjoint")
        if flat != sorted(flat):
            raise ValueError("seasons must be chronological: train < validation < final_test")
        return self


# ------------------------------------------------------------ provider
class ProviderEntry(_Cfg):
    enabled: bool = False
    api_key_env: str  # NAME of env var; never the key itself
    model: str


class ProviderConfig(_Cfg):
    """RESERVED for S8 (LLM benchmark): validated here, consumed later."""

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
    "leagues": LeaguesConfig,
    "sources": SourcesConfig,
    "features": FeaturesConfig,
    "model": ModelConfig,
    "evaluation": EvaluationConfig,
    "provider": ProviderConfig,
}

# Fields intentionally not consumed yet. Each needs a sprint tag; the consumption test checks it.
RESERVED_FIELDS = {
    "ProviderConfig.providers": "S8",
    "ProviderEntry.enabled": "S8",
    "ProviderEntry.api_key_env": "S8",
    "ProviderEntry.model": "S8",
    "SourceEntry.provider": "docs",  # documentation/provenance label
    "AcknowledgedAnomaly.reason": "docs",  # human justification kept in the config
}


def config_dir_for(root: Path | None) -> Path:
    return CONFIG_DIR if root is None else Path(root) / "configs"


def load_config(name: str, config_dir: Path | None = None):
    if name not in _MODELS:
        raise KeyError(f"unknown config {name!r}; expected one of {sorted(_MODELS)}")
    path = (config_dir or CONFIG_DIR) / f"{name}.yaml"
    with path.open(encoding="utf-8") as f:
        return _MODELS[name].model_validate(yaml.safe_load(f))
