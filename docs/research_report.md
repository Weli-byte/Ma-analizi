# Research report — Football Forecasting & Intelligence Platform (S19)

Generated 2026-10-08 from real runs on `data_version dv-c89f77323b6e`, `feature_version fv2`, split
`split-d642486e1cbf`. Numbers below are copied from `artifacts/runs/*/report.md` and
`artifacts/walk_forward/*/walk_forward_summary.md`; regenerate them with the commands in "Reproducibility".

## 1. Goal and non-goals
Calibrated 1X2 probabilities with full provenance, an honest comparison of statistical, ML and LLM models, and
research-grade value analytics. Not a guarantee of profit; no model is declared "the winner".

## 2. Data
- Historical: football-data.co.uk CSVs, EPL + La Liga, 2019-20 .. 2026-27 (in progress). Results, closing odds.
  Licence unknown -> `RESEARCH_ONLY`. SHA-256 pinned per file (ADR 0037 documents the one re-pin).
- Recent results: openfootball (public domain, ADR 0038) and football-data.org free tier.
- Odds: The Odds API (exact provider timestamps, measured latency), ESPN/DraftKings (approximate, never used for EV).
- Injuries: FPL (EPL), API-Football free plan (yesterday..tomorrow). Lineups: ESPN when announced, else UNKNOWN.
- xG: not available, never imputed.

## 3. Leakage controls
`information_cutoff` per prediction; only FINISHED matches with `result_available_at_utc <= cutoff` enter any
feature (historical availability is INFERRED and labelled so); a leakage audit runs in the feature builder (300
samples, clean); live/LLM paths refuse post-kickoff calls structurally; LLM snapshots are audited for
result-bearing keys; predictions are immutable and content-hashed (ledger rejects conflicts).

## 4. Split protocol
Chronological only (ADR 0012): fit 2019-20..2021-22, validation 2022-23..2023-24 (1520 matches), final-test
seasons technically locked (`EvaluationContext`; only `run_final_evaluation()` reads them, once). Walk-forward:
3 folds, expanding window.

## 5. Metrics
Log loss, Brier, RPS, ECE primary; accuracy secondary; bootstrap 95% CIs; by league and season. No single winner.

## 6. Results (validation 2022-24, n=1520, common set)
| model | class | log loss | Brier | RPS | ECE | accuracy |
|---|---|---|---|---|---|---|
| historical_prior | baseline | 1.0615 | 0.6414 | 0.2294 | 0.0377 | 0.466 |
| recent_form_naive | baseline | 1.0496 | 0.6321 | 0.2247 | 0.0347 | 0.480 |
| market_implied (closing avg) | reference | **0.9476** | **0.5612** | **0.1907** | 0.0345 | 0.563 |
| elo | statistical | 0.9695 | 0.5757 | 0.1975 | 0.0260 | 0.546 |
| poisson | statistical | 0.9998 | 0.5966 | 0.2081 | 0.0234 | 0.518 |
| dixon_coles | statistical | 1.0013 | 0.5973 | 0.2082 | 0.0233 | 0.516 |
| xgboost | ml | 1.0050 | 0.6000 | 0.2091 | 0.0368 | 0.513 |
| lightgbm | ml | 1.0112 | 0.6043 | 0.2108 | 0.0446 | 0.508 |

**Key finding:** no model beats the market-implied probabilities (log loss 0.9476 vs best model Elo 0.9695,
non-overlapping-ish CIs [0.924, 0.972] vs [0.943, 0.998]). Walk-forward (3 folds, 2280 matches) agrees: Elo
0.9768, Dixon-Coles 0.9884. Consequence: any bet suggestion built on these models should be treated as a
research signal with low confidence (ADR 0039).

LLM benchmark (HISTORICAL track, 20 matches per model, OpenAI gpt-6-luna / Gemini 3.1 flash-lite / Groq
gpt-oss-20b): too small to rank, results may be memorized; see `reports/benchmarks/llm_historical_20matches_20261002/`.
Prospective (forward-only) evaluation needs elapsed time; the first locked stages are due 2026-10-08/10.

## 7. Calibration
Temperature scaling fitted on one chronological half and reported on the other (raw vs calibrated shown side
by side, ADR 0020); reliability curves and confidence histograms in every report. LLM probabilities
are NOT_CALIBRATED unless a calibration run is reported.

## 8. Model cards
| model | what | inputs | known weaknesses |
|---|---|---|---|
| Elo (v1.1.0) | rating system, fitted K/beta/home advantage | results only | no injuries/lineups; slow to react |
| Poisson / Dixon-Coles | goal-rate models | results only | independence assumptions; draws under-modelled |
| XGBoost / LightGBM | gradient boosting on form features, Optuna-tuned | fv2 features | do not beat Elo here; ECE higher |
| LLM (OpenAI/Gemini/Groq) | strict-JSON probabilities from an audited snapshot | snapshot only | memorization risk, run-to-run variance, cost |
| in-play Poisson | live updates from score/minute | prematch rates + state | uncalibrated, minute may be inferred |

## 9. Limitations (read before using any output)
Two leagues; modest sample; closing-odds market is a very strong baseline; LLM evidence is tiny; data sources are
research-licensed (except openfootball); injuries/lineups coverage is partial; the first real prospective
stages have not completed yet; the scheduler depends on a PC being on.

## 10. Reproducibility
```
python -m src.data.pipeline --mode research
python -m src.features.builder --mode research
python -m src.evaluation.run_baselines --mode research
python -m src.evaluation.walk_forward --mode research
python -m pytest            # includes golden, reproducibility, leakage, provenance tests
```
Dependencies are hash-locked (`requirements.lock`, Python 3.12 and 3.14 in CI). The reproducibility test
(`tests/test_golden_and_reproducibility.py, tests/test_real_data_reproducibility.py`) is part of the default suite; CI on GitHub Actions is the only valid
"green" claim.

## 11. Demo flow
upcoming fixture -> `python -m src.dashboard.build` (pre-match probabilities) -> model comparison + calibration
sections -> lineup/injury status (OBSERVED/UNKNOWN) -> live section (`python -m src.live.run`) -> result
ingestion (`python -m src.mlops.report`) -> evaluation/leaderboard -> bet suggestions / paper ledger
(`/v1/value-picks`, `python -m src.odds.run settle`).
