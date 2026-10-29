# Sprint 10 - over/under 2.5 and both teams to score (ADR 0088)

As of 2026-10-29 (live record generated 2026-10-29T06:04Z) · `python -m src.goalmkts.report`.

## Label contract (read from the code, not assumed)
- Goals are the **90-minute** score: football-data.org `regularTime` when a match went beyond 90 minutes (`live_results.score_90`), openfootball cup lines use the 90-minute figure and an extra-time result without it is unusable, the odds-provider scores and the ESPN backfill are accepted only in the league phase / as 90-minute scores. Awarded and abandoned matches are not in the record.
- Over 2.5 = home + away goals > 2; BTTS yes = both sides scored. Exact score is the single most probable cell of the score grid (not optimised).

## Probability consistency of the published forecasts
- Artifacts checked: **7658** (unreadable 0; with a stored total-goals distribution 6771); artifacts with at least one violated invariant: **0**.
- Invariants: probabilities in [0, 1]; 1X2 (model and headline) sums to 1; over + under = 1 on every line; BTTS yes + no = 1; double chance and draw-no-bet follow from the headline; over lines fall as the line rises; scorelines sorted and summing to at most 1; the total-goals distribution sums to 1 and gives the stored over 2.5.

## Live record versus trivial baselines
- Settled matches 202, no forecast before kick-off 0, **used 202**, of which with the experimental forecast 48.
- Leagues: SERIEA 38, UCL 31, EPL 30, BUNDESLIGA 27, LIGUE1 25, LALIGA 24, UEL 11, SUPERLIG 9, UECL 7.

### over/under 2.5

- Realised rate in the live record: **64.9 %** (95 % interval 58.0-71.1 %); pooled rate of the development seasons: 53.5 %. The live rate is **outside** the historical rate's neighbourhood: this early-season sample is unusually high-scoring, so "always over" / "always yes" looks strong here and is not a stable benchmark.
- Accuracy of the trivial strategies on the live record: always over 64.9 %, always under 35.1 %.

| candidate | n | log loss | Brier | accuracy % | mean prediction % | observed % |
|---|---|---|---|---|---|---|
| production forecast | 202 | 0.6532 | 0.2310 | 62.9 | 55.3 | 64.9 |
| prevalence, historical constant | 202 | 0.6749 | 0.2409 | 64.9 | 53.5 | 64.9 |
| prevalence, live expanding (as of each match) | 202 | 0.6606 | 0.2331 | 64.9 | 69.1 | 64.9 |

| paired log-loss difference | delta | 95 % interval | weeks | verdict |
|---|---|---|---|---|
| production minus prevalence (historical) | -0.0218 | -0.0471 to -0.0055 | 4 | TOO_FEW_WEEKLY_BLOCKS |
| production minus prevalence (live expanding) | -0.0075 | -0.0612 to +0.0286 | 4 | TOO_FEW_WEEKLY_BLOCKS |

Rows that carry the experimental forecast (48):

| candidate | n | log loss | Brier | accuracy % | mean prediction % | observed % |
|---|---|---|---|---|---|---|
| production forecast | 48 | 0.6546 | 0.2313 | 66.7 | 53.3 | 50.0 |
| experimental (hierarchical) | 48 | 0.6743 | 0.2408 | 52.1 | 53.4 | 50.0 |
| prevalence, historical constant | 48 | 0.6956 | 0.2512 | 50.0 | 53.5 | 50.0 |

| paired log-loss difference | delta | 95 % interval | weeks | verdict |
|---|---|---|---|---|
| experimental minus production | +0.0197 | -0.0024 to +0.0242 | 2 | TOO_FEW_WEEKLY_BLOCKS |
| experimental minus prevalence (historical) | -0.0212 | -0.0250 to -0.0025 | 2 | TOO_FEW_WEEKLY_BLOCKS |
| production minus prevalence (historical) | -0.0410 | -0.0491 to -0.0002 | 2 | TOO_FEW_WEEKLY_BLOCKS |

### both teams to score

- Realised rate in the live record: **60.4 %** (95 % interval 53.5-66.9 %); pooled rate of the development seasons: 54.3 %. The live rate is compatible with the historical rate.
- Accuracy of the trivial strategies on the live record: always yes 60.4 %, always no 39.6 %.

| candidate | n | log loss | Brier | accuracy % | mean prediction % | observed % |
|---|---|---|---|---|---|---|
| production forecast | 202 | 0.6845 | 0.2456 | 58.4 | 54.5 | 60.4 |
| prevalence, historical constant | 202 | 0.6790 | 0.2429 | 60.4 | 54.3 | 60.4 |
| prevalence, live expanding (as of each match) | 202 | 0.6835 | 0.2440 | 60.4 | 64.6 | 60.4 |

| paired log-loss difference | delta | 95 % interval | weeks | verdict |
|---|---|---|---|---|
| production minus prevalence (historical) | +0.0055 | -0.0008 to +0.0083 | 4 | TOO_FEW_WEEKLY_BLOCKS |
| production minus prevalence (live expanding) | +0.0009 | -0.0065 to +0.0053 | 4 | TOO_FEW_WEEKLY_BLOCKS |

Rows that carry the experimental forecast (48):

| candidate | n | log loss | Brier | accuracy % | mean prediction % | observed % |
|---|---|---|---|---|---|---|
| production forecast | 48 | 0.6843 | 0.2453 | 66.7 | 52.9 | 50.0 |
| experimental (hierarchical) | 48 | 0.6963 | 0.2513 | 56.2 | 55.3 | 50.0 |
| prevalence, historical constant | 48 | 0.6968 | 0.2518 | 50.0 | 54.3 | 50.0 |

| paired log-loss difference | delta | 95 % interval | weeks | verdict |
|---|---|---|---|---|
| experimental minus production | +0.0119 | -0.0004 to +0.0144 | 2 | TOO_FEW_WEEKLY_BLOCKS |
| experimental minus prevalence (historical) | -0.0006 | -0.0163 to +0.0026 | 2 | TOO_FEW_WEEKLY_BLOCKS |
| production minus prevalence (historical) | -0.0125 | -0.0159 to -0.0118 | 2 | TOO_FEW_WEEKLY_BLOCKS |

## Development-fold evidence (league programme, stored, 10,712 matches, no live row, holdout untouched)

Mean log loss per candidate (lower is better); *train rate* is the constant prevalence of the training rows of each fold.

| target | candidate | log loss | Brier | accuracy % | log loss minus train rate |
|---|---|---|---|---|---|
| over_under | train_rate | 0.6915 | 0.2492 | 53.5 | +0.0000 |
| over_under | production_global | 0.6964 | 0.2510 | 54.7 | +0.0050 |
| over_under | B_dc_league | 0.6915 | 0.2487 | 55.5 | +0.0000 |
| over_under | P_dc_sep | 0.6884 | 0.2473 | 55.9 | -0.0030 |
| over_under | H_dc_hier | 0.6842 | 0.2455 | 56.0 | -0.0072 |
| over_under | G_base | 0.6853 | 0.2461 | 55.4 | -0.0062 |
| over_under | G_extra | 0.6829 | 0.2449 | 55.7 | -0.0085 |
| over_under | P_gbm_sep | 0.6888 | 0.2478 | 54.5 | -0.0026 |
| btts | train_rate | 0.6896 | 0.2482 | 54.3 | +0.0000 |
| btts | production_global | 0.6968 | 0.2515 | 52.9 | +0.0072 |
| btts | B_dc_league | 0.6933 | 0.2499 | 54.5 | +0.0036 |
| btts | P_dc_sep | 0.6919 | 0.2492 | 54.4 | +0.0023 |
| btts | H_dc_hier | 0.6896 | 0.2482 | 54.6 | -0.0000 |
| btts | G_base | 0.6880 | 0.2474 | 54.5 | -0.0016 |
| btts | G_extra | 0.6891 | 0.2480 | 53.7 | -0.0006 |
| btts | P_gbm_sep | 0.6950 | 0.2507 | 53.1 | +0.0053 |

Paired block-bootstrap results already stored (all competitions, `ALL`):

| target | candidate | reference | delta | 95 % interval | verdict |
|---|---|---|---|---|---|
| over_under | H_dc_hier | B_dc_league | -0.0073 | -0.0100 to -0.0047 | SUPPORTED |
| over_under | H_dc_hier | production_global | -0.0122 | -0.0158 to -0.0088 | SUPPORTED |
| over_under | G_base | production_global | -0.0112 | -0.0164 to -0.0061 | SUPPORTED |
| over_under | P_gbm_sep | P_dc_sep | +0.0004 | -0.0038 to +0.0049 | INCONCLUSIVE |
| btts | H_dc_hier | B_dc_league | -0.0037 | -0.0053 to -0.0020 | SUPPORTED |
| btts | H_dc_hier | production_global | -0.0072 | -0.0098 to -0.0046 | SUPPORTED |
| btts | G_base | production_global | -0.0088 | -0.0118 to -0.0054 | SUPPORTED |
| btts | P_gbm_sep | P_dc_sep | +0.0030 | -0.0004 to +0.0067 | INCONCLUSIVE |

No paired interval against the *train rate* is stored (the row-level losses are not kept); only the means above, so a gap to the prevalence baseline is a point estimate here.

## Reading
- Accuracy at a 0.5 threshold is the plan's secondary metric; log loss and Brier decide. Mean prediction versus observed rate shows whether a forecast is shifted (calibration in the large) on this sample.
- The live record spans only a few ISO weeks. With fewer than 8 weekly blocks the paired intervals are not usable and the verdict reads `TOO_FEW_WEEKLY_BLOCKS`; the point estimates are shown for orientation only.
- Bookmaker-implied over/under: **not available** (the collector stores 1X2 only), so that baseline is `BLOCKED`, not approximated.
- Bivariate Poisson, negative binomial and direct gradient-boosting classifiers: the league programme already tested a learned-variance hierarchical goal model, a wider variance grid (Sprint 4d, no gain) and direct classifiers (`P_gbm_sep`, `G_base`, `G_extra`) on the development folds; a bivariate Poisson or negative binomial candidate was **not added** in this sprint (it needs a row-level re-run of the nested harness on the CI runner).
- Nothing here changes a served number: the experimental card keeps its own pre-registered live gate (ADR 0073).

