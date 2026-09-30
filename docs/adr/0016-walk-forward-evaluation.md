# ADR 0016: Walk-forward backtest evaluation methodology (S7)

## Status
Accepted — 2026-09-28 (written retroactively during S0-S7 hardening; see audit finding H-01).
Phases 27-31 of the hardening pass (bootstrap CI, fold aggregation, runtime accounting,
configurable model subsets) extend this ADR's scope without changing the fold-construction or
final-test-isolation decisions recorded here.

## Context
`run_baselines`/`final` (S0-S3, ADR 0012) evaluate every model on exactly ONE chronological
split: fit on `train_seasons`, report on `validation_seasons`, `final_test_seasons` locked until
FINAL mode. S7's plan (`master_sprint_plan.raw.txt`, "S7 -- WALK-FORWARD BACKTEST ENGINE") calls
for "tek reproducible forecasting deney motoru" (one reproducible experiment engine) across
MULTIPLE chronological folds (expanding or rolling window, season-by-season), so that a model's
performance can be observed across several distinct historical periods rather than one.

## Problem
`src/evaluation/split.py::walk_forward_folds` (expanding/rolling fold construction) already
existed from the S0-S3 remediation but was never actually EXECUTED — `build_split_manifest`
computed the folds and stored them in the split manifest as metadata, but no code trained or
predicted anything per fold. S7's core problem was building the actual execution loop:
train→fit→predict→ledger→metrics per fold, with full provenance, while guaranteeing: (a)
final-test seasons remain structurally unreachable, (b) no state leaks between folds (a model
fit in fold 2 must not carry over rating/booster state from fold 1), (c) predictions across all
folds land in ONE immutable, content-hashed ledger without cross-fold ID collisions.

## Decision
- **Reuse the existing fold contract** (`walk_forward_folds`) unchanged — expanding/rolling per
  `evaluation.split_strategy`, season-by-season, entirely a pure function of `EvaluationConfig`.
- **Fresh model instances every fold** (`build_models(...)` called INSIDE the fold loop in
  `run_walk_forward`, `src/evaluation/walk_forward.py`) — no model object is reused across folds,
  eliminating any risk of Elo rating history / GBM booster state / Poisson IPF state leaking
  fold-to-fold.
- **Final-test isolation is STRUCTURAL, not policy**: every fold reads through
  `make_context(EvalMode.VALIDATION, eval_cfg)`, whose `allowed_seasons()` is
  `train_seasons + validation_seasons` only (`src/evaluation/context.py`); `walk_forward_folds`
  itself only ever combines those same two lists when building `fold.train_seasons`/
  `fold.test_season`. A final-test season literally cannot appear in a fold — this is enforced by
  the SAME `EvaluationContext` mechanism `run_baselines`/`final` already rely on (ADR 0004), not
  a new, separate check that could be forgotten or bypassed.
- **One prediction ledger across all folds** (`PredictionLedger`, accumulated via
  `all_records += _fold_predictions(...)` per fold): safe because `walk_forward_folds` gives each
  season EXACTLY ONE fold where it is the `test_season` — fixture IDs across folds' test sets are
  therefore disjoint by construction, so no cross-fold `(fixture_id, model_id)` collision is
  possible in the ledger (verified:
  `tests/test_walk_forward.py::test_test_season_prediction_never_reused_as_next_folds_train_label`).
- **`bootstrap_samples=0` for every fold** (as shipped in S7): walk-forward was scoped as a
  per-fold POINT-METRIC diagnostic loop; `run_baselines` remains the CI-bearing benchmark for the
  single train→validation split most sprints compare against. This is a deliberate SCOPE
  decision, not an oversight — but it is flagged in the hardening audit (finding H-06) because
  Rule 11 of the hardening task ("do not compare models using only one split if walk-forward
  evaluation is available") is harder to do RIGOROUSLY without per-fold confidence intervals;
  Phase 27 adds them without changing this ADR's fold-construction/isolation decisions.
- **A fold's test-season prediction is a diagnostic, never a model-selection signal** — stated
  explicitly in every walk-forward `report.json`'s `notes` field, mirroring the same discipline
  `run_baselines`/`docs/baselines.md` already apply ("no single overall winner").

## Alternatives considered
- **Reusing/warm-starting model state across folds** (e.g. carrying Elo ratings from fold 1 into
  fold 2's initial state): rejected — this would make later folds' results depend on earlier
  folds' specific outcome sequence beyond what "more training seasons" alone implies, complicating
  both reproducibility (a fold's result would depend on which folds ran before it, not just its
  own `train_seasons`) and the "test predictions never used for selection" guarantee. Fresh
  instances make each fold an independent, directly comparable experiment.
- **A separate, NEW final-test lock check specific to walk-forward**: rejected — reusing
  `EvaluationContext`/`make_context(EvalMode.VALIDATION, ...)` means walk-forward inherits the
  SAME, already-tested final-test isolation mechanism as every other evaluation path, rather than
  a parallel implementation that could drift out of sync or have its own bugs.
- **Bootstrap CI per fold from the start**: deferred, not rejected — S7 prioritized getting the
  fold-execution loop itself correct and tested first; per-fold CIs are Phase 27 of the hardening
  pass (audit finding H-06), an ADDITION to this decision, not a reversal of it.
- **A single aggregated cross-fold metric with no fold-level detail**: rejected even as a FUTURE
  direction — `report.json` always lists folds separately; Phase 28's aggregation (audit finding
  M-14) explicitly ADDS a summary WITHOUT removing fold-level detail, per the hardening task's
  Rule 29 ("never hide fold-level results").

## Why chosen
Reusing the existing fold contract and final-test-context mechanism minimizes new surface area
(and therefore new bug surface) for the highest-stakes property of the whole platform — final-test
isolation — while fresh-instance-per-fold gives the cleanest possible guarantee that each fold is
an independent, reproducible experiment whose result depends only on its own `train_seasons` and
the model's deterministic fitting procedure (already established per-model in ADRs 0013-0015).

## Leakage considerations
- Final-test seasons are unreachable by construction (see Decision above) — not re-verified by a
  NEW mechanism, but inherited from ADR 0004's `EvaluationContext`.
- `assert_chronological(train, test)` runs inside `evaluate()` for every fold (the SAME check
  `run_baselines`/`final` use), so a fold whose `train_seasons` don't strictly precede its
  `test_season` would fail loudly rather than silently produce a leaky result — verified
  additionally by `tests/test_walk_forward.py::test_folds_are_strictly_chronological`.
- Config hashing (Rule 8/`ExperimentRecord.config_hash`, a pre-existing computed field from S0-S3
  remediation) automatically differentiates every fold's experiment by including the fold
  definition (`train_seasons`, `test_season`, date ranges, cutoff) inside the hashed config blob
  — verified: `tests/test_walk_forward.py::test_config_hash_is_deterministic_and_fold_scoped`.

## Evaluation consequences
On the golden fixture (1 train + 1 validation season, `min_train_seasons=1`): exactly 1 fold,
matching `walk_forward_folds`' deterministic output. On the real dataset's current config (3
train + 2 validation seasons, `min_train_seasons=2`): 3 folds. No aggregate "walk-forward winner"
is computed or claimed (Rule 29); each fold's metrics stand on their own in `report.json`.

## Computational consequences
Runtime scales as `n_folds × n_models` (with GBM's own Optuna cost multiplying further per fold,
since fresh instances mean fresh tuning every time — audit finding M-16, no per-fold timing
artifact exists yet). On real data (3 folds, 9 models incl. both GBMs): not yet measured/recorded
as an artifact (Phase 30 of hardening adds this).

## Known limitations
All four items below are now CLOSED — see the Phase 9 amendment below.
- ~~`bootstrap_samples=0`: no per-fold confidence intervals (H-06) — Phase 27.~~
- ~~No cross-fold aggregate summary artifact, only per-fold `report.json` entries (M-14/M-15) —
  Phase 28 adds `reports/walk_forward_summary.*` WITHOUT removing fold-level detail.~~
- ~~No per-fold/per-model runtime accounting (M-16) — Phase 30.~~
- ~~Model list is always `model_cfg.models`, shared with `run_baselines`/`final`; no
  walk-forward-specific cheaper/more-expensive subset config exists (M-17) — Phase 31.~~

## Amendment (S0-S7 hardening Phase 9)

Closes the four "Known limitations" above, per the deferrals already recorded in Decision/
Alternatives-considered — no fold-construction or final-test-isolation decision changes.

- **H-06 — bootstrap CI**: `run_walk_forward` now passes `bootstrap_samples=eval_cfg.bootstrap_samples`
  (was hardcoded `0`) into each fold's `evaluate()` call, populating `FoldResult.confidence_intervals`
  per model per metric. Reuses the existing `evaluation.bootstrap_samples` knob `run_baselines`
  already reads — no new config field. Per the Revisit-conditions note above, this changes the
  MECHANISM (configurable) but the shipped DEFAULT is whatever `configs/evaluation.yaml` already
  set, so it is not a methodology-changing default flip.
- **M-14/M-15 — cross-fold aggregation**: `walk_forward_summary.json`/`.md` (mean, weighted mean by
  fold row count, std, min, max per model per metric) written alongside, never instead of,
  `report.json`'s per-fold entries (Rule 29 still holds — no aggregate "winner" is declared).
- **M-16 — runtime accounting**: `timings.json` records per-fold/per-model fit+predict wall time,
  via a `_time_wrap` method-wrapper. Deliberately excluded from `report.json` and from
  `ExperimentRecord.config` — wall-clock time is not a deterministic function of config/data/seed,
  and mixing it into either would break `test_reproducible_given_same_config_and_commit` and
  `test_config_hash_is_deterministic_and_fold_scoped`. Regression-guarded by
  `tests/test_walk_forward.py::test_timings_recorded_but_never_part_of_the_reproducibility_hash`.
- **M-17 — configurable model subset**: new `ModelConfig.walk_forward_models: list[str] | None`
  (default `None` = unchanged, falls back to `models`). `run_walk_forward` resolves
  `model_cfg.walk_forward_models or model_cfg.models` once per run. `run_baselines`/`final` are
  untouched — they still always read `model_cfg.models` directly.

Tests: `tests/test_walk_forward.py` (4 new: bootstrap CI populated, summary aggregates without
hiding fold detail, timings excluded from the reproducibility hash, model-subset override honored).

## Revisit conditions
Revisit if: the fold-construction algorithm itself changes (e.g. adding gap/embargo periods
between train and test beyond the current strict `max(train) < test_season` chronological
boundary), model state is deliberately allowed to persist across folds for a specific new
research question (would need its own ADR given the Alternatives-considered rejection above), or
`bootstrap_samples=0` is changed to a nonzero DEFAULT for walk-forward (Phase 27 makes it
configurable; changing the DEFAULT would be a methodology change warranting an amendment here).
