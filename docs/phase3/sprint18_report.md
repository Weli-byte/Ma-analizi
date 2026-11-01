# Phase 3 sprint 18 — implementation report (ADR 0097)

## 1. Discovery and gaps
See ADR 0097 "Discovery". Every sprint 13-17 evidence file exists (release gate check `evidence`: PASS).

## 2. Changes
| File | Change |
|---|---|
| `src/release/breaker.py`, `src/mlops/oplog.py`, `src/odds/theoddsapi.py`, `src/ingestion/highlightly.py` | circuit breaker, bounded retry, quota-safe wrapper |
| `src/release/slo.py`, `src/web/pages.py` (`status_page`), `src/web/routes.py` (`/durum`), `src/web/i18n.py` | SLOs, error budget, status page |
| `src/release/canary.py`, `configs/release/canary.yaml` | champion / challenger binding, canary policy, automatic rollback |
| `src/release/gate.py` | release gate, bundle secret check |
| `src/release/drills.py`, `docs/phase3/s18_drills.json` | restore, migration rollback, model rollback, deployment drill records |
| `src/markets/run.py`, `src/api/service.py`, `src/stages/freeze.py`, `src/ingestion/provider.py` | `kickoff_time_known` for date-only fixtures |
| `.github/workflows/markets-cloud.yml` | breaker on, SLO step, bundle check before deploy |
| six workflows | `permissions: contents: read` |
| `docs/phase3/sprint18_operations.md` | handbook: SLOs, failures, runbooks, escalation, postmortem, security review, DR, licences, checklist, approval form |

Migration: none for stored data (new optional fields only). Rollback: revert the commit; `PROVIDER_BREAKER` unset turns the breaker off; `configs/release/canary.yaml` is already the safe state.

## 3. Commands and results
```
python -m src.release.drills migration          -> PASS (columns + plan, then without; 5 rows, 1 favourite kept; re-apply idempotent; SQLite 3.50.4)
python -m src.release.drills model-rollback     -> PASS (ROLLBACK, challenger share 0.056 -> 0.0, real config unchanged)
python -m src.release.drills restore --markets-root <markets-data 2026-11-01 08:20>  -> PASS (10,194 files, 9,018 forecasts, 0 mismatches, 27.7 MB, 189 s)
vercel-rollback-drill dry-run (run 41295811913)  -> success: production dpl_7m9J..., previous dpl_Ebao..., site HTTP 200
vercel-rollback-drill preview (run 41295820893)  -> success: preview deployment READY (staging)
python -m src.release.slo --markets-root <markets-data> ...   -> docs/phase3/s18_slo_first_measurement.json
python -m src.release.gate                      -> research_release CONDITIONAL_GO (ci NOT_CHECKED locally), commercial_launch NO_GO
```
Test manifest: `tests/test_release.py` — unit (breaker classification on offline stubs, canary state machine, SLO evaluation and error budget), integration (SLOs, restore drill and stage
freezer on real excerpt forecasts, status page through the app, gate on the real repository), real-provider tests: none new (the live-site probe and the Vercel drills run in the cloud and are
recorded above; the existing `pytest -m live` tests are unchanged).

## 4. Data evidence (real, 2026-11-01)
| Indicator | Value | Target | Status |
|---|---|---|---|
| forecast freshness | 1.56 h | ≤ 2 | MET |
| fresh forecast share | 100 % of 14 | ≥ 95 % | MET |
| stage completion | 100 % of 268 due | ≥ 95 % | MET |
| settlement | 80.6 % of 36 | ≥ 95 % | BREACHED (5 date-only LaLiga fixtures, 2 Ligue 1 matches without a result yet) |
| calibration ECE 1X2 | 0.0985 (n 259) | ≤ max(0.05, noise floor 0.0858) | BREACHED |
| odds freshness | not measured locally | ≤ 24 h | stale in the cloud since 2026-10-19 (quota) |
| run availability | measured from the first cloud run on | ≥ 95 % | — |

## 5. Licence, cost, quota
No new dependency, no paid call. The live-site probe is 20 requests per hour to the project's own site. Licences: see the operations handbook §7.

## 6. Open blockers
Odds quota (owner), written licences for a commercial launch (owner), the production rollback `execute` drill (owner decision), the calibration breach (needs weeks of data and the sprint 11 / 17
protocol), staging parity (a preview deployment only).

## 7. Release decision
Research product: **CONDITIONAL_GO** — every gate check passes except the CI result, which the workflow supplies, and two SLOs are breached and reported. Commercial launch: **NO_GO**.
