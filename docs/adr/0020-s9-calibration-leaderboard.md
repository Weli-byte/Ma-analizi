# ADR 0020: S9 calibration split protocol, reliability/leaderboard integration

## Status
Accepted — 2026-10-01.

## Context
S9 (`CLAUDE.md` sprint order) asks for calibration with raw/calibrated predictions kept
separate, reliability curves, a confidence histogram, and a global/league/season/model-class
leaderboard — on top of the Log Loss/Brier/RPS/ECE/Accuracy this repo already had from S0-S7.
`src/evaluation/calibration.py`, `reliability.py`, `leaderboard.py` (building blocks, previous
commit) are policy-free: they operate on whatever `(probs, outcomes)` arrays a caller passes.
This ADR is the policy: exactly which rows fit the calibration temperature, and which rows
report calibrated metrics, so "calibrated is better than raw" can never be an artifact of
testing on the same data that fit it.

## Decision: calibration split

`run_baselines.py` already evaluates every model on the SAME common-fixture set (rows every
model can predict, chronologically ordered — `runner.evaluate`'s `common_rows`/`probs`). This
set is split in half BY POSITION (chronological, since `common_rows` preserves `test`'s
chronological order):

- **first half** (`calib_rows`): fits each model's temperature `T` via
  `calibration.fit_temperature(raw_probs, outcomes)`. Never reported on.
- **second half** (`report_rows`): BOTH raw and calibrated (`calibration.apply_temperature(...,
  T)`) metrics are computed and reported, on the exact same rows, so "calibrated improved X" is
  never comparing apples to oranges.

This reuses the EXISTING validation-seasons split (ADR 0012) one level deeper — it does not add
a fourth top-level split or touch `EvaluationContext`'s season boundaries, and final-test seasons
remain exactly as unreachable as before. A model with too few common rows to split meaningfully
(⌊n/2⌋ < 10) skips calibration for that run and the report says so explicitly, rather than fitting
`T` on a handful of rows and reporting a noisy number as if it meant something.

## Decision: reliability curve / confidence histogram

Computed over the FULL common-fixture set (not split) on RAW probabilities — these are
descriptive diagnostics ("how confident is this model, and is that confidence earned"), not a
metric being validated for overfitting, so the full set is the right denominator for them.

## Decision: leaderboard

`leaderboard.build_leaderboard` is called once on `EvalReport.results` (unchanged — the existing
`ModelResult.by_league`/`by_season`), producing global/by_league/by_season/by_model_class tables
in the SAME run. Rendered into a new `leaderboard.md` section appended to the existing
`report.md`, and a `leaderboard` key added to `report.json`. No single winner: every model, every
metric, every scope — `render_leaderboard_md`'s own header states this, and the table never
ranks or sorts by a composite score.

## Decision: output shape (no golden-breaking format change)

New JSON keys (`calibration`, `reliability`, `confidence_histogram`, `leaderboard`) are ADDED to
`report.json`; existing keys are untouched. `predictions.jsonl` is unchanged — calibrated
probabilities are NOT written as new `PredictionRecord`s in this pass (S9 reports calibrated
METRICS; S9 does not mint a parallel `llm_x_calibrated`-style prediction stream — there is
nothing in the sprint plan asking for that, and it would double the ledger's row count for every
future run). `golden.json`'s exact-hash assertions (`tests/test_golden_and_reproducibility.py`)
only cover `predictions_sha256` (closed-form models) and compare `metrics` with a documented
tolerance against SPECIFIC keys already in `expected["metrics"]` — neither assertion requires
`summary["metrics"]` to have ONLY those keys, so no golden regeneration is needed for this ADR's
changes. Verified by running the full suite, including golden, after implementation.

## Alternatives considered

- **k-fold calibration within validation** (fit T on out-of-fold predictions across several
  folds, not just a chronological half) — more statistically efficient, but needs either
  `walk_forward`'s fold machinery wired into `run_baselines` (a bigger refactor) or a parallel
  fold loop duplicating it. The simple chronological half-split is enough to demonstrate honest
  raw-vs-calibrated separation for S9; revisit if S9's calibration quality itself becomes a
  research question worth the complexity.
- **Mint calibrated `PredictionRecord`s** — rejected (see output-shape decision above): no
  stated requirement for it, and it would be a larger, separate design question (a new
  `model_id` suffix convention, ledger growth) better left for whenever calibrated predictions
  are actually consumed downstream (S11 ensemble could plausibly want this — not decided here).
