# XGBoost / LightGBM multiclass models (S6)

`src/models/gbm.py`: `XGBModel` (`model_id="xgboost"`) and `LGBMModel` (`model_id="lightgbm"`), both
subclassing the shared `GBMModel` scaffolding.

## Features (leakage-safe only)

Exactly `src.features.registry.produced_names()` — the S2 leakage-safe feature contract, nothing
else (no raw score, no post-match data) — plus each feature's own `_available` flag as a separate
input column. Both boosters route `NaN` natively as "missing"; the explicit flag additionally gives
the model an unambiguous "was this observed" signal (ADR 0009's no-silent-fallback policy applies
here too: a missing feature is never turned into an implicit 0, and `RecentFormNaive`-style
fallback counting still runs via the shared availability report in `run_baselines`/`final`).

## Chronological validation (no random split, ever)

`fit(train)` orders its rows by `kickoff_utc` and holds out the LAST `validation_fraction`
(config, default 0.15) as an internal validation set — entirely inside the season range
`EvaluationContext` already cleared for training. This validation set drives:
- Optuna's objective (below);
- early stopping (`early_stopping_rounds`, default 20).

The internal split never overlaps the outer validation/final-test seasons; `final_test_seasons`
are never read by `fit()` regardless (ADR 0004).

## Optuna

Primary objective: validation log loss (what the study minimizes). RPS is computed on every
trial and recorded as a `trial.user_attr` for secondary comparison/reporting — it never
overrides the primary objective or picks the winning trial (`diagnostics["optuna_best_rps_secondary"]`).
A seeded `TPESampler` (`seed`, config, default 42) for determinism. `n_optuna_trials=0` skips
tuning and uses the class defaults.

**Per-mode trial budget** (S0-S7 hardening Phase 8, audit finding M-10): `GBMConfig.
n_optuna_trials` is a `GBMTrialBudget` (`development`/`research`/`strict`/`final`), not one flat
number — `configs/model.yaml` defaults: 2/8/8/30. `build_models(..., mode=...)` resolves the
right budget for the run's `RunMode`; `run_baselines`/`walk_forward`/`final` all pass their own
mode through automatically.

**Temporal (multi-window) tuning** (Phase 8, M-11): `GBMConfig.n_temporal_folds` (default `1`,
exactly the single-holdout behavior below, unchanged). `>1` builds that many chronological
EXPANDING windows from the `train` rows `fit()` itself received
(`src.models.gbm._temporal_cv_folds`) — Optuna's objective becomes the MEAN validation log loss
across every fold, not one window's number. The deployed booster is still fit on the most
recent (last) chronological window.

## Early stopping

Both boosters fit with `num_boost_round=500` and `early_stopping_rounds` against the internal
validation set; when the validation set is too small (< 10 internal-split rows total) tuning
and early stopping are skipped and a fixed small `num_boost_round=100` is used instead — noted
in `diagnostics`, never silent.

## Calibration candidate

`predict_proba` returns raw (uncalibrated) probabilities; `self.raw_probs_` retains the last
call's output. Calibration itself (Platt/isotonic) is S9 scope and is expected to wrap this
model's raw output rather than modify it in place.

## SHAP

`diagnostics["top_shap_features"]` holds the top-10 mean-|SHAP| feature importances (via
`shap.TreeExplainer` on up to 300 internal-validation rows). Informational only — SHAP never
selects or prunes the feature set (S6 plan: "model selection'ı yalnız importance'a göre yapma").

## Artifact metadata & reload

`diagnostics` records: `training_window` (min/max kickoff of the fit rows), `hyperparameters`
(the actually-used params, post-tuning), `seed`, `n_optuna_trials`, `n_features`,
`optuna_best_log_loss` / `optuna_best_rps_secondary`. `git_sha`, `data_version`, `feature_version`
and `model_version` are recorded at the run level in `ExperimentRecord` (same as every other
model — see `docs/baselines.md`).

`dump()` / `load(bytes)` serialize/restore the fitted booster (XGBoost: `save_raw("json")` /
`load_model`; LightGBM: `model_to_string()` / `Booster(model_str=...)`) — `tests/test_gbm.py`
verifies a reload reproduces byte-identical (`atol=1e-6`) predictions.

## Determinism

Same `train` + same `seed` → identical `predict_proba` output (both boosters are deterministic
given a fixed seed and single-threaded-equivalent training; verified in `tests/test_gbm.py`,
including the multi-fold temporal CV path). Cross-platform bitwise-identical output is NOT
claimed (same class of limitation as ADR-0017's H-09 finding for Elo/Poisson/DC — tree-split
comparisons are structurally less exposed to CPU/SIMD dispatch variance than iterative float
accumulation, but this is not proven immunity).

## Feature audit & correlated-feature comparison (Phase 8, M-08/M-09)

`scripts/gbm_feature_audit.py`: every feature grouped (form/scoring/conceding/home-away/
opponent/rest/streaks/future-planned) with contract metadata from `src.features.registry` plus
runtime null-rate/variance from the built feature artifact, and the real correlation matrix
within `form_points_{3,5,10}` (0.83/0.82/0.69 pairwise on real data — confirms the expected
nested-window correlation).

`scripts/gbm_feature_reduction_comparison.py`: fits XGBoost on the full vs a reduced
(`form_points_3`/`form_points_10` excluded) feature set across every walk-forward fold via the
new `GBMModel.excluded_features` constructor param (masks columns to NaN/unavailable without
changing the feature contract's shape). Real-data result: mixed sign across folds — no removal
warranted; nothing was removed (diagnostic only, per Rule 4/18).

## Early-stopping leakage test (Phase 8, L-03)

`tests/test_gbm.py::test_early_stopping_uses_only_the_internal_validation_split_never_anything_else`:
a mutation-style test corrupting only the internal validation labels and confirming the fitted
booster's `best_iteration`/hyperparameters change — positive proof early stopping reads
`X_val`/`y_val` and nothing else. A companion test confirms `predict_proba` is read-only and can
never affect `best_iteration` or later predictions regardless of what rows it's called with.

## Validation numbers (current split, `configs/evaluation.yaml`)

train 2019-20..2021-22 → validation 2022-23..2023-24: `xgboost` Log Loss 1.0050, `lightgbm`
1.0112 (both close to `historical_prior`'s 1.0615, below `elo`/`market_implied` on this feature
set and split — expected: the feature set is deliberately small (S2) and Optuna's budget is
modest by default; this is a floor to improve on, not a final result, and no "overall winner" is
declared — see `docs/baselines.md`).
