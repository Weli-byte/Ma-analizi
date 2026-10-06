# Calibration, reliability, leaderboard (S9, ADR 0020)

Built into `python -m src.evaluation.run_baselines` — no separate command. Output additions in
`artifacts/runs/<tag>/report.json`: `calibration`, `reliability`, `confidence_histogram`,
`leaderboard` keys; `report.md` gets a `## Leaderboard` section appended. `predictions.jsonl` is
unaffected (verified: its content hash is unchanged by this sprint).

## Calibration (`src/evaluation/calibration.py`)

Temperature scaling: one scalar `T` per model, fit by minimizing log loss
(`scipy.optimize.minimize_scalar`, bounded `[0.05, 20]`). `T>1` softens an overconfident model,
`T<1` sharpens an underconfident one; `T=1` is a no-op.

**Split (ADR 0020):** the common-fixture set every model is evaluated on is halved
chronologically — first half fits `T`, second half reports BOTH raw and calibrated metrics, on
the exact same rows. A model with fewer than `2 * MIN_CALIBRATION_ROWS` (20) common rows skips
calibration for that run and says why, rather than reporting a fit on a handful of rows.

## Reliability curve / confidence histogram (`src/evaluation/reliability.py`)

Computed over the FULL common set, on RAW probabilities (descriptive diagnostics, not something
being validated for overfitting). `reliability_curve` returns one row per non-empty confidence
bin: `confidence` (mean top-label probability), `accuracy` (mean fractional-credit correctness,
same tie rule as `metrics.ece`), `count`, `gap` (positive = overconfident) — exactly the
decomposition `metrics.ece` sums over, cross-checked in `tests/test_reliability.py`.
`confidence_histogram` is the same binning without the correctness axis.

## Leaderboard (`src/evaluation/leaderboard.py`)

`build_leaderboard` tabulates every model's metrics into `global`/`by_league`/`by_season`/
`by_model_class` scopes. **No single winner**: every model, every metric, every scope — never
collapsed into a ranked table or one "best" label, per `CLAUDE.md`'s non-negotiable rule.
Duck-typed (`.model_id`/`.model_class`/`.metrics`/`.by_league`/`.by_season` shape, not an import
of `runner.ModelResult`), so an LLM-sourced result built the same shape could join the same
leaderboard without `src.evaluation.leaderboard` depending on `src.llm`.

`by_horizon` (days-to-kickoff) is in the original sprint plan's dimension list but has no
meaning yet — no live/forward fixture feed exists (S13/S14). Add it the same way once that
exists.
