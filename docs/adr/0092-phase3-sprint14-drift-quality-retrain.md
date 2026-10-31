# ADR 0092 — Phase 3, Sprint 14: drift detection, data quality and the safe retrain candidate (2026-10-31)

Plan: `Futbol_Tahmin_Platformu_Faz3_Sprint_13_18_ULTRA_PRO_MAX.pdf`, sprint 14. Release decision: **CONDITIONAL_GO** — the monitors, thresholds, fault tests and the candidate pipeline are built and run on real data; the live record is too short (two ISO weeks per window) for an input- or performance-drift verdict, and the odds feed is stale.

## Existing monitoring (found, not rebuilt)
`src/mlops/monitor.py` (S16, ADR 0032: freshness, provider health, PSI, feature z-shift, metric drift of the research pipeline), the shadow-layer alerts (ADR 0068 / 0076), the watchdog and Telegram (ADR 0077) and the retrain gate that never retrains (ADR 0032). None of them watched the PRODUCT market model's inputs, outputs and stores together. `src/drift/` adds that and reuses `psi`.

## Decision
- `src/drift/quality.py`: freshness of the live record, forecasts, results per league, line-ups and exact odds; schema of forecasts and results; duplicate results; result-in-the-future and result-and-void; league coverage loss.
- `src/drift/stats.py`: PSI, Jensen-Shannon and KS for continuous values, Jensen-Shannon for categories; INSUFFICIENT_DATA below 100 rows per window.
- `src/drift/thresholds.py`: thresholds from validation history only (the out-of-fold market-model predictions of 2020-21 to 2022-23, 14 pairs of consecutive 60-day windows; threshold = largest value × 1.25), stored in `docs/phase3/s14_thresholds.json` before the live data was looked at. Cooldown: one announcement per key per day.
- `src/drift/monitor.py`: input drift (model home / over 2.5 / BTTS probabilities, league mix, odds and low-confidence shares) on fixed, non-overlapping windows (the first 14 days of the record against the last 14 days); performance drift on settled matches only (first 100 against last 50, weekly-block bootstrap at alpha 0.05 / 3, at least 4 ISO weeks per window). Shown on separate panels.
- `src/drift/retrain.py`: candidate refit with an immutable manifest (cutoff, history data version and size, config hash, commit, seed none, parameter hash) registered once as SHADOW_CANDIDATE; production is never changed, promotion waits for the sprint 17 gate and a human approval.
- Runbook and post-incident template: `docs/phase3/sprint14_runbook.md`.

## Evidence (2026-10-31)
- Data quality (`docs/phase3/sprint14_drift_report.md`): one alarm, `freshness:odds` (newest exact quote 282 h old: both free odds keys below their reserve); live record and forecasts fresh; 0 schema, duplicate or consistency faults; the UCL results gap is shown as a cup gap, not an alarm.
- Input drift: INSUFFICIENT_DATA (166 reference / 71 current forecasts; 100 each needed). Performance drift: INSUFFICIENT_DATA (237 settled matches, but each window spans only 2 ISO weeks). The first draft compared overlapping windows and raised a performance alarm from one week of matches; both errors were found on the real data and fixed before this decision.
- Retrain candidate on the real history as of 2026-10-31: 24,716 matches, data version `dv-of-22151d32198c`, parameter hash `60232c9f…`, reproduced by a second fit, registered as SHADOW_CANDIDATE.
- Sprint 13 follow-up: the 80 forecasts written by the first cloud run after the deploy all carry the lineage block and verify their hash (49 early, 30 t-24h, 1 t-90m).
- Fault tests (`tests/test_drift.py`, synthetic and labelled): stale providers, schema change, duplicate result, future / void result, league coverage loss, shifted predictions, lost league and clearly worse losses each fire the right alarm; no performance alarm without results.

## Not done
The monitors are a report command, not yet a scheduled cloud step with Telegram alerts (that wiring belongs with the sprint 18 SLO work); no incident ticket system (the runbook and the review template stand in).

## Amendment 2026-10-31 — scheduled in the cloud job
The open item above is closed in ADR 0093: `markets-cloud` runs `python -m src.drift.report` after the track record, publishes `drift_report.md` / `drift_status.json`, and sends the alarms to Telegram (critical once a day, warning once an ISO week).
