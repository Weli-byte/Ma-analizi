# 05 — Evaluation

Split `split-8ed65cab17a3` (ADR 0012): train 2019-20..2021-22 (2280 rows) · validation 2022-23..2023-24 (1520) ·
final test 2024-25..2025-26 (1520, **never loaded**) · 2026-27 partial, in no split.

Baselines on validation (strict run, common set n=1520; 95% bootstrap CI, 1000 resamples, seed 20260925):

| model | class | Log Loss | Brier | RPS | ECE | Accuracy |
|---|---|---|---|---|---|---|
| always_home | baseline | 18.451 [17.63, 19.36] | 1.068 | 0.414 | 0.534 | 0.466 |
| historical_prior | baseline | 1.0615 [1.050, 1.073] | 0.641 | 0.229 | 0.038 | 0.466 |
| recent_form_naive | baseline | 1.0496 [1.033, 1.067] | 0.632 | 0.225 | 0.035 | 0.480 |
| market_implied | reference_market_baseline | 0.9476 [0.924, 0.972] | 0.561 | 0.191 | 0.035 | 0.563 |

Interpretation: `always_home` log loss is a clipping artefact. `recent_form_naive` vs `historical_prior` intervals
overlap on Log Loss (1.033–1.067 vs 1.050–1.073): **no superiority is established**. `market_implied` uses closing
odds with unknown timestamps: a reference bar, not a competitor or signal. No overall winner is declared.

Rules verified by tests: ties → fractional accuracy credit; ECE with configured bins; bootstrap deterministic per seed;
final-test lock (all ordinary modes rejected, FINAL only via `run_final_evaluation`, one run per configuration,
read-only artifact, access log); split manifest; walk-forward folds honour `split_strategy`.
S3 regression check: 2022-23 numbers equal the original S3 report (prior 1.0576, market 0.9699).
