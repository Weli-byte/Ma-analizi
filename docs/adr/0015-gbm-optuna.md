# ADR 0015: XGBoost / LightGBM + Optuna methodology (S6)

## Status
Accepted — 2026-09-28 (written retroactively during S0-S7 hardening; see audit finding H-01).
Phases 17-22 of the hardening pass (feature audit, correlation audit, per-mode Optuna budgets,
temporal tuning, determinism documentation) extend this ADR's scope; they do not change the core
decisions recorded here (feature contract, tuning objective, leakage boundary).

## Context
S4/S5 (Elo, Poisson/DC) are structurally-constrained models (a handful of interpretable
parameters). S6's plan (`master_sprint_plan.raw.txt`, "S6 -- XGBOOST / LIGHTGBM + OPTUNA") calls
for feature-rich, flexible multiclass ML models using the S2 leakage-safe feature registry,
tuned via Optuna, with chronological (not random) validation, early stopping, calibration
readiness, and SHAP diagnostics.

## Problem
Four design problems specific to tree-boosted multiclass models:
1. Which features are allowed as input (must be exactly the leakage-safe contract, nothing
   invented).
2. How to represent missingness (raw `NaN` alone, an explicit flag, or both).
3. How to tune hyperparameters WITHOUT touching validation/final-test seasons or using a random
   (non-chronological) split for the internal tuning objective.
4. How to keep the tuning objective aligned with the project's PRIMARY metric (Log Loss) while
   still surfacing RPS, since Rule 12 (hardening task) explicitly forbids optimizing for Accuracy
   when the primary objective is probabilistic forecasting.

## Decision
- **Feature contract**: exactly `src.features.registry.produced_names()` (the S2 leakage-safe
  registry) plus each feature's own `_available` flag as a SEPARATE input column
  (`src/models/gbm.py::_design_matrix`). No feature outside this registry is ever used.
- **Missingness representation**: BOTH native NaN routing (both XGBoost and LightGBM natively
  treat `NaN` as "missing" and learn a routing direction at each split) AND the explicit
  `_available` flag column — belt-and-suspenders, consistent with the project's
  "never silent 0, no silent fallbacks" rule (ADR 0009).
- **Internal chronological split**: `fit(train)` orders rows by `kickoff_utc` and holds out the
  LAST `validation_fraction` (config, default 0.15) as an internal validation set — used for
  BOTH Optuna's objective and early stopping (`_chronological_split`). This never touches the
  outer validation/final-test seasons (`EvaluationContext` already prevents `fit()` from ever
  seeing them; the internal split further subdivides only what `fit()` itself received).
- **Optuna objective**: validation LOG LOSS is the only quantity the study minimizes
  (`GBMModel._tune`). RPS is computed every trial and recorded as `trial.set_user_attr("rps", ...)`
  for secondary reporting — explicitly NEVER used to override the primary objective or select the
  winning trial, directly satisfying Rule 12.
- **Determinism**: seeded `optuna.samplers.TPESampler(seed=self.seed)`; both boosters take
  `seed`/`seed` params directly (not left to library defaults).
- **SHAP**: computed post-fit on up to 300 internal-validation rows, stored in
  `diagnostics["top_shap_features"]` — explicitly diagnostic-only, never used to select or prune
  the feature set (S6 plan: "model selection'ı yalnız importance'a göre yapma").

## Alternatives considered
- **Random K-fold cross-validation** for Optuna's objective: rejected outright — violates the
  project's chronological-only rule (Rule 1 of the master hardening prompt, and ADR 0012).
- **A single flat feature vector without `_available` flags**, relying purely on native NaN
  handling: rejected — the explicit flag costs one column per feature and gives the model an
  unambiguous "was this observed" signal beyond what NaN-routing alone implies (NaN-routing
  optimizes prediction quality, not transparency about missingness for diagnostics/audits).
- **Accuracy or RPS as the primary Optuna objective**: rejected per Rule 12 — Log Loss is the
  project's stated primary probabilistic metric; Accuracy in particular is explicitly a secondary
  metric only, with fractional tie credit (ADR 0012), unsuitable as a tuning objective for a
  probability-producing model.
- **A single, one-size-fits-all Optuna trial budget with no per-mode distinction**: this is what
  S6 SHIPPED (`n_optuna_trials=8` flat), acknowledged here as a known limitation (audit finding
  M-10), not a considered-and-rejected alternative — Phase 19 of the hardening pass adds
  per-`RunMode` budgets (`development`/`research`/`final`).

## Why chosen
The feature contract and internal-chronological-split design directly satisfy the project's
non-negotiable leakage rules with the smallest amount of new machinery; using Log Loss as the
sole Optuna objective (with RPS recorded but non-binding) is the most literal implementation of
Rule 12 available without inventing a custom multi-objective scheme that the plan doesn't ask for.

## Leakage considerations
- `required_features = FEATURES` (the S2 registry) is enforced through the SAME
  `build_report`/`enforce` availability-governance path every other feature-consuming model uses
  (`src/features/availability.py`), so GBM's feature availability is measured/enforced
  identically to e.g. `recent_form_naive`.
- The internal validation split is entirely inside the `train` rows `fit()` received — it can
  never include validation/final-test rows, because `fit()` itself is never called with them (the
  `EvaluationContext` in `run_baselines.py`/`walk_forward.py`/`final.py` prevents that upstream).
- Early stopping uses `X_val`/`y_val` (the internal split) exclusively — verified functionally by
  `tests/test_gbm.py`; audit finding L-03 notes a STRONGER, explicit negative/mutation-style test
  for this is still owed (Phase 21 of hardening), distinct from the current functional coverage.

## Evaluation consequences
Real validation-period numbers: `xgboost` Log Loss 1.0050, `lightgbm` 1.0112 — both above
`historical_prior` (1.0615, i.e. beating it) but below `elo` (0.9718) and `market_implied`
(0.9476). This ADR explicitly does NOT chase beating the market by relaxing the leakage/tuning
rules above (see audit finding M-07 and Rule 16 of the hardening master prompt) — the honest
floor is reported, not manipulated.

## Computational consequences
Full model-matrix real-data run (9 models incl. both GBMs, 8 Optuna trials each,
`early_stopping_rounds=20`, up to 500 boosting rounds): ~66s wall time measured on real data
(2280 train rows). This scales with `n_optuna_trials × n_folds` when run inside `walk_forward`
(audit finding M-16 — no runtime accounting artifact exists yet; Phase 30 adds one).

## Known limitations
- `n_optuna_trials=8` is a baseline, not a serious research budget (M-10) — Phase 19 adds
  per-mode budgets; this ADR's OBJECTIVE design (Log Loss primary, RPS secondary) is unaffected
  by that change.
- Single internal chronological holdout, not multi-window temporal CV (M-11) — Phase 20 builds a
  temporal tuning evaluator reusing `walk_forward_folds`, without changing the leakage boundary
  this ADR already establishes.
- No structured feature audit artifact (null rate, variance, correlation per feature) exists yet
  (M-08) — Phase 17; no automatic correlated-feature reduction is planned even after that audit
  (M-09) — comparison only, never automatic removal, per Rule 4/18.
- Determinism is same-machine/same-run verified; cross-platform bitwise-identical output from
  XGBoost/LightGBM's internal threading is NOT claimed (L-04).

## Amendment (S0-S7 hardening, Phase 8): per-mode budget, temporal CV, feature audit, leakage test

- **Per-mode Optuna budget (M-10, closed)**: `GBMConfig.n_optuna_trials` is now a
  `GBMTrialBudget` (`development`/`research`/`strict`/`final`, mirroring `FallbackThresholds`'s
  existing per-mode pattern), not one flat number. `build_models(..., mode=...)` resolves the
  right budget for the run's `RunMode` (default config: 2/8/8/30). `run_baselines`/
  `walk_forward`/`final` all now pass their own `mode` through.
- **Temporal (multi-window) tuning (M-11, closed)**: `GBMConfig.n_temporal_folds` (default `1`,
  fully backward-compatible — reduces to exactly the old single last-`validation_fraction`
  holdout, tested). `>1` builds that many chronological EXPANDING windows
  (`src.models.gbm._temporal_cv_folds`) purely from row-count cuts within whatever `train` rows
  `fit()` itself received (no dependency on season metadata) — Optuna's objective becomes the
  MEAN validation log loss across every fold, never a single window's number. The deployed
  booster is still fit on the most recent (last) chronological window, same as before.
- **Feature audit (M-08, closed)**: `scripts/gbm_feature_audit.py` — every feature grouped
  (form/scoring/conceding/home-away/opponent/rest/streaks/future-planned) with its contract
  metadata (type, `available_at`, source, leakage status, feature version) from
  `src.features.registry`, plus runtime null-rate/variance from the currently built feature
  artifact when one exists. Also reports the REAL correlation matrix within
  `form_points_{3,5,10}` (real data: 0.83/0.82/0.69 pairwise — confirms the expected
  nested-window correlation).
- **Correlated features (M-09, closed)**: `scripts/gbm_feature_reduction_comparison.py` fits
  XGBoost on the full vs a reduced (`form_points_3`/`form_points_10` excluded, `form_points_5`
  kept) feature set across every walk-forward fold, reporting log loss per fold for both —
  `GBMModel.excluded_features` (new constructor param) makes this possible without touching the
  feature CONTRACT (`FEATURES`/`FEATURE_NAMES` stay fixed-shape; excluded columns are forced to
  NaN/unavailable, same as any other genuinely-missing feature). Real-data result: mixed sign
  across folds (+0.0054, +0.0019, −0.0066) — NOT consistently negative, so no removal is
  warranted by this evidence; nothing was removed.
- **Early-stopping leakage test (L-03, closed)**: added an explicit mutation-style test
  (`test_early_stopping_uses_only_the_internal_validation_split_never_anything_else`) —
  corrupting only the internal validation labels changes the fitted booster's `best_iteration`
  or chosen hyperparameters, positively confirming early stopping reads `X_val`/`y_val` and
  nothing else. A second test confirms `predict_proba` is read-only and can never affect
  `best_iteration` or later predictions regardless of what rows it's called with.
- **Determinism (L-04, unchanged/reaffirmed)**: multi-fold temporal CV re-verified
  deterministic (same seed → same predictions) in addition to the existing single-fold case;
  cross-platform bitwise-identical output is still NOT claimed (same class of limitation as
  ADR-0017's H-09 finding for Elo/Poisson/DC, though GBM's tree-split arithmetic has not itself
  been observed to trigger it — tree splits are comparisons, not accumulating sums, and are
  structurally less exposed to this than Elo/Poisson's iterative float accumulation).

## Revisit conditions
Revisit if: the feature contract source changes (e.g. S12 adds new leakage-safe features and
GBM should consume them — that's an automatic consequence of `produced_names()` changing, not a
methodology change, so likely doesn't need a NEW ADR unless the missingness representation also
changes), the Optuna objective changes from pure Log Loss to a multi-objective or weighted
scheme, the internal split strategy's DEFAULT changes from a single chronological holdout to
multi-window temporal CV (`n_temporal_folds` default moving off `1`), or
`gbm_feature_reduction_comparison.py`'s evidence is ever acted on to actually remove
`form_points_3`/`form_points_10` (that would need its own ADR, not just this ADR's amendment).
