# ADR 0012: Evaluation splits, season categories, run modes, metric rules

## Status
Accepted — 2026-09-26. This ADR documents METHODOLOGICAL CHANGES to the S3 protocol.

## Context
The original split (train 2019-22, validation 2022-23, final test 2023-24) was chosen by the assistant
without review. After the data refresh (2024-25, 2025-26 complete; 2026-27 partial) the final test can be a
genuinely unseen, recent period.

## Decision
- **Split** (`configs/evaluation.yaml`): train 2019-20..2021-22; validation 2022-23..2023-24;
  final test 2024-25..2025-26. Chronological only; the split manifest (`split_id`, periods, cutoffs, row
  and season counts, walk-forward folds from `split_strategy`/`min_train_seasons`) is produced by
  `src/evaluation/split.py` and stored with every run. Changed vs S3: validation grew from 1 to 2 seasons
  (2023-24 was never used as a test before), and the final test moved to seasons no model has seen.
- **Season categories**: `HISTORICAL_COMPLETE`, `CURRENT_PARTIAL` (2026-27), `FUTURE_FIXTURE` (rows without
  result after `as_of`), and the error state `INCOMPLETE_HISTORICAL`. Partial seasons belong to no split
  and cannot be read by any evaluation context.
- **Run modes**: DEVELOPMENT (flexible), RESEARCH (real git SHA, provenance complete, quality errors fail),
  STRICT (clean tree, known checksums, warnings fail), FINAL (STRICT + only mode that unlocks final data).
- **Accuracy tie rule**: fractional credit 1/k when k classes tie for the maximum probability. This
  replaces the S3 behaviour "ties resolve to home", which silently classified draws/ties as home wins.
  Consequence: uniform predictions score 1/3 instead of the home rate.
- **ECE**: top-label, equal-width bins (`calibration_bins`). **Uncertainty**: percentile bootstrap over
  fixtures (`bootstrap_samples`, `bootstrap_seed`); no significance claims.

## Alternatives
Keep 2023-24 as final test (rejected: spent data are older and one season is small); random splits
(forbidden).

## Consequences
S3 numbers on 2022-23 are unchanged (regression-checked: prior 1.0576, market 0.9699) except accuracy where
the tie rule applies. The final test has NOT been evaluated by any model.
