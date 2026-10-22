# Sprint 6, task 1: production integration inventory (read from the repository, 2026-10-22)

Nothing was rewritten. The league-model layer is additive, off the user path and behind flags that default to closed.

| Area | What exists (path) | How the shadow layer relates |
|---|---|---|
| API | FastAPI `/v1` (`src/api/app.py`, `service.py`): fixtures, predictions, intelligence, tips, track record, models, benchmarks, teams, value research; API-key auth, rate limit; reads dashboard view-model artifacts | untouched. No `/v1` endpoint returns a shadow or league-model prediction. A future read-only endpoint is listed as not done. |
| Web UI | `src/web/` (routes, pages, auth, store, plans), Turkish/English, Vercel snapshot bundle, Free/Pro gates off | untouched. The shadow dashboard is a separate static file. |
| Background jobs / scheduler | Windows tasks FootballSnapshotTick / LiveTick / OddsTick / MonitorTick (`scripts/*_tick.ps1`); GitHub Actions `markets-cloud.yml` (cron 4 h), `odds-exact.yml` (self-restarting loop) | a `FootballShadowTick` script and installer were added (`scripts/shadow_tick.ps1`, `install_shadow_task.ps1`); NOT registered. |
| Data store | file artifacts under `artifacts/` (JSONL, JSON), SQLite `users.sqlite`, optional Turso for users, private data repo for cached source snapshots | new append-only files under `artifacts/league_models/rollout/` only. |
| Cache | raw-response audit store of the ingestion layer, openfootball cache under `artifacts/markets/_cache` | read-only use (history for fitting). |
| Prediction schema | `PredictionRecord` + ledger (`src/schemas`), S13 snapshot stages (`src/snapshot`), live predictions store | NOT changed. The prospective record is a separate, additive schema (`lm-prospective-v1`). |
| Authentication | API-key (fail closed), session cookies, Google sign-in | not involved. |
| CI/CD | `ci.yml` (tests 3.12 + 3.14, ruff, lock check, real-data sanity), `league-benchmark.yml` (real-data S2 to S5 dev reproduction on Linux), `relock.yml`, Docker image (`Dockerfile`), Vercel bundle | tests of the new layer run in `ci.yml`; the holdout stage is never run in CI. |
| Observability | `src/mlops` operational log, heartbeats, provider-call log, alerts, registry, `python -m src.mlops.report` | the shadow layer has its own alert log and reuses `mlops.monitor.psi`; it is not yet merged into `src.mlops.report` (listed as not done). |
| Deployment config | `docs/deployment.md`, `docs/hosting.md`, `docs/always_on.md` | `docs/league_models/s6_deployment_manifest.yaml` describes the flags and paths. |
| Named plan components | LeagueModelRegistry, EligibilityGate, PredictionRouter, FeatureSnapshotStore, EvaluationStore | `rollout/control.py` (registry, eligibility, canary), `prediction_router.py` (Sprint 5), `rollout/shadow.py` (snapshot hash), `rollout/records.py` + `evaluation.py` (stores). |

Response-schema compatibility: no existing response changes. Migration plan: records carry a schema version; a new version writes new records and never edits old ones; rollback is the kill switch.
