# ADR 0097 — Phase 3, Sprint 18: production hardening, canary policy, SLOs and incident response (2026-11-01)

Plan: `Futbol_Tahmin_Platformu_Faz3_Sprint_13_18_ULTRA_PRO_MAX.pdf`, sprint 18. Release decision: **CONDITIONAL_GO for the research product, NO_GO for a commercial launch.**

## Discovery (what existed)
League-model canary and kill switch (ADR 0068, `configs/league_models/rollout.yaml`); Vercel rollback drill (ADR 0080); backup script (ADR 0036); token bucket and backoff in `src/ingestion/rate_limit.py`
(not used by the cloud providers); hourly cloud job with deploy record (ADR 0094); drift / freshness alarms with Telegram (ADR 0092); licence manifest (ADR 0063). Missing: a circuit breaker on the
provider calls, service level objectives with an error budget, a champion / challenger binding for the served market model, a release gate, recorded restore / migration / model-rollback drills,
incident runbooks, a status page, token permissions on six workflows, and any handling of date-only kick-off times.

## Decision
- **Circuit breaker** (`src/release/breaker.py`) inside `oplog.logged_urlopen` (every provider except the two quota-metered ones, which use `breaker.observe` without retries): 429 opens for
  max(Retry-After, 60 min), 401 / 403 for 6 h, three consecutive 5xx / network failures for 30 min, one bounded retry for 502 / 503 / 504 / timeouts, 404 is not a provider fault. State persisted
  between hourly runs (`artifacts/markets/_ops/breakers.json`). Enabled in the cloud job only (`PROVIDER_BREAKER=1`).
- **SLOs and error budget** (`src/release/slo.py`): ten indicators with targets fixed before the first measurement (see `docs/phase3/sprint18_operations.md` §1); measured every hourly run
  including 20 real requests to the live site; history in `_ops/slo_history.jsonl`; Telegram on a breach (critical at once, warning weekly); status page **`/durum`** (public). The calibration
  indicator only breaches above the ECE a perfectly calibrated forecaster shows on the same matches (simulated noise floor), so a small sample does not alarm (the lesson of the ADR 0092
  amendment).
- **Canary** (`src/release/canary.py`, `configs/release/canary.yaml`): champion pinned (`markets-1.0.0`), no challenger, stage shadow; stages shadow → 5 → 25 → 50 → 100 % with 14 / 14 / 14 / 14 / 28
  days and 150 / 200 / 300 / 400 / 600 settled matches; deterministic user buckets; stop criteria (critical SLO, leakage, a worse paired interval, ECE worse by > 0.02) roll back automatically;
  advancing is the owner's edit. Only a sprint 17 PROMOTION_CANDIDATE may be bound.
- **Release gate** (`src/release/gate.py`): sprint 13-17 evidence, champion pin, CI result, drills, security (API fails closed, rate limit, cookie flags, workflow permissions, no secret value in
  the bundle, empty gitleaks allowlist, redacted call log), licences (research vs commercial). The deploy step stops if a secret value is found in the site bundle.
- **Date-only kick-offs** (found while measuring the settlement SLO): openfootball fixtures without a time were stored at local midnight (23:00 UTC) and shown with that clock time. New forecasts
  carry `kickoff_time_known`; the page says "saat belli değil", the stage freezer skips such fixtures (`TIME_UNKNOWN`), the SLOs leave them out. Older artifacts are unchanged (immutable).
- **Least privilege**: `permissions: contents: read` on the six workflows that had none.
- **Operations handbook** `docs/phase3/sprint18_operations.md`: SLO table, provider failure policy, seven incident runbooks, escalation, postmortem template, security review, DR, licence gate,
  release checklist and approval form.

## Evidence (`docs/phase3/sprint18_report.md`)
- Drills (`docs/phase3/s18_drills.json`): restore of the published state (10,194 files, 9,018 forecasts, every content hash recomputed, 0 mismatches, 189 s); migration rollback (ADR 0062 column,
  rows kept, idempotent re-apply); model rollback (challenger share 5.6 % → 0 %, real config unchanged); Vercel dry-run (run 41295811913) and preview / staging deploy (run 41295820893) passed.
  The production `execute` rollback was not run (it changes production for minutes; owner decision).
- First SLO measurement (2026-11-01 09:57 UTC, data of 08:20): forecast freshness 1.6 h, fresh share 100 % (14 fixtures), stage completion 100 % (268 due), settlement 80.6 % (36 due; five are
  date-only LaLiga fixtures, two Ligue 1 matches not yet in the result sources), calibration ECE 0.0985 vs noise floor 0.0858 on 259 matches (BREACHED: the live forecasts are underconfident,
  as sprint 16 showed), odds NOT_MEASURED locally; the live-site probe runs in the cloud.
- Release gate: research_release CONDITIONAL_GO (CI result is supplied by the workflow), commercial_launch NO_GO (football-data.org, The Odds API, Highlightly CONDITIONAL; ESPN, API-Football
  RESEARCH_ONLY).
- Tests `tests/test_release.py` (offline HTTP stubs for 429 / 503 / 500 / 401 / 404 marked TEST_ONLY; real excerpt forecasts for the SLOs, restore drill and date-only handling).

## Not done / blockers
No paid provider, so quota exhaustion stays the most likely incident (odds stale since 2026-10-19). Commercial launch blocked on written licences. Staging is a Vercel preview, not a full
parity environment (no separate data branch). The calibration breach is reported, not acted on: a recalibration needs the sprint 11 / 17 protocol and more weeks.
