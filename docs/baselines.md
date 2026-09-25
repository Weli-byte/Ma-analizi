# S3 Baseline Benchmark

Run: `python -m src.features.builder` (once) then `python -m src.evaluation.run_baselines`.
Outputs: `reports/baselines_dv1_fv1.{md,json}`, `reports/experiment_baselines_dv1_fv1.json`.

## Split (configs/evaluation.yaml)
train 2019-20..2021-22 · validation 2022-23 (baselines are reported here) · final test 2023-24
(NOT loaded by any sprint until the final evaluation). Runner refuses train/eval overlap.

## Models (all `predict_proba -> [p_home, p_draw, p_away]`, not optimized)
- `always_home`: [1,0,0]. Degenerate; log loss = clip artefact.
- `historical_prior`: per-league outcome frequencies of the training period.
- `recent_form_naive`: fixed rule, non-draw mass split ∝ (1 + points last 5), draw = training draw rate;
  prior fallback when form is NaN (counted in diagnostics).
- `market_implied`: proportional de-vig of bookmaker odds (closing Avg -> closing B365 -> pre-match ...).
  Uses CLOSING odds: a reference bar, not a legitimate pre-cutoff signal.

## Evaluation
Common fixture set = fixtures every model can predict; identical for all models. Metrics: Log Loss
(clip 1e-15), multiclass Brier, RPS (ordered H<D<A), Accuracy (secondary; ties -> home). Reported
globally, per league and per season with sample size. No "winner" label; CIs and ECE come in S9.
Metric functions are tested against hand-computed values and edge cases (p=0, invalid probs).
