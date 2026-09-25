# ADR 0004: Technical isolation of the final test set

## Status
Accepted — 2026-09-26.

## Context
"Final test is untouchable" existed only as a config comment; `load_rows` could read any season.

## Decision
- All evaluation data access goes through `load_rows(ref, ctx, seasons, ...)` which needs an
  `EvaluationContext`. Modes: train, validation, tuning, calibration, model_selection, ensemble_fit
  cannot read final-test seasons (`FinalTestAccessError`); seasons outside every split (current partial
  data) are never readable.
- `final` contexts can only be created by `unlock_final()` (needs FINAL run mode: clean git tree, real
  SHA, known checksums). `run_final_evaluation()` fits on train+validation, evaluates once, writes a
  read-only artifact, logs the access (`artifacts/final_access_log.jsonl`) and refuses a second run for
  the same data/feature/split/model set.
- The split manifest only reads aggregate metadata (counts, min/max dates).

## Alternatives
Documentation only (status quo); filesystem permissions (not portable).

## Consequences
This guards against accidents, not deliberate bypass: code can still open the DuckDB file directly. The
feature builder computes features for every fixture (causally, from past results only); those rows sit in
the feature artifact but `load_rows` will not serve final-season rows outside FINAL mode.
