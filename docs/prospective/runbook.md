# Runbook and service objectives of the live record (ADR 0090)

Everything below names a mechanism that exists and has run; nothing is aspirational. Times are UTC.

## Objectives (SLO) and how each is measured
| Objective | Target | Measured by | Alert |
|---|---|---|---|
| Forecasts published before kick-off | every scored forecast | `python -m src.prospective.report` (timing audit) | report line; a post-kick-off artifact is never scored |
| Stored forecasts unchanged | 0 hash mismatches | prospective audit (content hash recomputed) | report line (manual run) |
| Probabilities valid | 0 invariant violations | `python -m src.goalmkts.report` consistency audit (ADR 0088) | report line |
| Site up | HTTP 200 / 303 | `site-check.yml` | failed workflow |
| Data refresh | markets job every ~4 h; shadow loop every 10 min | `markets-cloud.yml`, `shadow-cloud.yml` (self-restarting) | watchdog: `shadow_gate.json` older than 6 h → Telegram (ADR 0077) |
| Settlement | a forecast fixture settled within 36 h | prospective audit `unsettled` list | report line; ESPN settlement adds missing domestic results (ADR 0090) |
| Shadow layer health | no critical alert | `rollout/monitoring.py` keys `records_refused`, `zero_coverage`, `provider_unavailable`, `schema_mismatch`, `probabilities_malformed`, `fixture_identity_conflict`, `shadow_record_visible`, `lineups_not_unknown` (critical) and `scheduler_stale`, `provider_error`, `fallback_rate`, `slow_inference`, `drift_critical`, `outcome_lag`, `model_missing` (warning) | Telegram, once per key and day (`notify_once`) |
| Provider quota | never call a provider without credits | odds reserve (25 credits), quota pause (6 h), one call per window (ADR 0083); `INJURIES_COLLECT=off` (ADR 0084) | log lines `SKIPPED` / `PAUSED` |
| Secrets | none in history | `secret-scan.yml` (gitleaks pinned + canary) | failed workflow |

## When an alert fires
1. **Watchdog: shadow stamp stale** — open the last `shadow-cloud` run; if it stopped, run the workflow by hand (`gh workflow run shadow-cloud.yml`); the hourly cron restarts it anyway. `SHADOW_LOOP=off` stops the loop on purpose.
2. **`records_refused` (critical)** — read the run's `skipped` reasons. A stage already recorded with a longer history is no longer a refusal (ADR 0082); any other refusal is a real validation failure: do not delete records, fix the cause.
3. **Provider errors / quota** — check the provider's dashboard; never switch TLS off, never paste a key in a chat; rotate a leaked key at the provider and in the repository secrets.
4. **Site down or a wrong number on the site** — roll back the deployment: `gh workflow run vercel-rollback-drill.yml -f mode=execute` (rolls back to the previous production deployment and restores; ADR 0080), or redeploy by running `markets-cloud`. The experimental card is switched off with `MARKETS_EXPERIMENTAL=0`.
5. **A model must stop serving** — the league-model kill switch is ON and the canary fraction 0 (ADR 0068); nothing but the global model is served, so there is no canary to roll back.

## Drills that have run
- Rollback drill: dry-run, preview and execute, 2026-10-25, site 200 at every step (ADR 0080).
- Notification test: `notify-test.yml`, test message received 2026-10-25 (ADR 0077).
- Secret-scan canary: every run of `secret-scan.yml` proves the scanner catches a planted secret.

## Retention and privacy
Forecast artifacts, line-ups and odds stay in the private data repository; the public repository holds only code, configuration, documentation and small real excerpts used by tests. No user data is in the record. Keys live only in `.env` (untracked) and the repository secrets; logs and reports print names, never values.
