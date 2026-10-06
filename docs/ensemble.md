# OOF ensemble / meta-model (S11, ADR 0022)

Run (after walk-forward has run for the same data/feature/split version):

```
python -m src.evaluation.walk_forward --mode research
python -m src.evaluation.run_ensemble --mode research
```

Output: `artifacts/ensemble/<data_version>_<feature_version>_<split_id>_ensemble/` (gitignored):
`report.json`, `report.md`, `hashes.json`.

## Four variants, compared, no single winner

- `simple_mean` — unweighted average (`src/evaluation/ensemble.py::simple_mean`).
- `validation_weighted` — per-model weight `∝ 1/log_loss` on the fit split.
- `logistic_stacking` — multinomial `LogisticRegression` over every base model's `[p_home,
  p_draw, p_away]`.
- `lightgbm_stacking` — a small LightGBM multiclass booster over the same meta-features.

Every variant's raw AND calibrated (temperature-scaled) metrics are reported together — reading
`report.md`'s table is the point, not picking a "winner" row.

## Split (ADR 0022)

The common out-of-fold fixture set (every configured base model predicted it, read from
`artifacts/walk_forward/<tag>/predictions.jsonl` — S7) is split into three chronological thirds:
**fit-ensemble** (combiner training) → **fit-calibration** (one temperature `T`) → **report**
(raw + calibrated metrics, on rows neither step above ever saw). A variant needs at least 45
common OOF rows (15 per third) or is reported as `skipped` with the reason, never silently fit on
too little data.

## Base models

Whatever `configs/model.yaml`'s `walk_forward_models` (or `models`, if unset) names — classical
models today (Elo, Poisson, Dixon-Coles, XGBoost, LightGBM, baselines). LLM base models
(GPT/Claude/Gemini, S8) can join the SAME ensemble the moment their `PredictionRecord`s exist in
the loaded set — the loader is model-id-generic — but are not wired in automatically yet (no
default checkout has real LLM predictions to test against honestly; see ADR 0022).

## Season/league breakdown

Every variant's report-split metrics are also grouped by `league_id` and `season`
(`_grouped_metrics`), in `report.json`'s `by_league`/`by_season`. Horizon (days-to-kickoff) is
omitted — no live/forward fixture feed exists yet (S13/S14), same as S9's leaderboard.

## Tests

`tests/test_ensemble.py` — the pure combination functions (`simple_mean`, `fit_validation_weights`,
`weighted_average`, `build_meta_features`, logistic/LightGBM stacking fit+predict).
`tests/test_run_ensemble.py` — the orchestration helpers, the skip-below-minimum path, and a real
end-to-end run against `tests/fixtures/real_smoke` (1140 real matches, enough rows to exercise
every variant's actual fit path, not just the skip branch).
