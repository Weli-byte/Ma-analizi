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
class EloTuningConfig(_Cfg):
    """S0-S7 hardening Phase 6: temporal (walk-forward) hyperparameter search. Never tuned on
    final-test data -- consumed only by src.models.elo_tuning, which reads walk-forward folds."""

    enabled: bool = False
    method: Literal["optuna"] = "optuna"
    objective: Literal["log_loss"] = "log_loss"
    n_trials: int = Field(default=20, ge=0)  # 0 = skip search, tuned == baseline (still evaluated)
    seed: int = 42
    k_factor_range: tuple[float, float] = (5.0, 60.0)
    home_advantage_range: tuple[float, float] = (0.0, 150.0)
    decay_half_life_days_range: tuple[float, float] | None = (30.0, 3650.0)


class EloConfig(_Cfg):
    """S4: rating engine hyperparameters. Recorded verbatim into ExperimentRecord.config."""

    initial_rating: float = 1500.0
    k_factor: float = Field(default=20.0, gt=0)
    home_advantage: float = 60.0
    use_margin_of_victory: bool = False  # off by default: no goal-margin feature exists yet
    decay_half_life_days: float | None = None  # None = no decay (S0-S7 hardening Phase 6)
    tuning: EloTuningConfig = EloTuningConfig()


class PoissonConfig(_Cfg):
    """S5: Poisson / Dixon-Coles goal model hyperparameters. Recorded into ExperimentRecord.config."""

    max_goals: int = Field(default=10, ge=1)
    max_iterations: int = Field(default=200, ge=1)  # IPF sweep upper bound (S0-S7 Phase 7)
    convergence_tolerance: float = Field(default=1e-6, gt=0)
    fail_on_non_convergence: bool = False  # strict-research posture: opt-in, not default-fatal
    decay_half_life_days: float | None = None  # None = no time decay
    tail_mass_warn_threshold: float = Field(default=0.01, gt=0, lt=1)


class GBMTrialBudget(_Cfg):
    """S0-S7 hardening Phase 8: Optuna trial count is a serious research knob, not one flat
    number for every run mode (audit finding M-10)."""

    development: int = Field(default=2, ge=0)
    research: int = Field(default=8, ge=0)
    strict: int = Field(default=8, ge=0)
    final: int = Field(default=30, ge=0)


class GBMConfig(_Cfg):
    """S6: XGBoost/LightGBM hyperparameter-search hyperparameters (not the model's own
    hyperparameters — those come out of Optuna and are recorded in ExperimentRecord.config)."""

    seed: int = 42
    n_optuna_trials: GBMTrialBudget = GBMTrialBudget()
    validation_fraction: float = Field(default=0.15, gt=0, lt=1)
    early_stopping_rounds: int = Field(default=20, ge=1)
    n_temporal_folds: int = Field(default=1, ge=1)  # >1: multi-window chronological tuning (Phase 8)


class ModelConfig(_Cfg):
    seed: int = 42
    feature_version: str
    models: list[str]
    elo: EloConfig = EloConfig()
    poisson: PoissonConfig = PoissonConfig()
    gbm: GBMConfig = GBMConfig()
    # S0-S7 hardening Phase 9 (audit finding M-17): walk-forward can run a cheaper/different
    # model subset than run_baselines/final; None = fall back to `models` (unchanged default).
    walk_forward_models: list[str] | None = None


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
    metrics: list[Literal["log_loss", "brier", "rps", "ece_raw", "accuracy"]]
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


class LLMBudget(_Cfg):
    """ADR 0024 no-cost-surprise policy: checked by `src.llm.budget` BEFORE any API call."""

    max_requests_per_run: int = Field(default=3, ge=1)
    max_total_tokens: int = Field(default=6000, ge=1)
    max_estimated_cost_usd: float = Field(default=0.01, gt=0)
    max_concurrency: int = Field(default=1, ge=1)
    request_timeout_seconds: float = Field(default=30.0, gt=0)
    retry_limit: int = Field(default=2, ge=0)
    max_output_tokens: int = Field(default=400, ge=16)


class ProviderConfig(_Cfg):
    """S8 (LLM benchmark): validated here, consumed by `src.llm.runner.resolve_provider`."""

    providers: dict[str, ProviderEntry]
    budget: LLMBudget = Field(default_factory=LLMBudget)

    @model_validator(mode="after")
    def _no_literal_secrets(self) -> "ProviderConfig":
        for name, p in self.providers.items():
            if not p.api_key_env.isupper() or " " in p.api_key_env:
                raise ValueError(f"{name}.api_key_env must be an ENV VAR NAME, not a secret")
        return self

    def api_key(self, provider: str) -> str | None:
        return os.environ.get(self.providers[provider].api_key_env)


class ModelPrice(_Cfg):
    input_per_mtok: float = Field(ge=0)
    output_per_mtok: float = Field(ge=0)


class PricingConfig(_Cfg):
    """ADR 0024: the ONLY place LLM prices live; consumed by `src.llm.pricing`."""

    pricing_version: str = Field(min_length=1)
    retrieved_at_utc: str = Field(min_length=1)
    sources: dict[str, str]
    models: dict[str, ModelPrice]


class OddsConfig(_Cfg):
    """S15 (ADR 0030): paper-trading thresholds, consumed by `src.odds.paper`/`src.odds.run`."""

    min_edge: float = Field(ge=0)
    min_ev: float = Field(ge=0)
    stake_units: float = Field(gt=0)
    leagues: list[str] = Field(min_length=1)
    odds_api_reserve_credits: int = Field(ge=0)  # never spend the last credits of the month
    exact_horizon_hours: int = Field(gt=0)  # only fixtures kicking off within this horizon are considered
    odds_api_bookmakers: list[str] = Field(min_length=1, max_length=10)  # <=10 keys: 1 credit per call
    kelly_fraction: float = Field(gt=0, le=1)  # bet suggestions (ADR 0039): fraction of full Kelly
    max_stake_pct: float = Field(gt=0, le=100)  # cap of the stake hint, percent of bankroll


class _FreshnessDays(_Cfg):
    warning: int = Field(gt=0)
    stale: int = Field(gt=0)


class _ProviderThresholds(_Cfg):
    window_hours: int = Field(gt=0)
    max_error_rate: float = Field(ge=0, le=1)
    max_p95_latency_ms: float = Field(gt=0)
    stale_minutes: int = Field(gt=0)


class _HeartbeatThresholds(_Cfg):
    max_gap_minutes: int = Field(gt=0)
    names: list[str] = Field(min_length=1)


class _LLMThresholds(_Cfg):
    max_rate_limit_rate: float = Field(ge=0, le=1)
    max_server_error_rate: float = Field(ge=0, le=1)
    max_schema_failure_rate: float = Field(ge=0, le=1)
    max_failure_rate: float = Field(ge=0, le=1)
    max_p95_latency_ms: float = Field(gt=0)
    max_cost_usd_per_day: float = Field(gt=0)


class _DriftThresholds(_Cfg):
    min_samples: int = Field(gt=0)
    feature_z_threshold: float = Field(gt=0)
    psi_threshold: float = Field(gt=0)
    max_missing_share: float = Field(ge=0, le=1)


class _MetricDriftThresholds(_Cfg):
    min_settled: int = Field(gt=0)
    max_log_loss_increase: float = Field(ge=0)


class MlopsConfig(_Cfg):
    """S16 (ADR 0032): monitoring thresholds, consumed by `src.mlops.monitor` / `src.mlops.alerts`."""

    data_freshness_days: _FreshnessDays
    provider: _ProviderThresholds
    heartbeat: _HeartbeatThresholds
    llm: _LLMThresholds
    drift: _DriftThresholds
    metric_drift: _MetricDriftThresholds


# ------------------------------------------------------------ ingestion
class IngestionProviderEntry(_Cfg):
    enabled: bool = False
    api_key_env: str  # NAME of env var; never the key itself
    leagues: list[str] = Field(default_factory=list)  # provider's own league ids to sync


class IngestionConfig(_Cfg):
    """S12 (global fixture ingestion): validated here, consumed by
    `src.ingestion.sync.sync_league_season` callers (no built-in CLI orchestrator yet -- see
    ADR 0023; wiring a concrete sync command is left for whenever a provider is actually used)."""

    providers: dict[str, IngestionProviderEntry] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _no_literal_secrets(self) -> "IngestionConfig":
        for name, p in self.providers.items():
            if not p.api_key_env.isupper() or " " in p.api_key_env:
                raise ValueError(f"{name}.api_key_env must be an ENV VAR NAME, not a secret")
        return self

    def api_key(self, provider: str) -> str | None:
        return os.environ.get(self.providers[provider].api_key_env)


class MarketsConfig(_Cfg):
    """ADR 0041: match-intelligence markets."""

    half_life_days: float = Field(gt=0)
    shrink_pseudo_matches: float = Field(ge=0)
    max_goals: int = Field(ge=4, le=20)
    ipf_iterations: int = Field(ge=1)
    refit_block_days: int = Field(ge=1)
    count_stats: list[Literal["corners", "yellow_cards", "shots_on_target"]]
    over_under_goal_lines: list[float]
    over_under_lines: dict[str, list[float]]
    market_blend_weight: float = Field(ge=0, le=1)
    refresh_hours: float = Field(gt=0)


_MODELS = {
    "data": DataConfig,
    "leagues": LeaguesConfig,
    "sources": SourcesConfig,
    "features": FeaturesConfig,
    "model": ModelConfig,
    "evaluation": EvaluationConfig,
    "provider": ProviderConfig,
    "pricing": PricingConfig,
    "odds": OddsConfig,
    "mlops": MlopsConfig,
    "ingestion": IngestionConfig,
    "markets": MarketsConfig,
}

# Fields intentionally not consumed yet. Each needs a sprint tag; the consumption test checks it.
# ProviderConfig.providers/ProviderEntry.* (ex-S8) are now wired up by src/llm/runner.py's
# resolve_provider() and no longer reserved.
RESERVED_FIELDS = {
    "SourceEntry.provider": "docs",  # documentation/provenance label
    "PricingConfig.sources": "docs",  # where each price was read (provenance label)
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
