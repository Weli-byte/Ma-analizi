# ADR 0093 — Phase 3, Sprint 15: multi-stage forecast records (2026-10-31)

Plan: `Futbol_Tahmin_Platformu_Faz3_Sprint_13_18_ULTRA_PRO_MAX.pdf`, sprint 15. Release decision: **CONDITIONAL_GO** — every fixture now gets four immutable stage records built in the cloud job; the stage comparison has too few weeks for a verdict, and the late stages often hold a forecast that is a few hours old because forecasts are published by the four-hourly job.

## Existing pieces (reused, not rebuilt)
The shadow layer already stores staged, immutable records of the league-programme models (ADR 0076); the S13 snapshot engine (ADR 0027) covers the research pipeline. The PRODUCT forecasts were not staged: a new forecast file appeared every four hours. The cloud job's own schedule (`markets-cloud`, concurrency group, no parallel run) is the scheduler; no new job mechanism was added.

## Decision
- `src/stages/freeze.py` (run in `markets-cloud` after the forecasts and the track record): for t-24h, t-90m, t-30m and kickoff the record is the newest published forecast whose information cutoff is at or before the stage cutoff — independent of when the freezer runs (`frozen_late_minutes` says how late it ran, it never picks a later forecast). It stores the source forecast's hash, its staleness, the probabilities (1X2 headline and model, over 2.5, BTTS), the odds observation time (an observation after the cutoff makes the record `ODDS_AFTER_CUTOFF`), the line-up status at the cutoff (`CONFIRMED` / `LINEUP_UNCONFIRMED`, information only), `CALIBRATION_FALLBACK: identity` (no stage-specific calibrator is validated) and the coherence check of the source. No forecast at a cutoff: `NO_FORECAST`.
- Idempotency key `fixture | markets | stage | model version | scheduled kick-off`; exclusive file creation; a second run compares and reports UNCHANGED; a different content is logged in `_conflicts.jsonl` and never overwritten. A moved kick-off gets new records; the old schedule's records stay. A failure goes to `_failed.jsonl`; `--replay` retries it, marks the record `replayed`, and the selection is the same as an on-time run.
- Read side: `GET /v1/fixtures/{id}/stages` (API key), an "Aşamalara göre tahmin" table on the match page (forecast, age, line-up status per stage, "not betting advice"), the site bundle carries the records of recent fixtures; `python -m src.stages.evaluate` compares stages on the same fixtures.
- Sprint 14 follow-up: the drift and data-quality report now runs in every `markets-cloud` run and sends its alarms to Telegram (critical once a day, warning once an ISO week, `--notify-state`), with the quotes read from `artifacts/odds_remote`.

## Evidence (real data, 2026-10-31; `docs/phase3/sprint15_stage_report.md`)
- 985 stage records from the published forecasts of 268 fixtures; a second run wrote 0 and found 985 unchanged. t-24h: 31 fixtures had no forecast yet that early (NO_FORECAST, not filled).
- Paired set: 204 settled fixtures with an OK record at all four stages. 1X2 log loss falls from 0.8698 (t-24h) to 0.8631 (kickoff); over/under and BTTS barely move. Against kickoff: t-24h +0.0067, t-90m +0.0026, t-30m +0.0019 — four ISO weeks, fewer than the 8 needed, so the verdict is TOO_FEW_WEEKLY_BLOCKS.
- Mean staleness at the late stages ≈ 3.5–4 h (the forecast cadence); a confirmed XI was known at t-30m for 6 % of the paired fixtures and never at t-90m (ESPN shows the XI 31–57 min before kick-off, ADR 0086).
- Tests (`tests/test_stages.py`, real forecasts with TEST_ONLY edits): cutoff selection, idempotent rerun, conflict, odds after the cutoff, moved kick-off, late line-up, dead letter and replay, same-fixture comparison, API, page, cloud wiring and bundle.

## Not done
No dedicated forecast run at t-90m / t-30m (the stage holds the newest four-hourly forecast; a faster cadence would mean refitting the market model in the ten-minute loop); no stage-specific model or calibrator; no 429 retry here (the freezer calls no provider: the providers' own limits are handled by their collectors).
