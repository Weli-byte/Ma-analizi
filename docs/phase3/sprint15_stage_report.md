# Sprint 15 - stage records and the paired stage comparison (ADR 0093)

As of 2026-10-31 (stage records frozen from the published forecasts of the markets-data branch, 2026-10-31) · `python -m src.stages.evaluate`.

Fixtures with stage records: 268; settled fixtures with an OK record at every stage (the paired set): **204**.

| stage | records | OK |
|---|---|---|
| t-24h | 268 | 237 |
| t-90m | 243 | 243 |
| t-30m | 237 | 237 |
| kickoff | 237 | 237 |

| stage | n | 1X2 log loss | 1X2 Brier | 1X2 accuracy % | O/U 2.5 log loss | BTTS log loss | mean staleness (min) | XI confirmed % |
|---|---|---|---|---|---|---|---|---|
| t-24h | 204 | 0.8698 | 0.5048 | 64.2 | 0.6609 | 0.6842 | 170 | 0 |
| t-90m | 204 | 0.8657 | 0.5017 | 65.2 | 0.6611 | 0.6845 | 228 | 0 |
| t-30m | 204 | 0.8650 | 0.5015 | 65.2 | 0.6614 | 0.6845 | 216 | 6 |
| kickoff | 204 | 0.8631 | 0.5000 | 65.2 | 0.6614 | 0.6845 | 211 | 6 |

| stage minus kickoff (1X2 log loss) | delta | 95 % interval | weeks | verdict |
|---|---|---|---|---|
| t-24h | +0.0067 | +0.0006 to +0.0138 | 4 | TOO_FEW_WEEKLY_BLOCKS |
| t-90m | +0.0026 | +0.0002 to +0.0043 | 4 | TOO_FEW_WEEKLY_BLOCKS |
| t-30m | +0.0019 | +0.0000 to +0.0033 | 4 | TOO_FEW_WEEKLY_BLOCKS |

Staleness = minutes between the stage cutoff and the forecast that existed then: the forecasts are published by the four-hourly job, so a late stage often still holds an earlier forecast.
