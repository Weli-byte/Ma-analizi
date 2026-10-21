# Sprint 4: league-aware gradient-boosting adapters: report

Status: **Sprint 4 built and run on the real cached source files (2026-10-21).** Nothing is deployed, the global production model is untouched, no candidate is
promoted and nothing is calibrated: calibration, the frozen holdout and the approval gate are Sprint 5. Plan: [`plan.md`](plan.md) (PDF pages 10 to 13), decisions: ADR 0066
(builds on ADR 0063, 0064 and 0065). Everything below comes from `python -m src.league_models.ml_benchmark --as-of 2026-10-20T00:00:00Z` (command output in
[`evidence/`](evidence/), machine-readable results in the `s4_*` files next to this one; per-fixture predictions and the 192 fitted model files are in
`artifacts/league_models/ml/s4_final/`, git-ignored like every artifact).

## Headline findings (read these first)
1. **Same folds, same fixtures as Sprints 2 and 3.** Five expanding walk-forward folds (eval seasons 2020-21 to 2024-25, N = 10,712) identical to the committed Sprint 2 / 3 folds (the run
   refuses to start otherwise). The 2,278 holdout, 250 reserved and 750 guard fixtures are never read. Every model of Sprints 3 and 4 is scored on exactly these matches.
2. **Tuning, not league awareness, is what improves the boosted trees on 1X2.** The frozen league-blind GBM `A_gbm` scores 1.0123 log loss; the same features tuned on the inner
   validation season (`A_gbm_tuned`, the control) score 0.9986 (-0.0137, 95% interval -0.0171 to -0.0101). Adding league identity and priors to the *frozen* model changes nothing
   (`B_gbm_league_default` 1.0131, +0.0008, interval -0.0015 to +0.0031). The tuned league-aware model `B_gbm_league` (1.0001) beats the frozen baseline (-0.0122) but is not better
   than its tuned league-blind control (+0.0015, interval -0.0003 to +0.0030). Without the control the result would have read "league awareness helps"; with it, it does not.
3. **The best 1X2 model is still the Sprint 3 per-country Elo with the UEFA bridge.** `E_elo_bridge` 0.9958; the tuned league-blind GBM 0.9986 and the existing Elo `elo_s2` 0.9990 are
   statistically indistinguishable from it (paired intervals include 0). Elo strength is the signal: dropping it costs +0.0070 against the frozen GBM and makes the model worse than the
   frozen baseline (1.0193); permutation importance by group (mean over folds): Elo 0.0855, form 0.0109, venue form 0.0016, league identity 0.0007, league priors 0.0005, rest 0.0004.
4. **League awareness helps BTTS, not 1X2 or over/under.** BTTS pooled: `B_gbm_league` 0.6884 and `D_gbm_blend` 0.6881 against the tuned control 0.6927 (-0.0043, interval -0.0062 to -0.0025)
   and the base rate 0.6896; the frozen `A_gbm` (0.7005) is worse than the base rate. Over/under: B 0.6854 against the control 0.6863 (interval -0.0026 to +0.0007, no proof) and the base rate
   0.6915. Accuracy of every model is within about two points of the majority-class accuracy (BTTS 0.5429, over 2.5 0.5347), so accuracy says almost nothing here.
5. **Decisions for the nine competitions (dev out-of-sample evidence only, 135 comparisons).** 1X2: the global model is kept in all nine; no league-aware GBM beats its tuned league-blind control
   anywhere. A candidate passes every rule in only three competition-target cells: BTTS in La Liga (`B_gbm_league`), BTTS and over/under in the Bundesliga (`A_dc_ridge_tuned`, the Sprint 3
   Dixon-Coles with a tuned ridge). With 135 one-sided 2.5% comparisons a few passes are expected by chance; Sprint 5 must confirm them on the frozen holdout before they count.
6. **Local models could be tried only where the data allow it.** Trained (500 or more own training matches): Premier League, La Liga, Bundesliga, Serie A from the second fold (2021-22); Ligue 1
   from the third; Champions League and Europa League in the last fold only (619 and 626 matches, evidence tier); never the Conference League (at most 423) or the Süper Lig (306 matches in its
   only evaluation fold, 420 evaluation matches). The local GBM `C_gbm_local` is worse than the global model pooled (1.0083, +0.0097 against the control, interval +0.0066 to +0.0131); the
   shrinkage blend `D_gbm_blend` is no better than B (1.0006 vs 1.0001) and is significantly worse than the control (+0.0020, interval +0.0002 to +0.0036).
7. **League hypotheses.** Supported: La Liga (the Elo gap improves 1X2), Süper Lig (league-level home advantage and priors improve 1X2, 420 matches in one season). Not supported / inconclusive:
   Premier League rest, Serie A priors, Bundesliga goal density, Ligue 1 identity, Süper Lig venue form, the three UEFA competitions (identity plus bridge against the tuned control). Süper Lig squad
   continuity and any market claim are NOT_TESTABLE. No feature weight was fixed from a hypothesis, and nothing is read as causal.
8. **Errors are mostly draws.** The draw is the most likely class for only 40 of 10,712 matches; 0-0 and 1-1 results cost about 1.44 log loss against 0.92 to 0.94 for one-goal margins and 4-plus goal
   matches. Confidence is well ordered (accuracy 0.40 in [0.00, 0.40) up to 0.73 above 0.70, mean confidence 0.38 to 0.74). Mean favourite bias 0.0006, away probability 0.013 above the observed
   away rate, draw probability 0.007 below the observed draw rate. Late-season rows cost more (1.011) than early-season rows (0.999).
9. **Time safety.** Feature as-of check: replacing every result from two cut points on by 7-0 changes 8,093 and 3,633 later rows and none of the 4,447 and 8,911 earlier rows (0 violations); the
   Sprint 3 replay check is 0 violations in all five folds; the tuner only receives the inner blocks.
10. **Reproducibility.** `--verify` 210 of 210 files; `--reproduce` under Python 3.14.6 `REPRODUCED` (210 of 210); a run under Python 3.12.14 gives 210 of 210 byte-identical outputs
    (`evidence/s4_cross_python_check.json`) and `--compare-committed` passes. The Linux CI job is the check against another operating system (status below).

## Acceptance criteria of the Sprint 4 prompt
| Criterion | Result | Evidence |
|---|---|---|
| Baseline / adapted / local / blended candidates compared on the same OOS matches | DONE: one `n` for every model | `s4_ml_metrics.json`, `s4_paired_deltas.json`, `s4_paired_vs_control.json`, `s4_paired_vs_s3.json` |
| Leakage, split overlap, target order, missingness, one-class fold, unseen league, unseen new club, probability-sum tests green | DONE: 34 Sprint 4 tests | `tests/test_league_models_s4.py`, `evidence/pytest_s4.txt` |
| Tuning does not see the holdout; every trial traceable | DONE: 420 trials recorded, none stopped by the clock | `s4_tuning_trials.json`, `s4_leakage_audit.json` |
| Global fallback when superiority is not proven | DONE: 1X2 stays global in all nine; ineligible local fits fall back explicitly to B | `s4_league_decisions.json`, `s4_ml_metrics.json` (`fallbacks`) |
| Frozen OOF predictions, model cards, artifacts and eligibility evidence ready for Sprint 5 | DONE | handover section |

## What was built
- `src/league_models/ml_features.py`: the feature contract (38 columns in 8 groups, schema hash), the as-of builder (availability rule, same-kick-off isolation, Elo driven under the same rule) and the
  perturbation audit.
- `src/league_models/ml_models.py`: LightGBM / XGBoost wrapper (CPU, one thread, deterministic), the seeded Optuna tuner, local eligibility and the shrinkage blend.
- `src/league_models/ml_benchmark.py`: candidates, ablations, permutation importance, decisions per competition and target, hypotheses, error analysis, model cards, immutable run manifest and registry,
  `--dry-run`, `--verify`, `--reproduce`, `--compare-committed`.
- `configs/league_models/ml_models.yaml`: every parameter; `tests/test_league_models_s4.py`; `.github/workflows/league-benchmark.yml` gained the Sprint 4 steps; `pyproject.toml` a per-file ruff exception.

## Results (numbers copied from `s4_ml_metrics.json`, `s4_paired_deltas.json`, `s4_paired_vs_control.json`)

### 1X2, all folds pooled (N = 10,712)
| Model | Log loss | Brier | RPS | Accuracy | ECE (raw) | Delta vs frozen `A_gbm` (95% interval) | Delta vs tuned control `A_gbm_tuned` |
|---|---|---|---|---|---|---|---|
| `E_elo_bridge` (Sprint 3) | 0.9958 | 0.5940 | 0.2045 | 0.5216 | 0.0087 | -0.0165 [-0.0217, -0.0112] | -0.0028 [-0.0059, +0.0004] |
| `A_gbm_tuned` (control) | 0.9986 | 0.5954 | 0.2050 | 0.5215 | 0.0073 | -0.0137 [-0.0171, -0.0101] | reference |
| `elo_s2` (existing Elo) | 0.9990 | 0.5960 | 0.2053 | 0.5194 | 0.0262 | -0.0133 [-0.0189, -0.0076] | +0.0004 [-0.0036, +0.0045] |
| `B_gbm_league` | 1.0001 | 0.5961 | 0.2052 | 0.5229 | 0.0118 | -0.0122 [-0.0155, -0.0090] | +0.0015 [-0.0003, +0.0030] |
| `D_gbm_blend` | 1.0006 | 0.5964 | 0.2054 | 0.5220 | 0.0124 | -0.0117 [-0.0151, -0.0084] | +0.0020 [+0.0002, +0.0036] |
| `B_xgb_league` (cross-check, OS-dependent) | 1.0059 | 0.5998 | 0.2067 | 0.5181 | 0.0158 | -0.0064 [-0.0089, -0.0039] | +0.0073 [+0.0045, +0.0101] |
| `A_dc_ridge_tuned` | 1.0080 | 0.6023 | 0.2087 | 0.5056 | 0.0177 | -0.0043 [-0.0110, +0.0028] | +0.0093 [+0.0041, +0.0148] |
| `C_gbm_local` | 1.0083 | 0.6014 | 0.2074 | 0.5167 | 0.0107 | -0.0040 [-0.0088, +0.0007] | +0.0097 [+0.0066, +0.0131] |
| `A_dc` (Sprint 3 reference) | 1.0102 | 0.6033 | 0.2092 | 0.5039 | 0.0186 | -0.0020 [-0.0087, +0.0054] | +0.0116 [+0.0059, +0.0173] |
| `A_gbm` (frozen) | 1.0123 | 0.6036 | 0.2080 | 0.5150 | 0.0280 | reference | +0.0137 [+0.0101, +0.0171] |
| `B_gbm_league_default` | 1.0131 | 0.6040 | 0.2083 | 0.5135 | 0.0325 | +0.0008 [-0.0015, +0.0031] | +0.0145 [+0.0106, +0.0183] |
| `B_no_elo` (ablation) | 1.0193 | 0.6095 | 0.2115 | 0.5037 | 0.0062 | +0.0070 [+0.0025, +0.0118] | +0.0207 [+0.0166, +0.0245] |
| `historical_prior` | 1.0725 | 0.6492 | 0.2317 | 0.4308 | 0.0100 | +0.0602 [+0.0520, +0.0689] | +0.0739 [+0.0673, +0.0806] |

All other rows (the other ablations, Sprint 3 candidates) are in `s4_ml_metrics.json`. There is no single winner: `E_elo_bridge`, `A_gbm_tuned` and `elo_s2` are tied within noise on 1X2.

### Over/under 2.5 and BTTS (pooled; base rates 0.5347 over, 0.5429 yes)
| Target | Model | Log loss | Brier | Accuracy | ECE (raw) | Delta vs `A_gbm` | Delta vs control |
|---|---|---|---|---|---|---|---|
| Over/under | `B_gbm_league` | 0.6854 | 0.2461 | 0.5524 | 0.0114 | -0.0030 [-0.0055, -0.0005] | -0.0009 [-0.0026, +0.0007] |
| Over/under | `A_gbm_tuned` (control) | 0.6863 | 0.2466 | 0.5518 | 0.0120 | -0.0021 [-0.0042, +0.0000] | reference |
| Over/under | `A_gbm` | 0.6884 | 0.2476 | 0.5471 | 0.0260 | reference | +0.0021 [-0.0000, +0.0042] |
| Over/under | `A_dc` (Dixon-Coles totals) | 0.6907 | 0.2484 | 0.5563 | 0.0331 | +0.0023 [-0.0023, +0.0072] | +0.0044 [-0.0000, +0.0089] |
| Over/under | `train_over_rate` | 0.6915 | 0.2492 | 0.5347 | 0.0092 | +0.0030 [-0.0008, +0.0067] | +0.0051 [+0.0026, +0.0074] |
| BTTS | `D_gbm_blend` | 0.6881 | 0.2475 | 0.5424 | 0.0090 | -0.0124 [-0.0166, -0.0086] | -0.0046 [-0.0068, -0.0026] |
| BTTS | `B_gbm_league` | 0.6884 | 0.2476 | 0.5441 | 0.0112 | -0.0121 [-0.0161, -0.0085] | -0.0043 [-0.0062, -0.0025] |
| BTTS | `train_btts_rate` | 0.6896 | 0.2482 | 0.5429 | 0.0057 | -0.0109 [-0.0156, -0.0068] | -0.0031 [-0.0057, -0.0008] |
| BTTS | `A_gbm_tuned` (control) | 0.6927 | 0.2498 | 0.5282 | 0.0213 | -0.0078 [-0.0103, -0.0053] | reference |
| BTTS | `A_gbm` | 0.7005 | 0.2535 | 0.5148 | 0.0529 | reference | +0.0078 [+0.0053, +0.0103] |

A model that predicts the base rate (`train_*_rate`) is the honest bar for these two targets: the gap between the best model and the base rate is about 0.006 log loss for over/under and about 0.001 to 0.0015 for BTTS. Exact score comes
from the Dixon-Coles matrix of Sprint 3 only; no score was derived from a GBM.

### Decisions per competition (dev evidence only; `s4_league_decisions.json`)
| Competition | Training matches (last fold) | Tier | 1X2 | Over/under | BTTS |
|---|---|---|---|---|---|
| Premier League `ENG1` | 1,900 | local candidates over 1,500 | global | global | global |
| La Liga `ESP1` | 1,900 | local candidates over 1,500 | global | global | `B_gbm_league` |
| Bundesliga `GER1` | 1,530 | local candidates over 1,500 | global | `A_dc_ridge_tuned` | `A_dc_ridge_tuned` |
| Serie A `ITA1` | 1,900 | local candidates over 1,500 | global | global | global |
| Ligue 1 `FRA1` | 1,446 | evidence 500 to 1,500 | global | global | global |
| Süper Lig `TUR1` | 726 | evidence 500 to 1,500 | global | global | global |
| Champions League | 619 | evidence 500 to 1,500 | global | global | global |
| Europa League | 626 | evidence 500 to 1,500 | global | global | global |
| Conference League | 423 | local off under 500 | global | global | global |

"Global" means no league-aware candidate passed every rule (each blocking reason is listed per candidate in the file). The three candidate cells: BTTS La Liga `B_gbm_league` -0.0167 against `A_gbm`
(interval -0.0263 to -0.0074) and -0.0068 against the control (-0.0116 to -0.0020); Bundesliga `A_dc_ridge_tuned` BTTS -0.0281 (-0.0388 to -0.0174) and over/under -0.0192 (-0.0302 to -0.0075), against the control
-0.0197 and -0.0120. The 1X2 gains of `B_gbm_league` over the frozen baseline are large in the leagues (for example Premier League -0.0146, Serie A -0.0155) but every one of them disappears against the tuned control
(Premier League +0.0038, Serie A -0.0013 with an interval spanning 0), and the Premier League, Ligue 1 and Champions League candidates are significantly *worse* than the Sprint 3 Elo.

### Tuning, rounds and compute
- Optuna TPE (seed 20261021), 14 trials per study on a coarse categorical grid, early stopping 30 rounds, studies for `B_gbm_league` and for the control, per fold and target: 420 trials, all stored; none was stopped by the wall clock.
  Chosen learning rates 0.03 to 0.08, 4 or 8 leaves in 22 of the 30 studies (31 leaves in 2), `min_data_in_leaf` 50 or 100 in 25 of 30, bagging fraction 0.7 in 21 of 30: the search prefers small, strongly regularised trees.
  Best inner-validation log loss for 1X2 ranged from 0.9750 to 1.0204 across folds; BTTS needed only 9 to 44 boosting rounds (almost no signal).
- Final rounds = best iteration scaled by training size / inner-fit size. The whole run takes about two minutes on one CPU thread; no GPU was used or needed (`device` in the run manifest).
- Ridge of the Sprint 3 Dixon-Coles (finding 4 of that report) tuned on the inner block: chosen team ridge 16, 16, 8, 16, 1 for the five folds (default 2). `A_dc_ridge_tuned` improves on `A_dc` (-0.0023 pooled) but is not
  better than the fixed 4x ridge of Sprint 3 (`A_dc_shrink` 1.0070 vs 1.0080): the choice from one validation season is noisy (the last fold picked the smallest value).
- First fold: only one training season exists, so the inner block is a 70 / 30 chronological cut with a purge (recorded as `WITHIN_SEASON_CHRONOLOGICAL_PURGED`).

### Blend weights
Learned from out-of-fit predictions of the inner-validation season, shrunk by n / (n + 300), 0 unless the blend beats B by 0.002. In the last fold: Serie A 0.56 (1X2) and 0.56 (over/under), 0.39 (BTTS); Ligue 1 0.30 / 0.35 / 0;
Bundesliga 0 / 0 / 0.40; every UEFA competition 0. Weights above 0 did not translate into out-of-sample gains (finding 6).

### Hypothesis tests (`s4_hypotheses.json`; SUPPORTED only if the whole 95% block interval of variant minus base is below 0)
| Hypothesis | Competition | Variant vs base | Delta log loss [95% interval] | Verdict |
|---|---|---|---|---|
| Rest / fixture congestion | Premier League | B vs no rest | +0.0001 [-0.0029, +0.0031] | INCONCLUSIVE |
| League priors (draw / low scoring) | Serie A | B vs no league priors | -0.0017 [-0.0045, +0.0010] | INCONCLUSIVE |
| Elo strength gap | La Liga | B vs no Elo | -0.0245 [-0.0334, -0.0157] | SUPPORTED |
| League goal density (over/under) | Bundesliga | B vs no league priors | +0.0027 [-0.0004, +0.0057] | INCONCLUSIVE |
| League identity and priors | Ligue 1 | B vs tuned league-blind | +0.0008 [-0.0026, +0.0043] | INCONCLUSIVE |
| League-level home advantage / priors | Süper Lig | B vs no league priors | -0.0139 [-0.0226, -0.0055] | SUPPORTED (one season, 420 matches) |
| Venue form | Süper Lig | B vs no venue form | -0.0005 [-0.0088, +0.0077] | INCONCLUSIVE |
| Identity plus bridge Elo | Champions / Europa / Conference | B vs tuned league-blind | +0.0015 / -0.0006 / +0.0017 | INCONCLUSIVE (all three) |
| Squad continuity | Süper Lig | n/a | n/a | NOT_TESTABLE (no squad data) |
| Beating a bookmaker baseline | all | n/a | n/a | NOT_TESTABLE (no licensed timestamped odds; no odds-aware model exists) |

### Error analysis (`s4_error_analysis.json`)
Per league x season: confusion matrices, favourite bias, away and draw bias, label rates for `A_gbm`, `A_gbm_tuned`, `B_gbm_league`, `D_gbm_blend` and `E_elo_bridge`; segments (early / late third of the season, clubs new to the
history, 0-0, 1-1, four or more goals, one-goal margin); confidence bins; over/under against the base rate and "always over", BTTS against the majority class. Segments by missing xG or odds are impossible in this universe and
are reported as such; UEFA stages are not marked in the source (analysed per competition).

## Honest limits
- Score-only and `BACKTEST_LIMITED`; odds-free only. The odds-aware model card is NOT_AVAILABLE and no claim about beating the market is made. xG, lineups, injuries and shots are not in the licensed universe and were never imputed.
- `A_gbm` is a score-only re-implementation of a frozen GBM; the production XGBoost / LightGBM cannot run on this universe (its feature pipeline reads the BLOCKED football-data.co.uk source).
- Each fold is tuned on one season (the first on a 70 / 30 cut of one season): small blocks, noisy choices.
- Selection on the inner season (tuning, early stopping) also feeds the blend weights: a small bias that favours w = 0.
- The Süper Lig has 420 evaluation matches in one season; the Conference League has no local-eligible fold; UEFA finals carry a home advantage.
- Permutation importance is an association on the inner-validation season. SHAP was not computed.
- XGBoost (`B_xgb_league`, fixed parameters) is a backend cross-check only: its CPU results are reproducible on one machine but differed on Linux CI (accuracy by up to 0.02 per league-season), so it is not a decision candidate and is skipped in the cross-OS comparison. The LightGBM and statistical results matched Linux within 1e-3.
- Raw probabilities: the ECE values here are before any calibration (Sprint 5).

## Handover to Sprint 5
- Frozen out-of-fold probabilities of every model on the Sprint 2 folds: `artifacts/league_models/ml/s4_final/fold_predictions.jsonl` (hash in `s4_run_manifest.json`); fitted models in `models/` with their hashes in
  `training_runs_with_artifact_hashes.json`; the committed `s4_training_runs.json` has the same records without the model-file hashes.
- `s4_eligibility_evidence.json` (per competition and target: decision and the blocking reasons of every candidate), `s4_model_cards.md`, `s4_feature_schema.json` (schema hash `0e5a2f89...`), `s4_tuning_trials.json`.
- Still required in Sprint 5: calibration fitted on a separate chronological split, the one-shot evaluation of the frozen holdout with block-bootstrap intervals, and the approval gate. Leagues without proven improvement stay on
  the global model.

## Commands and checks
```
python -m src.league_models.ml_benchmark --as-of 2026-10-20T00:00:00Z --dry-run
python -m src.league_models.ml_benchmark --as-of 2026-10-20T00:00:00Z --output-dir artifacts/league_models/ml/s4_final --docs-dir docs/league_models
python -m src.league_models.ml_benchmark --verify artifacts/league_models/ml/s4_final
python -m src.league_models.ml_benchmark --as-of 2026-10-20T00:00:00Z --reproduce artifacts/league_models/ml/s4_final
python -m src.league_models.ml_benchmark --as-of 2026-10-20T00:00:00Z --output-dir artifacts/league_models/ml/ci --compare-committed docs/league_models
```
- Local checks (2026-10-21, Windows 11): `ruff check .` clean; the full `pytest` run: 1,049 tests, no failure and no skip (the 34 Sprint 4 tests alone: `evidence/pytest_s4.txt`); `--verify` 210 of 210; `--reproduce` (Python 3.14.6) `REPRODUCED` 210 of 210; Python 3.12.14 run byte-identical (210 of 210)
  and `--compare-committed` ok (`evidence/s4_cross_python_check.json`).
- CI (GitHub Actions, PR #86, 2026-10-21): the `league-benchmark` job (Ubuntu 24.04, Python 3.12, archived snapshot) re-ran Sprints 2, 3 and 4 and passed the comparison with the committed files; `test (3.12)`, `test (3.14)` and
  `real-data-sanity` passed. The first Linux run failed for two real reasons, both fixed: XGBoost CPU results differ between operating systems (now a reported cross-check outside the comparison) and the committed
  `training_runs` carried the git sha of the local checkout. Every LightGBM and statistical result matched Linux within 1e-3. The `lock-up-to-date` check fails on this and every other pull request because
  upstream packages (for example fastapi 0.157.0) were released after the last relock; it is unrelated to this change (relock pull request #83 is open).
