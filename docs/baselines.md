# Baseline benchmark (S3, remediated)

Run: `python -m src.data.pipeline` → `python -m src.features.builder` → `python -m src.evaluation.run_baselines`
(all support `--mode development|research|strict`; the final path is `python -m src.evaluation.final`).
Outputs: `artifacts/runs/<data_version>_<feature_version>_<split_id>/` (gitignored): `report.md`, `report.json`,
`predictions.jsonl` (immutable `PredictionRecord`s, status EVALUATED), `split_manifest.json`, `hashes.json`,
`experiments/<model>.json` (full provenance).

## Split (ADR 0012)
train 2019-20..2021-22 · validation 2022-23..2023-24 (baselines are reported here) · final test 2024-25..2025-26
(locked by `EvaluationContext`, ADR 0004). 2026-27 is a partial season outside every split.

## Models (`predict_proba → [p_home, p_draw, p_away]`, not optimized)
- `always_home` — [1,0,0], degenerate; log loss is dominated by the 1e-15 clip.
- `historical_prior` — per-league outcome frequencies of the training period.
- `recent_form_naive` — fixed rule (share ∝ 1 + points last 5; draw = training rate); fallbacks are counted and
  enforced by the availability report (ADR 0009).
- `market_implied` — proportional de-vig of closing odds; **REFERENCE_MARKET_BASELINE** (timestamp unknown; not a
  signal, not a competitor; ADR 0007).
- `elo` (S4, `src/models/elo.py`) — team-strength Elo, leakage-safe: rating updates happen only after a fixture's
  result is known, always using the rating from BEFORE that fixture. Config: `initial_rating`, `k_factor`,
  `home_advantage`, optional `use_margin_of_victory` (off — no goal-margin feature exists, never fabricated).
  Idempotent per `fixture_id`; full timestamped rating history in `model.history`; replaying the same
  chronologically-ordered rows always reproduces the same rating state (tested in `tests/test_elo.py`). Rating
  difference → 1X2 probability via a 3-outcome ordinal-logit ("proportional odds") mapping fitted once on the
  training replay only.
- `poisson` / `dixon_coles` (S5, `src/models/poisson_dc.py`, model card `docs/poisson_dc.md`) — attack/defense/
  home-advantage Poisson goal model fit by iterative proportional fitting on the training period only;
  `dixon_coles` adds the low-score correlation correction (fitted `rho`). Full scoreline probability matrix via
  `scoreline_matrix()`; 1X2 = triangular/diagonal sums.
- `xgboost` / `lightgbm` (S6, `src/models/gbm.py`, model card `docs/gbm.md`) — multiclass boosters on the S2
  leakage-safe feature set only, chronological internal validation split, Optuna-tuned (primary objective log
  loss, RPS secondary), early stopping, SHAP diagnostics (informational only), deterministic given a seed,
  artifact `dump()`/`load()` roundtrip.

## Metrics
Log Loss (clip 1e-15), multiclass Brier, RPS (ordered H<D<A), top-label ECE (`calibration_bins`), Accuracy
(fractional credit on exact ties). Bootstrap 95% intervals (`bootstrap_samples`, `bootstrap_seed`). Reported globally,
per league and per season with sample size. No overall-winner label; overlapping intervals mean a difference is
NOT established. ROI/CLV are not computed (no timestamped odds).

## Reading the numbers correctly
Baselines exist to give later models a floor and a reference. A model "beating" `historical_prior` is the minimum
bar; `market_implied` shows how far the closing market is ahead but is not a fair opponent.
