# CLAUDE.md — Global Football Forecasting Platform

Plan source: `docs/reference/Global_Football_Forecasting_MASTER_Sprint_Plani.pdf`. User writes Turkish; reply in the
user's language. Remote: github.com/Weli-byte/Ma-analizi (work on branches; `main` = green).

## Goal
Benchmark Elo/Poisson/Dixon-Coles/XGBoost/LightGBM/LLMs/ensemble on 1X2 probabilities; then live forecasting,
value analytics (suggestions only, ADR 0039; the system never places bets), dashboard, API. Order: data correctness → leakage control → baseline → statistical →
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
python -m src.llm.live_smoke                  # ONE real call per enabled provider (ADR 0024)
ALLOW_REAL_LLM_CALLS=true python -m src.llm.benchmark --track historical --limit 2   # plan, budget, coverage, LLM_REAL eval (ADR 0026)
pytest -m live tests/integration              # real provider tests (excluded from default run)
ALLOW_REAL_LLM_CALLS=true python -m src.llm.forecast --league PL   # real end-to-end forecast, next upcoming fixture
python -m src.llm.audit                       # S10 scan artifacts/llm_runs/ for tampering/leakage
python -m src.evaluation.run_ensemble [--llm-run DIR]   # S11 OOF ensemble; run walk_forward first; --llm-run adds REAL LLM models
```
Dependencies: edit `pyproject.toml`, regenerate `requirements.lock` (uv, hashed). Python 3.12 + 3.14.
Lock regeneration MUST use `uv pip compile pyproject.toml --extra dev --universal --generate-hashes --python-version 3.12 --upgrade -o requirements.lock`
(the `--upgrade` flag is required — without it `uv` treats the existing lock file as a soft
preference and can pin a stale transitive version that only the fresh-file `lock-up-to-date` CI
job catches; see incident `dec5c04` (H-05, hardening audit Phase 23).

## Layout
`src/schemas` (Fixture lifecycle, FeatureSnapshot, PredictionRecord + lifecycle/ledger, ExperimentRecord, frozen
containers) · `src/config` (typed YAML, all fields consumed or reserved) · `src/data` (download, raw_validation,
manifest, checksums, versioning, dataset, pipeline, clean, quality, teams, team_resolution, timezones) ·
`src/features` (history, compute, registry, builder, artifact, availability, leakage_audit) ·
`src/evaluation` (metrics, context, split, dataset, runner, run_baselines, walk_forward, final) ·
`src/models` (baselines.py, elo.py, poisson_dc.py, gbm.py) · `src/llm` (S8: providers.py, snapshot.py,
prompt.py, parse.py, runner.py, cli.py, audit.py) · `src/ingestion` (S12: provider.py, upsert.py,
coverage.py, cache.py, rate_limit.py, sync.py) · `src/provenance.py`, `src/runmode.py` · `configs/` ·
`docs/adr/0001-0023` · `tests/fixtures/golden`.

## Status
- [x] S0–S3 built and REMEDIATED (see `reports/remediation/FINAL_S0_S3_REMEDIATION_REPORT.md`).
- [x] S4 Elo (`src/models/elo.py`, `tests/test_elo.py`, `docs/baselines.md`).
- [x] S5 Poisson/Dixon-Coles (`src/models/poisson_dc.py`, `tests/test_poisson_dc.py`, `docs/poisson_dc.md`).
- [x] S6 XGBoost/LightGBM + Optuna (`src/models/gbm.py`, `tests/test_gbm.py`, `docs/gbm.md`).
- [x] S7 walk-forward engine (`src/evaluation/walk_forward.py`, `tests/test_walk_forward.py`, `docs/walk_forward.md`).
- [x] S0-S7 research-grade hardening pass COMPLETE — see `reports/remediation/S0_S7_HARDENING_AUDIT.md`
      (every MEDIUM/HIGH row and all but two deliberately-standing LOW rows closed, CI-verified) and
      ADR 0013-0018.
- [x] S8 LLM probability benchmark (`src/llm/`, `tests/test_llm.py`, `docs/llm.md`, ADR 0019) —
      OpenAI/Anthropic/Google adapters, strict JSON + retry, historical/prospective track
      separation. Disabled by default; a real run needs the owner's own API keys and costs money.
- [x] S9 calibration/reliability/leaderboard (`src/evaluation/calibration.py`, `reliability.py`,
      `leaderboard.py`, `tests/test_calibration.py`, `test_reliability.py`, `test_leaderboard.py`,
      `test_s9_integration.py`, `docs/calibration.md`, ADR 0020) — temperature scaling (raw vs
      calibrated on disjoint chronological halves), reliability curve, confidence histogram,
      global/league/season/model-class leaderboard with no single winner. Wired into
      `run_baselines.py`'s `report.json`/`report.md`; `predictions.jsonl` unaffected.
- [x] S10 LLM leakage + forward-only guard (`src/llm/audit.py`, `tests/test_llm_audit.py`,
      ADR 0021) — `PROSPECTIVE` track now uses the REAL call time for `PredictionRecord.generated_at`,
      so a post-kickoff live call is structurally refused (`status="post_kickoff_rejected"`), not
      silently accepted. "Prediction lock" is the pre-existing `PredictionLedger`
      (`LedgerConflict` already forbids content mutation, S0-S3) — not duplicated. Audit CLI scans
      persisted artifacts for tampering/corruption (defense in depth; schema validators already
      forbid these for anything this repo's own code writes) and `partition_clean()` excludes +
      counts critical-leakage fixtures from a benchmark result.
- [x] S11 OOF ensemble (`src/evaluation/ensemble.py`, `run_ensemble.py`, `tests/test_ensemble.py`,
      `test_run_ensemble.py`, `docs/ensemble.md`, ADR 0022) — simple mean / validation-weighted /
      logistic stacking / LightGBM stacking over walk-forward's OOF predictions, three-way
      chronological split (fit-ensemble/fit-calibration/report), by-league/by-season breakdown,
      no single winner. LLM base models can join once their predictions exist; not wired by
      default (no real LLM data to test against without spending API budget).
- [x] S12 global fixture ingestion adapter/infra (`src/ingestion/`, `tests/test_ingestion.py`,
      `docs/ingestion.md`, ADR 0023) — `FixtureProvider` protocol (leagues/seasons/fixtures
      required; lineups/injuries/events/statistics/odds left as an interface for S13+), idempotent
      upsert reusing the existing `TeamDirectory` (never auto-registers), coverage matrix +
      freshness monitor, rate-limit token bucket + backoff, cache = raw-response audit store.
      One FREE-tier adapter connected (`football_data_org.py`, `configs/ingestion.yaml`,
      `tests/test_football_data_org.py`, ADR 0023 amendment, 2026-10-01) — football-data.org,
      no cost, owner has no budget currently; CONNECTED with a real free API key (`.env`,
      gitignored, auto-loaded by `cli_utils.load_dotenv`), `enabled: true`, verified end-to-end
      (leagues/seasons/fixtures all returned real data). NOT a resolved commercial answer — terms
      unverified, stays `RESEARCH_ONLY`
      (`docs/data_sources/licensing.md`); a PAID commercial vendor is still the project owner's
      open decision. `src/data/`'s football-data.co.uk pipeline is unchanged and untouched.
      Timestamped odds (S15) and the xG decision remain separately open.
- [x] REAL-AI provider core (ADR 0024, 2026-10-01): official-SDK OpenAI(Responses)/Gemini/Anthropic
      adapters, NO mock path (mocks deleted), budget pre-flight, central pricing, error taxonomy +
      retry. OpenAI + Gemini verified by real calls; Anthropic NOT_CONFIGURED (no key yet).
- [x] Phase G (ADR 0026, 2026-10-02): `src.llm.benchmark` engine (plan/budget/operator gate), coverage report,
      `LLM_REAL` evaluation with raw+calibrated, ensemble `--llm-run`; verified with real calls on 3 providers.
- [x] S13 snapshot engine (ADR 0027, 2026-10-02): `src/snapshot/` stages t-24h/t-90m/t-30m/kickoff, immutable
      content-hashed locked stages, deltas, lineups/injuries UNKNOWN; verified on a real historical fixture; the
      first LIVE stage run is pending real time (next fixtures 2026-10-09/10). Run: `python -m src.snapshot.run`.
- [x] Phase I (ADR 0028, 2026-10-02): capability interfaces + `ProviderMeta`; REAL injuries via FPL (EPL only,
      RESEARCH_ONLY) with cutoff rule + provenance; lineups stay UNKNOWN (no free source). Scheduler: Windows task
      `FootballSnapshotTick` (every 10 min, `scripts/snapshot_tick.ps1`).
- [x] S14 live engine (ADR 0029, 2026-10-02): `src/live/` feeds (OpenLigaDB real, football-data.org), events/state,
      in-play Poisson model, separate `LivePredictionRecord` + store; Windows task `FootballLiveTick` (2 min).
      Real in-play football-data.org payload still unverified (no live match yet). Transfermarkt rejected (ToS).
- [x] S15 odds/EV/CLV paper (ADR 0030, 2026-10-02): `src/odds/` real DraftKings 1X2 via ESPN (RESEARCH_ONLY), exact-only gate,
      paper ledger; Windows task `FootballOddsTick`. ESPN quotes are `approximate` (no provider timestamp) => no edge/EV/CLV;
      exact odds: The Odds API verified 2026-10-08, collected by the cloud workflow odds-exact.yml (PC network blocked).
- [x] S16 MLOps (ADR 0032, 2026-10-06): `src/mlops/` operational log + heartbeats, monitors (freshness, providers, LLM, drift),
      alerts, registry, retrain gate (no automatic retraining), `python -m src.mlops.report`; recent results ingested into the
      live history (`src/ingestion/results.py`). Windows tasks: FootballSnapshotTick/LiveTick/OddsTick/MonitorTick. Findings:
      PC off 2026-10-02..06, dataset stale (primary source unreachable), see ADR 0032.
- [x] S17 dashboard (`src/dashboard/`, `tests/test_dashboard.py`, `docs/dashboard.md`, ADR 0034) — static,
      read-only HTML from real artifacts; unknown stays UNKNOWN; no bet advice.
- [x] S18 API (`src/api/`, `tests/test_api.py`, `docs/api.md`, ADR 0035) — FastAPI /v1, API-key auth (fail closed),
      rate limit, pagination, error schema, OpenAPI; RESEARCH_ONLY.
- [x] Phase O commercial readiness (ADR 0036): provider_evaluation.md, product.md, Dockerfile, backup script, deployment.md.
      Every source still RESEARCH_ONLY; commercial launch gated on written provider terms (owner).
- [x] Follow-ups 2026-10-08: ADR 0037 re-pin, 0038 openfootball (public domain results), 0039 bet suggestions, slim API image, `docs/research_report.md`.
- [x] Match intelligence (ADR 0041): `src/markets/` scores/goals/corners/cards, `python -m src.markets.run`, API + dashboard.
- [x] ADR 0044: product path history/fixtures from openfootball (public domain); football-data.co.uk research only.
- [x] ADR 0045 private data repo (Ma-analizi2, deploy key); ADR 0046 Bundesliga/Serie A/Ligue 1 + Süper Lig (source-limited).
- [ ] S19 startup MVP (hosting, odds for the new leagues, Anthropic remain).
Data source is RESEARCH_ONLY (docs/data_sources/licensing.md): resolve licensing before any commercial use.
