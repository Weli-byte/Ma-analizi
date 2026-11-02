# ADR 0094 — Hourly forecasts for fresher stage records, and the deploy record (2026-11-01)

Closes two free gaps of Phase 3 sprints 13 and 15.

## 1. Fresher late stages (sprint 15)
The stage records of ADR 0093 showed that the t-90m / t-30m stages held forecasts 3.5–4 hours old, because `markets-cloud` ran every four hours. The job now runs **hourly** (`23 * * * *`).
To stay inside the free budgets, every hour that is not a multiple of four (UTC) is a **light run** (`MARKETS_LIGHT_RUN=1`): no Highlightly statistics (their daily request budget is spent by
the four-hourly runs), no odds-provider score calls (they cost credits), and the ESPN settlement scans 3 days back instead of 21. A manual dispatch is always a full run. Everything else —
forecasts, track record, stage freezing, drift report, deploy — runs every hour. Expected effect: the stage records hold forecasts at most about an hour old. Vercel Hobby allows far more than
24 deployments a day; the public repository's Actions minutes are free.

## 2. Which code is deployed (sprint 13)
The deploy step writes `deploy_info.json` (commit SHA, run id, build time) into the bundle; `GET /v1/health` returns it as `deployed`. The deployed commit can now be read from the public
site itself instead of from the deploy job's record.

## Amendment (2026-11-02): the odds loop keeps markets-cloud hourly
- Finding: GitHub ran the hourly `markets-cloud` schedule only 4-5 times a day (2026-11-01..02: 06:24, 15:19, 19:49, 23:42, 06:29 UTC), so the live
  site was up to ~7 hours old and the t-90m / t-30m stage records this ADR wanted stayed sparse.
- The self-restarting `odds-exact` loop (ADR 0049, already `actions: write`) now ends each ~50-minute run by dispatching `markets-cloud` with
  `kind=auto` when its last run is completed and started at least 45 minutes ago. `continue-on-error` keeps a failed kick from stopping the odds loop;
  the repository variable `MARKETS_KICK=off` switches the kick off.
- `markets-cloud` gains the dispatch input `kind`: `full` (default, a manual run: every call, as before) or `auto` (the hour rule of this ADR: full
  only when the UTC hour is divisible by 4), so the kicks never spend the budgeted calls more often than the schedule did.
