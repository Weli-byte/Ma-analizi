# CLAUDE.md — Global Football Forecasting Platform

Plan source: `docs/reference/Global_Football_Forecasting_MASTER_Sprint_Plani.pdf`. User writes Turkish; reply in the
user's language. Remote: github.com/Weli-byte/Ma-analizi (work on branches; `main` = green).

## Goal
Benchmark Elo/Poisson/Dixon-Coles/XGBoost/LightGBM/LLMs/ensemble on 1X2 probabilities; then live forecasting,
value analytics (paper only), dashboard, API. Order: data correctness → leakage control → baseline → statistical →
ML → walk-forward → LLM → calibration → ensemble → global live → value → dashboard/API → startup.

## Non-negotiable rules
- No random train/test split. Chronological / walk-forward only. Split is config-driven (ADR 0012).
- Nothing after `information_cutoff` enters a feature. Only FINISHED matches with `result_available_at_utc <= cutoff`
  are history (the availability time is INFERRED for historical data; never present it as observed).
- Final-test seasons are technically locked (`EvaluationContext`); only `run_final_evaluation()` (FINAL mode) reads
  them, once. Never load them for training/validation/tuning/calibration/selection.
- Every artifact carries the content-derived `data_version` (`dv-<hash>`); loaders fail loudly on stale artifacts.
- Predictions are immutable, content-hashed; a changed prediction = new snapshot; ledger rejects conflicts.
- Missing values: NaN + `_available` flag + reason; never silent 0; no silent fallbacks (availability report).
- Odds: only `timestamp_quality=exact` odds may drive edge/EV/CLV. Closing odds = REFERENCE_MARKET_BASELINE.
- xG is NOT available (experimental, never produced). Never impute or fake data.
- TLS verification is never disabled. Never kill all python processes (PID + ownership only).
- No hard-coded secrets. Never claim CI is green unless GitHub Actions actually ran green; never claim
  reproducibility without running the reproducibility test.
- Metrics: Log Loss, Brier, RPS, ECE primary; accuracy secondary (ties = fractional credit). No single "winner".
- Methodological changes require an ADR. Golden artifacts change only with an ADR (`scripts/update_golden.py`).
- Implement ONLY the current sprint's scope. Notebooks are not for production logic.

## Commands (Windows: `.venv\Scripts\python`)
```
python -m pytest                              # 300+ tests (golden, reproducibility, CLI, leakage, provenance)
python -m ruff check .
python -m src.data.pipeline --mode strict     # atomic, content-versioned dataset
python -m src.features.builder --mode strict
python -m src.evaluation.run_baselines --mode strict
python -m src.evaluation.walk_forward --mode strict   # expanding/rolling season-by-season backtest
python -m src.data.team_resolution review     # unresolved team names
```
Dependencies: edit `pyproject.toml`, regenerate `requirements.lock` (uv, hashed). Python 3.12 + 3.14.
Lock regeneration MUST use `uv pip compile pyproject.toml -o requirements.lock --generate-hashes --upgrade`
(the `--upgrade` flag is required — without it `uv` treats the existing lock file as a soft
preference and can pin a stale transitive version that only the fresh-file `lock-up-to-date` CI
job catches; see incident `dec5c04` (H-05, hardening audit Phase 23).

## Layout
`src/schemas` (Fixture lifecycle, FeatureSnapshot, PredictionRecord + lifecycle/ledger, ExperimentRecord, frozen
containers) · `src/config` (typed YAML, all fields consumed or reserved) · `src/data` (download, raw_validation,
manifest, checksums, versioning, dataset, pipeline, clean, quality, teams, team_resolution, timezones) ·
`src/features` (history, compute, registry, builder, artifact, availability, leakage_audit) ·
`src/evaluation` (metrics, context, split, dataset, runner, run_baselines, walk_forward, final) ·
`src/models` (baselines.py, elo.py, poisson_dc.py, gbm.py) · `src/provenance.py`, `src/runmode.py` · `configs/` ·
`docs/adr/0001-0017` · `tests/fixtures/golden`.

## Status
- [x] S0–S3 built and REMEDIATED (see `reports/remediation/FINAL_S0_S3_REMEDIATION_REPORT.md`).
- [x] S4 Elo (`src/models/elo.py`, `tests/test_elo.py`, `docs/baselines.md`).
- [x] S5 Poisson/Dixon-Coles (`src/models/poisson_dc.py`, `tests/test_poisson_dc.py`, `docs/poisson_dc.md`).
- [x] S6 XGBoost/LightGBM + Optuna (`src/models/gbm.py`, `tests/test_gbm.py`, `docs/gbm.md`).
- [x] S7 walk-forward engine (`src/evaluation/walk_forward.py`, `tests/test_walk_forward.py`, `docs/walk_forward.md`).
- [~] S0-S7 research-grade hardening pass IN PROGRESS — see `reports/remediation/S0_S7_HARDENING_AUDIT.md`
      and ADR 0013-0016. `READY_FOR_S8 = FALSE` until the hardening pass's final report says otherwise.
- [ ] S8 LLM · S9 calibration · S10 LLM leakage ·
      S11 ensemble · S12 ingestion (commercial data source + timestamped odds + xG decision) · S13 pre-match ·
      S14 live · S15 odds/EV · S16 MLOps · S17 dashboard · S18 API · S19 startup MVP.
Data source is RESEARCH_ONLY (docs/data_sources/licensing.md): resolve licensing before any commercial use.
