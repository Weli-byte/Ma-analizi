# Walk-forward backtest engine (S7)

`src/evaluation/walk_forward.py`:

```
python -m src.evaluation.walk_forward [--root DIR] [--mode development|research|strict]
```

Outputs: `artifacts/walk_forward/<data_version>_<feature_version>_<split_id>_walkforward/`
(gitignored) — `report.json` (per-fold metrics + confidence intervals + notes),
`walk_forward_summary.json`/`.md` (cross-fold aggregate — see below), `timings.json` (runtime
accounting, unhashed), `predictions.jsonl` (immutable, content-hashed `PredictionRecord`s across
every fold, status EVALUATED), `split_manifest.json`, `hashes.json`,
`experiments/fold<i>_<model>.json` (one `ExperimentRecord` per fold × model,
`experiment_type=HISTORICAL_BACKTEST`).

## What it does

One reproducible loop, re-used for every model in `configs/model.yaml`, over the folds
`src.evaluation.split.walk_forward_folds` derives from `configs/evaluation.yaml`
(`split_strategy: expanding|rolling`, `min_train_seasons`, `rolling_window_seasons`):

- **Expanding**: fold `i`'s train set is every season before `seasons[i]`; it only grows.
- **Rolling**: fold `i`'s train set is the last `rolling_window_seasons` seasons before it — a
  fixed-size window that slides forward.
- **Season-by-season**: each fold predicts exactly one held-out season (`test_season`); a
  season is a fold's test set exactly once across the whole loop.

Per fold: fresh model instances (`build_models`, no state carried over from the previous fold)
fit on `fold.train_seasons`, predict `fold.test_season`, append to ONE prediction ledger, and
one `ExperimentRecord` is written per model with that fold's dates, cutoff, `model_version`,
`feature_version`, git SHA and a `config_hash` (the record's existing computed field) — the
config hashed includes the fold definition, so every fold's experiments are distinguishable
even though the model/evaluation/features config blocks are identical across folds.

## Isolation from final-test (ADR 0004)

Every fold reads through `make_context(EvalMode.VALIDATION, eval_cfg)`, whose `allowed_seasons()`
is `train_seasons + validation_seasons` only — final-test seasons are structurally unreachable
here, not policy-excluded. `walk_forward_folds` itself only ever combines
`train_seasons + validation_seasons` when building folds, so a final-test season can never appear
as a fold's `train_seasons` or `test_season` either.

## Test predictions are a diagnostic, not a selection signal

Every walk-forward run's `report.json` carries this note verbatim, and it is the same discipline
`run_baselines`/`docs/baselines.md` already apply: a fold's test-season prediction may be
*reported* (metrics, comparison across models across folds) but must never be the basis for
picking a "winning" model to ship — that decision is reserved for the once-only FINAL-mode
evaluation (`src/evaluation/final.py`).

## Reproducibility & config hashing

Same config + same commit + same data content -> byte-identical `predictions.jsonl` / report
hashes (`tests/test_walk_forward.py::test_reproducible_given_same_config_and_commit`), because:
- fold construction is a pure function of `EvaluationConfig` (`walk_forward_folds`);
- every model class (S4/S5/S6) is deterministic given a fixed seed;
- each `ExperimentRecord.config_hash` is a canonical-JSON hash of its own config (model +
  evaluation + features + fold), so the SAME fold+config always hashes to the SAME value, and a
  DIFFERENT config (different fold, different hyperparameters, ...) always hashes differently.

## S0-S7 hardening Phase 9 additions

- **Bootstrap CIs per fold** (audit finding H-06, was hardcoded `bootstrap_samples=0`): now uses
  `evaluation.bootstrap_samples` — the same config `run_baselines` reads. Overlapping intervals
  still mean a difference is NOT established (uncertainty reporting, not a significance test).
- **Cross-fold aggregation** (M-14/M-15): `walk_forward_summary.json`/`.md` — mean, weighted
  mean (by that fold's common-row count), std, min, max, fold count, total matches, per model
  per metric. This is ADDED alongside `report.json`'s per-fold detail, never instead of it (Rule
  29: never hide fold-level results) — read `report.json` for the individual fold numbers.
- **Runtime accounting** (M-16): `timings.json` records each model's fit+predict wall time per
  fold and the fold's total. Deliberately kept OUT of `report.json`/`ExperimentRecord.config`:
  wall-clock time is not deterministic run-to-run, and both `report_json`'s hash and every
  `config_hash` must stay reproducible — mixing in timing would break that (tested:
  `test_timings_recorded_but_never_part_of_the_reproducibility_hash`).
- **Configurable model subset** (M-17): `ModelConfig.walk_forward_models` (default `None` = fall
  back to the shared `models` list, unchanged behavior). Set it to run a cheaper/different subset
  through walk-forward without touching `run_baselines`/`final`'s own model list.

## What it still deliberately does NOT do

No single "winner" is ever computed or declared from the aggregate — `walk_forward_summary.md`
is a comparison table, not a ranking; picking a model to ship remains the once-only FINAL-mode
evaluation's job (`src/evaluation/final.py`), same as `run_baselines` (`docs/baselines.md`).
