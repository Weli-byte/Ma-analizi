# Product positioning and feature matrix (S19 draft)

Football Forecasting & Intelligence Platform: calibrated probabilities with full provenance and an honest
model comparison. No guaranteed-win or betting-advice language anywhere; value analytics are paper research.

| Capability | Free | Pro | API | B2B |
|---|---|---|---|---|
| Upcoming fixtures + basic 1X2 forecast | yes | yes | yes | yes |
| Match intelligence (injuries/lineups status with OBSERVED/UNKNOWN labels) | yes | yes | yes | yes |
| Model comparison + calibration + reliability | - | yes | yes | yes |
| Forecast history + stage updates (t-24h .. kickoff) | - | yes | yes | yes |
| Live in-play forecast | - | yes | yes | yes |
| Alerts | - | yes | - | yes |
| Paper value research (exact odds only) | - | yes | yes | yes |
| REST access, API keys, rate limits | - | - | yes | yes |
| Custom reports (media/scout/club) | - | - | - | yes |

Gate: none of this ships commercially until `docs/data_sources/provider_evaluation.md` items 1-4 are resolved.

Web gates (ADR 0062, package B): the match tabs `Marketler`, `Hakem` and `Oyuncular` need Pro when `FORECAST_PLANS_ENFORCED` is set; off by default. Tips and the track record stay free.
