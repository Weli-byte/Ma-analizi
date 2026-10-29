# Phase 2 (sprints 7-12) readiness matrix (ADR 0090)

Plan: `Futbol_Tahmin_Optimizasyonu_Sprint_7_12_ULTRA_PRO_MAX.pdf`. Decision words as defined by the plan: PASS = code, tests, real data, licence and independent evaluation all present; CONDITIONAL = infrastructure done but data access, sample or permission missing; FAIL = leakage, security, licence or validation problem. No sprint is FAIL; none is PASS.

| Sprint | Built and verified on real data | Result | What is missing (why) |
|---|---|---|---|
| 7 xG/xGA (ADR 0085) | as-of xG observations from the Highlightly store (335 of 336 fetched matches carry xG), xG features behind an OFF flag, coverage and leakage report, baseline re-measured (first 138 matches reproduce the plan) | CONDITIONAL | licence: free plan is research only, product use needs Highlightly PRO; no publication time, history starts at our first fetch (2026-10-13), so no backtest; ablation gate 1,500 matches (49 today) |
| 8 line-ups (ADR 0086) | as-of ESPN line-ups, immutable t-24h/t-90m/t-30m snapshots, line-up features behind an OFF flag, leakage and idempotency checks; history no longer deleted | CONDITIONAL | ESPN shows the XI only 31-57 min before kick-off (t-90m always UNKNOWN); no licensed injury feed (API-Football suspended), no player minutes / xA for player impact, no player directory; ablation gate 800 fixtures (0 today) |
| 9 odds-aware ensemble (ADR 0087) | normalization / power / Shin vig removal, as-of bookmaker consensus, chronological market-vs-model comparison on 131 rows | CONDITIONAL | odds quota exhausted (both free keys), no historical odds on the free plan, over/under and BTTS odds not collected; gates: 300 report rows to change the market rule, 600 rows for a stacker |
| 10 goal markets (ADR 0088) | 90-minute label contract, consistency of all 7,658 published forecasts (0 violations), live baselines, development-fold evidence | CONDITIONAL | bivariate Poisson / negative binomial not added (needs a CI re-run on development folds); bookmaker-implied over/under BLOCKED; BTTS shows no skill beyond the base rate |
| 11 calibration (ADR 0089) | 7,650 out-of-fold predictions, 1X2 and binary calibrators incl. league-level, fit/select/test by season with a pre-registered gate | CONDITIONAL | no calibrator confirmed on the test season (all KEEP_CHAMPION); the 2023-24 test season was read twice during development (same numbers) |
| 12 prospective validation (ADR 0090) | immutability (0 hash mismatches over 7,658 forecasts), timing (all published before kick-off), settlement audit, ESPN settlement of domestic results, prospective hit tables, runbook | CONDITIONAL | unsettled fixtures remain where no free source has the result; live record covers only a few weeks (intervals per league are noise); canary impossible (no approved model); licences of the data sources still CONDITIONAL / research only |

## Owner decisions that would move a sprint towards PASS (all paid or external)
- Highlightly PRO (sprint 7, xG in product; also corners/cards/shots).
- A licensed line-up / injury / player-minutes feed, or the API-Football account restored and its terms clarified (sprint 8).
- A paid odds plan with history and totals/BTTS markets (sprints 9, 10).
- football-data.org paid plan (licence of the schedule and results; written yes received 2026-10-24 for the paid plan).
- Time: every gate above is a sample-size gate; the live record grows by about ten matches a day while the cloud jobs run.
