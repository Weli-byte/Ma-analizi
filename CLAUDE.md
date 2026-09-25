# CLAUDE.md — Global Football Forecasting Platform

Plan source: `docs/reference/Global_Football_Forecasting_MASTER_Sprint_Plani.pdf` (text: `master_sprint_plan.raw.txt`).
User writes Turkish; reply in user's language.

## Goal
Benchmark Elo/Poisson/Dixon-Coles/XGBoost/LightGBM/LLMs/ensemble on 1X2 probabilities; then live
forecasting, value analytics (paper only), dashboard, API. Priority order: data correctness -> leakage
control -> baseline -> statistical -> ML -> walk-forward -> LLM -> calibration -> ensemble -> global
live -> value -> dashboard/API -> startup.

## Non-negotiable rules
- No random train/test split. Chronological / walk-forward only.
- Nothing after `information_cutoff` enters a feature. Rolling features exclude current fixture.
- Final test set never used for selection/tuning.
- Ensemble trains on OOF predictions only.
- Predictions immutable; change = new snapshot. Carry model_version, feature_version, data_version,
  generated_at, information_cutoff.
- Metrics: Log Loss, Brier, RPS, ECE primary. Accuracy secondary only. No single "winner" label.
- Historical LLM benchmark separate from prospective; report memorization risk.
- Value engine = paper trading only. No real-money execution. No guarantee/certain-outcome language.
- No hard-coded secrets (env var names in configs; `.env` gitignored).
- Missing values: NaN + availability flag, never silent 0.
- Notebooks not for production logic. Mock provider calls in tests.
- Implement ONLY the current sprint's scope.

## Commands
```
.venv\Scripts\python -m pytest         # tests
.venv\Scripts\python -m ruff check .   # lint
```
Every sprint: unit + integration + regression + replay tests. Invariants: probs sum 1; future data
can't affect features; locked prediction immutable; same event/ingestion twice = no change; OOF-only stacking.

## Layout
`src/models/baselines.py` + `src/evaluation` (S3: metrics, dataset, runner, run_baselines) · `src/features` (S2: history, compute, registry, builder, leakage_audit) · `src/data` (S1: leagues, teams, manifest, clean, pipeline, quality) · `src/schemas` (Fixture, FeatureSnapshot, PredictionRecord, ExperimentRecord) · `src/config` (typed YAML
loader) · `src/versioning.py` (naming regex, config_hash) · `configs/*.yaml` · `docs/` (benchmark_protocol,
leakage_policy, versioning, adr) · other `src/*` empty until their sprint.

## Naming
model_id `snake_case`; model_version semver; feature_version `fvN`; data_version `dvN`. Commits:
Conventional, scope sprint, e.g. `feat(s1): ...`.

## Sprint status
- [x] S0 scope/repo/contracts (2026-09-25)
- [x] S1 historical data pipeline DONE 2026-09-25: dv1 = EPL+LALIGA 2019-20..2023-24, 3800 fixtures, quality report PASS (EPL 1920-2324 + LALIGA 2021-22/2023-24 from Wayback archive, rest user-downloaded)
- [x] S2 temporal features DONE 2026-09-25 (fv1, src/features, docs/features.md, 59 tests)
- [x] S3 baselines DONE 2026-09-25 (src/models/baselines.py, src/evaluation, docs/baselines.md, 78 tests)
- [ ] S4 Elo · S5 Poisson/DC · S6 XGB/LGBM · S7 walk-forward
- [ ] S8 LLM · S9 calibration · S10 LLM leakage · S11 ensemble · S12 ingestion · S13 pre-match · S14 live
- [ ] S15 odds/EV · S16 MLOps · S17 dashboard · S18 API · S19 startup MVP
MVP cut: 1-2 leagues, 5+ seasons, Home/Elo/Poisson/XGBoost/1 LLM, walk-forward, dashboard, leaderboard.
