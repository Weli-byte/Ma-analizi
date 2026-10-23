# Sprint 4c - separately trained per-league models, learned hierarchy, nested selection, extended features, exact score (ADR 0071)

Development folds 2020-21 to 2024-25 (10,712 matches). The frozen holdout is consumed and was not read. Nothing here approves a model for users.

## What was asked and what happened

| # | Gap from the Sprint 6 list | Status | Where |
|---|---|---|---|
| 6 | Full hierarchical (random-effect) team strength | BUILT: team < country < world + competition terms, variance components learned per target by nested walk-forward (cross-validated empirical Bayes, not marginal likelihood) | `per_league.HierarchicalDC` |
| 7 | Opponent-adjusted form, 90 / 365-day windows, time weights | BUILT as an opt-in extension (own schema, 0 leakage violations over 3 cut points); helps the tuned trees slightly on 1X2 and over/under | `extra_features.py` |
| 8 | Nested walk-forward for every choice | BUILT: up to 3 inner seasons per choice, per target, every candidate recorded; also in the boosted-tree tuner | `nested.py` |
| 9 | Exact-score target and the 68.8% claim | BUILT: exact-score table + paired intervals; the claim is NOT reproducible (max over-2.5 rate per competition-season 64.7%) | `s4c_exact_score.json` |
| 10 | Expected goals in the prediction record | BUILT as an append-only sidecar keyed to the record hash (the immutable record is unchanged) | `rollout/records.ScoreForecastStore` |
| - | One model trained separately for each league | BUILT and TESTED (Poisson / Dixon-Coles and LightGBM per competition and target). Result: no gain over pooled models; see below | `per_league.PerLeagueDC`, `s4c_benchmark.py` |

## Pooled results (log loss, all competitions)

| Target | Candidate | Reference | Candidate | Reference | Delta | 95% CI | Verdict |
|---|---|---|---|---|---|---|---|
| 1x2 | P_dc_sep | B_dc_league | 1.0115 | 1.0113 | +0.0002 | [-0.0020, +0.0024] | INCONCLUSIVE |
| 1x2 | P_dc_sep | production_global | 1.0115 | 0.9990 | +0.0125 | [+0.0072, +0.0180] | WORSE |
| 1x2 | P_dc_sep | E_elo_bridge | 1.0115 | 0.9958 | +0.0157 | [+0.0102, +0.0215] | WORSE |
| 1x2 | H_dc_hier | B_dc_league | 1.0066 | 1.0113 | -0.0047 | [-0.0063, -0.0033] | SUPPORTED |
| 1x2 | H_dc_hier | production_global | 1.0066 | 0.9990 | +0.0076 | [+0.0026, +0.0126] | WORSE |
| 1x2 | G_extra | G_base | 0.9977 | 1.0000 | -0.0024 | [-0.0039, -0.0005] | SUPPORTED |
| 1x2 | G_base | production_global | 1.0000 | 0.9990 | +0.0010 | [-0.0033, +0.0052] | INCONCLUSIVE |
| 1x2 | G_extra | production_global | 0.9977 | 0.9990 | -0.0013 | [-0.0053, +0.0027] | INCONCLUSIVE |
| 1x2 | P_gbm_sep | G_extra | 1.0103 | 0.9977 | +0.0126 | [+0.0091, +0.0165] | WORSE |
| 1x2 | P_gbm_sep | G_base | 1.0103 | 1.0000 | +0.0103 | [+0.0060, +0.0146] | WORSE |
| 1x2 | P_gbm_sep | production_global | 1.0103 | 0.9990 | +0.0113 | [+0.0059, +0.0165] | WORSE |
| 1x2 | P_gbm_sep | P_dc_sep | 1.0103 | 1.0115 | -0.0012 | [-0.0077, +0.0059] | INCONCLUSIVE |
| over_under | P_dc_sep | B_dc_league | 0.6884 | 0.6915 | -0.0031 | [-0.0046, -0.0017] | SUPPORTED |
| over_under | P_dc_sep | production_global | 0.6884 | 0.6964 | -0.0080 | [-0.0104, -0.0057] | SUPPORTED |
| over_under | H_dc_hier | B_dc_league | 0.6842 | 0.6915 | -0.0073 | [-0.0100, -0.0047] | SUPPORTED |
| over_under | H_dc_hier | production_global | 0.6842 | 0.6964 | -0.0122 | [-0.0158, -0.0088] | SUPPORTED |
| over_under | G_extra | G_base | 0.6829 | 0.6853 | -0.0023 | [-0.0037, -0.0010] | SUPPORTED |
| over_under | G_base | production_global | 0.6853 | 0.6964 | -0.0112 | [-0.0164, -0.0061] | SUPPORTED |
| over_under | G_extra | production_global | 0.6829 | 0.6964 | -0.0135 | [-0.0184, -0.0090] | SUPPORTED |
| over_under | P_gbm_sep | G_extra | 0.6888 | 0.6829 | +0.0059 | [+0.0035, +0.0085] | WORSE |
| over_under | P_gbm_sep | G_base | 0.6888 | 0.6853 | +0.0036 | [+0.0011, +0.0065] | WORSE |
| over_under | P_gbm_sep | production_global | 0.6888 | 0.6964 | -0.0076 | [-0.0128, -0.0023] | SUPPORTED |
| over_under | P_gbm_sep | P_dc_sep | 0.6888 | 0.6884 | +0.0004 | [-0.0038, +0.0049] | INCONCLUSIVE |
| btts | P_dc_sep | B_dc_league | 0.6919 | 0.6933 | -0.0013 | [-0.0026, +0.0001] | INCONCLUSIVE |
| btts | P_dc_sep | production_global | 0.6919 | 0.6968 | -0.0049 | [-0.0069, -0.0029] | SUPPORTED |
| btts | H_dc_hier | B_dc_league | 0.6896 | 0.6933 | -0.0037 | [-0.0053, -0.0020] | SUPPORTED |
| btts | H_dc_hier | production_global | 0.6896 | 0.6968 | -0.0072 | [-0.0098, -0.0046] | SUPPORTED |
| btts | G_extra | G_base | 0.6891 | 0.6880 | +0.0011 | [-0.0001, +0.0023] | INCONCLUSIVE |
| btts | G_base | production_global | 0.6880 | 0.6968 | -0.0088 | [-0.0118, -0.0054] | SUPPORTED |
| btts | G_extra | production_global | 0.6891 | 0.6968 | -0.0078 | [-0.0110, -0.0043] | SUPPORTED |
| btts | P_gbm_sep | G_extra | 0.6950 | 0.6891 | +0.0059 | [+0.0036, +0.0082] | WORSE |
| btts | P_gbm_sep | G_base | 0.6950 | 0.6880 | +0.0070 | [+0.0047, +0.0095] | WORSE |
| btts | P_gbm_sep | production_global | 0.6950 | 0.6968 | -0.0018 | [-0.0058, +0.0024] | INCONCLUSIVE |
| btts | P_gbm_sep | P_dc_sep | 0.6950 | 0.6919 | +0.0030 | [-0.0004, +0.0067] | INCONCLUSIVE |

`production_global` = Elo for 1X2, Dixon-Coles for totals and BTTS (the models the product uses). `P_dc_sep` pooled rows mix separate models with reference fallbacks for ineligible competitions; use the per-competition table.

## Per competition: separately trained Poisson / Dixon-Coles against the production global model

Delta of log loss (negative = separate model better); `*` interval entirely below 0, `!` entirely above 0.

| Competition | 1X2 | Over/under | BTTS |
|---|---|---|---|
| ENG1 | +0.023! | -0.007* | +0.001 |
| ESP1 | -0.010 | -0.002 | -0.003 |
| FRA1 | +0.032! | -0.012* | -0.011* |
| GER1 | +0.002 | +0.000 | +0.001 |
| ITA1 | +0.007 | -0.009* | -0.004 |
| TUR1 | +0.006 | -0.004 | -0.017* |
| UEFA_UCL | +0.018 | -0.008 | -0.001 |
| UEFA_UECL | +0.034! | -0.017* | -0.011 |
| UEFA_UEL | +0.007 | -0.022* | -0.010 |

## Per competition: hierarchical model with learned variance components against the production global model

| Competition | 1X2 | Over/under | BTTS |
|---|---|---|---|
| ENG1 | +0.016! | -0.013* | -0.003 |
| ESP1 | -0.009 | -0.002 | -0.005* |
| FRA1 | +0.023! | -0.014* | -0.011* |
| GER1 | +0.002 | -0.000 | +0.002 |
| ITA1 | +0.010 | -0.007* | -0.004 |
| TUR1 | +0.003 | -0.016* | -0.026* |
| UEFA_UCL | -0.003 | -0.023* | -0.003 |
| UEFA_UECL | +0.015 | -0.037* | -0.025* |
| UEFA_UEL | +0.001 | -0.034* | -0.018* |

## What the nested selection chose (last outer fold 2024-25, trained on the seasons before it, per target)

Team penalty `l2_team` (larger = teams pulled harder toward the competition average) and decay half-life in days.

| Competition | 1X2 | Over/under | BTTS |
|---|---|---|---|
| ENG1 | pen 0.5 / hl 365 | pen 128.0 / hl 365 | pen 128.0 / hl 365 |
| ESP1 | pen 8.0 / hl 365 | pen 64.0 / hl 365 | pen 128.0 / hl 365 |
| FRA1 | pen 16.0 / hl none | pen 64.0 / hl 365 | pen 128.0 / hl 365 |
| GER1 | pen 32.0 / hl 1095 | pen 32.0 / hl none | pen 4.0 / hl none |
| ITA1 | pen 4.0 / hl 365 | pen 128.0 / hl 365 | pen 64.0 / hl 365 |
| TUR1 | pen 16.0 / hl none | pen 128.0 / hl none | pen 128.0 / hl none |
| UEFA_UCL | pen 4.0 / hl 365 | pen 128.0 / hl 365 | pen 32.0 / hl 365 |
| UEFA_UECL | LOCAL_INELIGIBLE | - | - |
| UEFA_UEL | pen 8.0 / hl 365 | pen 128.0 / hl none | pen 128.0 / hl 365 |

Reading: for the result (1X2) the data wants moderate team terms; for goal totals and BTTS it wants teams almost pooled. That is why the learned hierarchy beats fixed penalties on totals, and why a separate model per league adds nothing: the league is already a term in the pooled model.

## Exact score (all competitions)

| Model | Log score of the realised scoreline | Top-1 hit | Top-5 hit |
|---|---|---|---|
| B_dc_league | 2.9898 | 12.3% | 46.5% |
| H_dc_hier | 2.9775 | 12.6% | 46.6% |
| P_dc_sep | 2.9893 | 12.3% | 46.5% |
| constant_scoreline | 3.0924 | 11.9% | 42.7% |

## Honest limits

* 340 paired comparisons; about 8 SUPPORTED are expected by chance alone. The pooled lines above are the evidence; single cells are not.
* 76 of 90 selected values lie on the edge of their grid (mostly the strongest team penalty for totals and BTTS): the optimum may be beyond the grid.
* Selection and evaluation share the development folds (the selection uses inner folds that end before each eval season, so no eval season informs its own choice), but the holdout is consumed: confirmation is prospective only (shadow families `P_dc_sep`, `H_dc_hier`).
* Reproduction check: the whole run was executed twice on the same machine (2026-10-23); all seven committed `s4c_*.json` outputs are byte-identical between the two runs (wall-clock timings are excluded from the outputs). Cross-machine reproduction was NOT run.
