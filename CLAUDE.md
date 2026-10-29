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
- xG: a real source exists (Highlightly, research only, ADR 0085) but no model reads it; `src/xg/` features are behind the OFF flag `XG_FEATURES_ENABLED`. Never impute or fake data; xG is never derived from shots or goals.
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
python -m src.league_models.audit --as-of 2026-10-20T00:00:00Z --write   # league programme S1: licence/coverage/identity/as-of audit (ADR 0063)
python -m src.evaluation.run_ensemble [--llm-run DIR]   # S11 OOF ensemble; run walk_forward first; --llm-run adds REAL LLM models
python -m src.league_models.benchmark --as-of 2026-10-20T00:00:00Z [--dry-run|--verify DIR|--reproduce DIR]   # league programme S2: global reference walk-forward (ADR 0064)
python -m src.league_models.stat_benchmark --as-of 2026-10-20T00:00:00Z [--dry-run|--verify DIR|--reproduce DIR|--compare-committed docs/league_models]   # league programme S3: league statistical candidates (ADR 0065)
python -m src.league_models.ml_benchmark --as-of 2026-10-20T00:00:00Z [--dry-run|--verify DIR|--reproduce DIR|--compare-committed docs/league_models]   # league programme S4: league-aware GBM adapters + tuned control (ADR 0066)
python -m src.league_models.s5_benchmark --as-of 2026-10-20T00:00:00Z --stage dev [--dry-run|--verify DIR|--compare-committed docs/league_models]   # league programme S5 dev stage (calibration, screen, pre-registration); --stage holdout is ONE-SHOT and already consumed (ADR 0067)
python -m src.league_models.s4d_experiment --as-of 2026-10-20T00:00:00Z --docs-dir docs/league_models   # S4d: wider grid / random-walk strength vs the Sprint 4c model (ADR 0075)
python -m src.xg.report --root <checkout with artifacts/stats/highlightly> --as-of 2026-10-27T12:00:00Z --out docs/xg/sprint07_xg_coverage.md   # Phase 2 sprint 7: xG coverage + leakage self-check (ADR 0085); python -m src.xg.baseline --root <checkout with artifacts/markets/track_record.json> --out docs/xg/sprint07_baseline.md
python -m src.availability.report --root <checkout with artifacts/lineups> --as-of 2026-10-28T19:00:00Z --out docs/availability/sprint08_lineup_coverage.md   # Phase 2 sprint 8: as-of line-up coverage + leakage/idempotency self-check (ADR 0086)
python -m src.oddsens.report --markets-root <markets-data checkout> --odds-root <odds-data checkout> --out docs/oddsens/sprint09_market_vs_model.md   # Phase 2 sprint 9: market vs model on prospective rows, vig removal methods, as-of bookmaker consensus (ADR 0087)
python -m src.goalmkts.report --markets-root <markets-data checkout> --out docs/goalmkts/sprint10_goal_markets.md   # Phase 2 sprint 10: goal-market label audit, consistency audit of the published forecasts, live baselines (ADR 0088)
python -m src.league_models.s4c_benchmark --as-of 2026-10-20T00:00:00Z --docs-dir docs/league_models [--quick]   # league programme S4c: per-league separate models, learned hierarchy, nested selection, extended features, exact score (ADR 0071); dev folds only
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
      Real in-play football-data.org payload VERIFIED 2026-10-19 on 45 real captures (ADR 0029 amendment; PAUSED never seen). Transfermarkt rejected (ToS).
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
- [x] ADR 0047: exact odds for BL1/SA/FL1/TR (TR once per 12 h).
- [x] ADR 0048: live track record (`python -m src.markets.track`, `/v1/track-record`); corners/cards/shots source decision; docs/always_on.md.
- [x] ADR 0049: self-restarting odds loop (no PC, no external clock).
- [x] ADR 0050: Highlightly corners/cards/shots (verified; published only with `stats_plan: pro`).
- [x] ADR 0051/0052: web UI in `src/web/` - Turkish, mobile-app style (green/white), login first (guest works; Google/Apple/phone need credentials), per-league screens, logos only with FORECAST_UI_LOGOS=1.
- [x] ADR 0053: favourites + user store, search, form/goal charts, `/sunum`.
- [x] ADR 0054: English UI, phone sign-in (Twilio, unverified live), secure cookies, Space bundle + docs/hosting.md.
- [x] ADR 0055: UEFA Champions/Europa/Conference League (`src/ingestion/openfootball_cups.py`, `src/markets/cup_clubs.py`); history CC0, fixtures/odds via The Odds API (sport keys unconfirmed).
- [x] ADR 0056: cups phase 2 - history-only domestic leagues (AT/BE/GR/NL/PT/SCO), UEFA-style priors (`src/markets/coefficients.py`, weights chosen by `eval_priors` on validation seasons), neutral-venue finals, free events check before paid cup odds calls, weekly `probe-cups.yml`, `club_duplicates` (owner-approved merges only).
- [x] ADR 0057: injuries/suspensions from API-Football free plan shown as information only ("Eksikler" card, `src/markets/injuries.py`); never in a probability. Duplicate clubs merged on the owner's approval (`club_duplicates`).
- [x] ADR 0057 (S19 prep): public privacy/terms drafts, `FORECAST_USER_DB`, Vercel snapshot bundle, Apple sign-in code (unverified live, needs `APPLE_CLIENT_ID`).
- [x] ADR 0058: tabbed match page (Misli-style categories), live results store (`src/markets/live_results.py`, football-data.org + Odds API scores), track-record match listing with hit rates.
- [x] ADR 0059: referee + top-scorer tabs (football-data.org free), CL schedule fallback, optional second Odds API key (`THE_ODDS_API_KEY_2`).
- [x] ADR 0060: live on Vercel (https://mac-tahmin-eight.vercel.app, refreshed by the cloud job), app name Welliddia, owner/contact/KVKK sections on the legal drafts.
- [x] Google sign-in verified live 2026-10-15 (ADR 0060): client id set, consent screen published; works for a non-test account. Apple, SMS, Highlightly PRO, Anthropic, persistent user DB, Free/Pro remain.
- [x] ADR 0061: persistent user store on Turso free tier (`src/web/store.py`, `FORECAST_TURSO_URL`/`FORECAST_TURSO_TOKEN`); verified live 2026-10-15 (favourite survived a redeploy); the local live pytest was not run.
- [x] ADR 0062: Free/Pro plan infrastructure (`src/web/plans.py`, `users.plan`), package B (Pro = match tabs Marketler/Hakem/Oyuncular; owner always Pro), gates OFF unless `FORECAST_PLANS_ENFORCED`; no payment, nothing sold; migration verified on live Turso 2026-10-15.
- [x] ADR 0063 league-model programme (6 sprints, plan: `docs/league_models/plan.md`): Sprint 1 built (`src/league_models/`, `python -m src.league_models.audit`, outputs in `docs/league_models/`); Sprint 2 built (ADR 0064, `python -m src.league_models.benchmark`, report `docs/league_models/sprint02_global_benchmark.md`); Sprint 3 built (ADR 0065, `python -m src.league_models.stat_benchmark`, report `docs/league_models/sprint03_league_statistical_models.md`); Sprint 4 built (ADR 0066, `python -m src.league_models.ml_benchmark`, report `docs/league_models/sprint04_ml_adapters.md`); Sprint 5 built (ADR 0067, `python -m src.league_models.s5_benchmark`, report `docs/league_models/sprint05_calibration_and_gate.md`): the frozen holdout is CONSUMED (read once, 2026-10-21), no league model approved, global kept everywhere; Sprint 6 built (ADR 0068, `python -m src.league_models.rollout shadow|reconcile|monitor|scorecard|dashboard|status`, report `docs/league_models/sprint06_shadow_and_rollback.md`): kill switch ON, canary fraction 0, real shadow records collected, canary NO-GO (no approved model, football-data.org CONDITIONAL); the shadow Windows task is written but NOT registered (owner decision). The six-sprint programme is complete. ADR 0071 (2026-10-23, Sprint 4c, report `docs/league_models/sprint04c_per_league_and_modelling_gaps.md`): separately trained per-league models (Poisson/DC and LightGBM, `per_league.py`) tested against pooled ones - no gain; hierarchical model with learned variance components (`HierarchicalDC`) is the best goal model on dev folds; nested walk-forward (`nested.py`), extension features (`extra_features.py`), exact-score audit, expected goals in a score-forecast sidecar (`ScoreForecastStore`), exploratory shadow families `P_dc_sep` / `H_dc_hier` (RAW, never user-visible); nothing approved. ADR 0081 (2026-10-25): starting line-up card, INFORMATION ONLY (`src/markets/lineups.py`, ESPN public summary, collected in the shadow loop, "Kadrolar" in the Eksikler tab; the API-Football account was found SUSPENDED) and `python -m src.readiness [--github]` (SET/NOT_SET names only). ADR 0080 (2026-10-25): neutral-venue matches get the experimental card (`RidgePoissonDC.predict(neutral=True)`), the remaining ESPN clubs are resolved/registered, and a Vercel rollback drill exists (`scripts/vercel_rollback_drill.sh`, manual workflow `vercel-rollback-drill`: dry-run / preview / execute). ADR 0079 (2026-10-25): Sprint 4c/4d outputs are re-run and compared on the Ubuntu runner (`s4c_benchmark` / `s4d_experiment --compare-committed`, steps of `league-benchmark.yml`). ADR 0078 (2026-10-25): secret scanning in CI (`.github/workflows/secret-scan.yml`, gitleaks 8.30.1 pinned + checksum, canary, full history, `--redact`; 296 commits scanned clean). ADR 0077 (2026-10-25): optional Telegram notifications (`src/mlops/notify.py`, secrets `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`, workflow `notify-test`); announces warning/critical shadow alerts and status changes of the card / shadow families once. ADR 0076 (2026-10-25): the shadow layer runs in the cloud (`.github/workflows/shadow-cloud.yml`, state on the `shadow-data` branch of the data repo) with a pre-registered live gate (`configs/league_models/shadow_gate.yaml`, `rollout gate`; SHADOW / PROMOTION_CANDIDATE / RETIRE, a recommendation only) shown on the track page; the Windows shadow task is superseded. ADR 0075 (2026-10-24): wider variance grid and random-walk team strength (`dynamic_dc.py`, `s4d_experiment.py`) tested on dev folds - no gain, not adopted. football-data.org answered the licence questions with yes for the PAID plan (29 EUR/month + setup fee; `docs/data_sources/football_data_org_written_confirmation_2026-10-24.md`); no subscription, so `source_licenses.yaml` stays CONDITIONAL. ADR 0074 (2026-10-24): one-off ESPN backfill of the Europa/Conference League 2025-26 results (`python -m src.markets.espn_backfill`, 90-minute scores only, availability = day first seen). ADR 0073 (2026-10-24): pre-registered live gate for that card (`configs/league_models/experimental_gate.yaml`, pinned by hash; `track.experimental_scorecard`; BETA / VERIFIED / WITHDRAWN; section on the track page). ADR 0072 (2026-10-23, owner decision): the hierarchical goal model is shown to users as "Deneysel model (beta)" (`src/markets/experimental.py`, card on the match page, API field `experimental`; over/under and BTTS only, 1X2 stays on the current model; `MARKETS_EXPERIMENTAL=0` switches it off). ADR 0070 (2026-10-23): Europa/Conference League fixtures and results from ESPN's public scoreboard (`src/ingestion/espn_fixtures.py`, `live_results.collect_espn`) because The Odds API no longer lists both. ADR 0069 (2026-10-22): shadow research overlay of football-data.org results (`shadow.research_overlay`), operational public-domain cache (`src/ingestion/openfootball_txt.py`, Süper Lig 2026-27), 44 football-data.org aliases, UEFA stage experiment (`stage_experiment.py`: not adopted); FootballShadowTick registered. Licensed universe = openfootball scores only (football-data.co.uk BLOCKED for league models); every row BACKTEST_LIMITED; global model untouched.
- [x] Phase 2 sprint 7 (ADR 0085, 2026-10-27, plan `Futbol_Tahmin_Optimizasyonu_Sprint_7_12_ULTRA_PRO_MAX.pdf`): real xG observations from the Highlightly store with an as-of contract (`src/xg/`), as-of safe xG/xGA features behind the OFF flag, coverage + leakage report, baseline re-measured (first 138 matches reproduce the plan). Result CONDITIONAL: the data is research-only and starts 2026-10-13, ablation gate (1,500 matches with complete features) not met, no model changed. Sprints 8-12 are not started.
- [x] Phase 2 sprint 8 (ADR 0086, 2026-10-28): as-of starting line-ups from the stored ESPN files (`src/availability/`): observation contract, `t-24h`/`t-90m`/`t-30m` immutable snapshots, line-up features behind the OFF flag `LINEUP_FEATURES_ENABLED`, coverage + leakage report. Real finding: ESPN shows the XI only 31-57 min before kick-off (median 38), so `t-90m` is UNKNOWN for every fixture. Result CONDITIONAL: player impact, injuries, rest have no licensed data and are not built; ablation gate (800 fixtures with complete features) not met, no model changed. The line-up collector no longer deletes history (`KEEP_DAYS` 730; the bundle ships the last three days). Sprints 9-12 are not started.
- [x] Phase 2 sprint 9 (ADR 0087, 2026-10-29): odds-aware ensemble research (`src/oddsens/`): normalization/power/Shin vig removal, as-of bookmaker consensus from the stored exact quotes, chronological select/report comparison of market-only, convex blend and model on 131 real prospective rows. Result CONDITIONAL: no evidence that mixing the model into the market helps (production already uses market weight 1.0); the all-bookmaker power consensus looks better than the production market rule (-0.0066 log loss, 65 report rows) but the pre-registered gate (300 report rows) is not met, production unchanged; no stacker (gate 600 rows); over/under and BTTS odds are not collected. Sprints 10-12 are not started.
- [x] Phase 2 sprint 10 (ADR 0088, 2026-10-29): goal markets (`src/goalmkts/`): 90-minute label contract read from the code, 7,658 published artifacts pass every probability invariant, live baselines (the live 64.9 % over rate is far above the historical 53.5 %, so "always over" is a sample effect); on the development folds the production model is worse than a constant prevalence on over/under and BTTS, BTTS has no measurable skill beyond the base rate. Result CONDITIONAL: nothing promoted, no served number changed; bivariate Poisson / negative binomial not added (needs a CI re-run on dev folds), bookmaker-implied over/under BLOCKED (1X2 quotes only). Sprints 11-12 are not started.
- [ ] S19 startup MVP (deploy to a host, Google client id, SMS account, Free/Pro, Apple sign-in, Highlightly PRO, Anthropic remain).
Data source is RESEARCH_ONLY (docs/data_sources/licensing.md): resolve licensing before any commercial use.
