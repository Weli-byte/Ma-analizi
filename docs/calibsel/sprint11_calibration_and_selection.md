# Sprint 11 - calibration, uncertainty and model selection (ADR 0089)

OOF rows: 7650 (digest `4a022b29480da2f4`, history `dv-of-56d45e54d516`, 43 walk-forward fits, refit every 30 days) · git `c73e0d6c7e6c` · `python -m src.calibsel.report`.

## Split (by season, never mixed)

| window | seasons | rows | use |
|---|---|---|---|
| fit | 2020-21, 2021-22 | 4072 | every candidate is fitted |
| select | 2022-23 | 1826 | the candidate is chosen under the gate |
| test | 2023-24 | 1752 | read once, chosen calibrator vs identity only |

Rows are out-of-fold predictions of the production market model (model only, no odds) for the six domestic forecast leagues; the model is refitted every block on matches finished before it. The final-test seasons of `configs/evaluation.yaml` (2024-25, 2025-26) are refused by the code and never downloaded. The 2023-24 season was already used to REPORT the market blend in ADR 0041; it is untouched for calibration decisions, not pristine in general.
League-level calibrators were tried for the leagues with at least 800 fit + select rows: BUNDESLIGA, EPL, LALIGA, LIGUE1, SERIEA (the rest use the global calibrator).

## 1X2

Select window (2022-23), every candidate fitted on 2020-22:

| candidate | log loss | Brier | ECE |
|---|---|---|---|
| identity | 1.0006 | 0.5964 | 0.0256 |
| temperature | 0.9984 | 0.5954 | 0.0136 |
| vector | 0.9993 | 0.5956 | 0.0168 |
| dirichlet | 0.9996 | 0.5958 | 0.0217 |
| league_temperature | 0.9985 | 0.5953 | 0.0146 |

- Gate passed on the select window: **True**; chosen: **temperature** (refitted on 2020-23).
- Test 2023-24, read once: identity 0.9764 | 0.5821 | 0.0150; chosen 0.9781 | 0.5829 | 0.0236 (log loss | Brier | ECE).
- Paired log-loss difference chosen minus identity: +0.0017, interval -0.0015 to +0.0049 at alpha 0.0125 (0.05 divided by the 4 candidates tried), 39 weekly blocks.
- Calibration of the uncalibrated champion on the test season: slope 1.014, intercept -0.043 (1 and 0 = calibrated; home-win probability).
- **Status: KEEP_CHAMPION**.

## over/under 2.5

Select window (2022-23), every candidate fitted on 2020-22:

| candidate | log loss | Brier | ECE |
|---|---|---|---|
| identity | 0.6859 | 0.2462 | 0.0214 |
| temperature | 0.6836 | 0.2453 | 0.0116 |
| platt | 0.6835 | 0.2452 | 0.0152 |
| isotonic | 0.6842 | 0.2455 | 0.0155 |
| league_platt | 0.6854 | 0.2461 | 0.0386 |

- Gate passed on the select window: **True**; chosen: **platt** (refitted on 2020-23).
- Test 2023-24, read once: identity 0.6682 | 0.2377 | 0.0330; chosen 0.6709 | 0.2390 | 0.0352 (log loss | Brier | ECE).
- Paired log-loss difference chosen minus identity: +0.0027, interval -0.0009 to +0.0060 at alpha 0.0125 (0.05 divided by the 4 candidates tried), 39 weekly blocks.
- Calibration of the uncalibrated champion on the test season: slope 0.985, intercept +0.119 (1 and 0 = calibrated).
- **Status: KEEP_CHAMPION**.

## both teams to score

Select window (2022-23), every candidate fitted on 2020-22:

| candidate | log loss | Brier | ECE |
|---|---|---|---|
| identity | 0.6920 | 0.2494 | 0.0387 |
| temperature | 0.6904 | 0.2486 | 0.0303 |
| platt | 0.6887 | 0.2478 | 0.0101 |
| isotonic | 0.6908 | 0.2488 | 0.0145 |
| league_platt | 0.6899 | 0.2483 | 0.0170 |

- Gate passed on the select window: **True**; chosen: **platt** (refitted on 2020-23).
- Test 2023-24, read once: identity 0.6807 | 0.2438 | 0.0444; chosen 0.6787 | 0.2428 | 0.0189 (log loss | Brier | ECE).
- Paired log-loss difference chosen minus identity: -0.0020, interval -0.0058 to +0.0016 at alpha 0.0125 (0.05 divided by the 4 candidates tried), 39 weekly blocks.
- Calibration of the uncalibrated champion on the test season: slope 0.961, intercept +0.175 (1 and 0 = calibrated).
- **Status: KEEP_CHAMPION**.

## Confidence tiers of the champion (1X2, test season 2023-24, model only)

| top-pick probability | matches | share % | mean stated % | hit rate % | 95 % interval |
|---|---|---|---|---|---|
| 0.00-0.40 | 241 | 13.8 | 37.9 | 39.4 | 33.5-45.7 |
| 0.40-0.50 | 583 | 33.3 | 44.8 | 43.4 | 39.4-47.4 |
| 0.50-0.60 | 416 | 23.7 | 54.6 | 52.6 | 47.8-57.4 |
| 0.60-0.70 | 294 | 16.8 | 64.3 | 65.6 | 60.0-70.8 |
| 0.70-1.00 | 218 | 12.4 | 77.1 | 77.5 | 71.5-82.6 |

Abstention is a separate product metric: an accuracy on the picks above a threshold is never mixed with the accuracy on all matches.

## Prospective reliability of the labels users see (live record, 202 settled matches)

| label | market | tips | mean stated % | hit rate % | 95 % interval | verdict |
|---|---|---|---|---|---|---|
| strong lean | ALL | 303 | 76.6 | 78.5 | 73.6-82.8 | CONSISTENT |
| strong lean | btts | 17 | 67.8 | 47.1 | 26.2-69.0 | CONSISTENT |
| strong lean | double_chance | 202 | 79.1 | 79.7 | 73.6-84.7 | CONSISTENT |
| strong lean | goals_2_5 | 36 | 71.3 | 77.8 | 61.9-88.3 | CONSISTENT |
| strong lean | match_result | 48 | 73.3 | 85.4 | 72.8-92.8 | CONSISTENT |
| lean | ALL | 241 | 59.3 | 61.8 | 55.6-67.7 | CONSISTENT |
| lean | btts | 106 | 59.2 | 61.3 | 51.8-70.0 | CONSISTENT |
| lean | goals_2_5 | 97 | 59.7 | 60.8 | 50.9-69.9 | CONSISTENT |
| lean | match_result | 38 | 59.1 | 65.8 | 49.9-78.8 | CONSISTENT |
| toss-up | ALL | 264 | 48.7 | 56.4 | 50.4-62.3 | UNDERSTATES |
| toss-up | btts | 79 | 52.3 | 57.0 | 46.0-67.3 | CONSISTENT |
| toss-up | goals_2_5 | 69 | 52.3 | 58.0 | 46.2-68.9 | CONSISTENT |
| toss-up | match_result | 116 | 44.1 | 55.2 | 46.1-63.9 | UNDERSTATES |

CONSISTENT = the hit-rate interval contains the mean stated probability. The record spans only a few weeks of one early season; a label verdict is a first read, not a guarantee.

## Decision

- No calibrator is served. A candidate becomes a `SHADOW_CANDIDATE` only with a test interval wholly below 0 (alpha divided by the candidates tried) and no worse Brier or ECE; even then serving it needs its own ADR and a shadow period.
- The candidates that cleared the gate on 2022-23 did not carry over to 2023-24 (see the paired intervals): the selection gain was not stable from one season to the next, the winner's-curse risk the plan warns about.

