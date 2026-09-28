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
`n_optuna_trials` (config, default 8) trials with a seeded `TPESampler` (`seed`, config, default
42) for determinism. `n_optuna_trials=0` skips tuning and uses the class defaults.

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
given a fixed seed and single-threaded-equivalent training; verified in `tests/test_gbm.py`).

## Validation numbers (current split, `configs/evaluation.yaml`)

train 2019-20..2021-22 → validation 2022-23..2023-24: `xgboost` Log Loss 1.0050, `lightgbm`
1.0112 (both close to `historical_prior`'s 1.0615, below `elo`/`market_implied` on this feature
set and split — expected: the feature set is deliberately small (S2) and Optuna's budget is
modest by default; this is a floor to improve on, not a final result, and no "overall winner" is
declared — see `docs/baselines.md`).
