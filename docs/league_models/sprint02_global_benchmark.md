# Sprint 2: global reference and historical benchmark: report

Status: **Sprint 2 built and run on the real cached source files (2026-10-20).** No new production model, nothing deployed,
the global production model is untouched. Plan: [`plan.md`](plan.md) (PDF pages 5 to 7), decisions: ADR 0064 (builds on ADR 0063).
Everything below comes from `python -m src.league_models.benchmark --as-of 2026-10-20T00:00:00Z` (command output in
[`evidence/`](evidence/), machine-readable results in the `s2_*.json` files next to this one, full per-fixture predictions in
`artifacts/league_models/benchmark/s2_final/fold_predictions.jsonl`, which is git-ignored like every artifact).

## Headline findings (read these first)
1. **Reference scores are now frozen on one fixture set.** 10,712 finished matches of 9 competitions, 5 expanding walk-forward folds
   (eval seasons 2020-21 to 2024-25), every model scored on exactly the same fixtures. Three roles are held back and never read by a model:
   2,278 holdout, 250 reserved (running season), 750 guard (seasons the research benchmark locks as final test for EPL and La Liga).
2. **Elo is the best 1X2 reference on this universe, Dixon-Coles is the yardstick.** Pooled log loss: Elo 0.9990, Poisson 1.0225, Dixon-Coles
   1.0230, league prior 1.0725. Elo minus Dixon-Coles is -0.0239 (95% block interval -0.0303 to -0.0172). Part of that gap is not model class:
   Elo updates after every result inside the eval season (legal as-of), while Poisson/Dixon-Coles are fitted once per fold at the
   season cutoff. Poisson and Dixon-Coles are statistically indistinguishable (delta -0.0005, interval -0.0012 to +0.0001).
   No single winner is declared; later candidates must beat **both** references.
3. **For over/under 2.5 and BTTS the global goal model does not beat a constant rate in log loss.** Constant train-rate log loss 0.6898 /
   0.6891 versus Dixon-Coles 0.6964 / 0.6968 (paired deltas -0.0066 and -0.0077, intervals exclude 0). Its ranking skill is real but
   small (AUC 0.5575 and 0.5326 versus 0.5 for a constant), its probabilities are not well scaled (ECE 0.044 and 0.039 versus 0.021 and 0.015).
4. **Unconverged fits exist and are reported, not hidden.** Poisson/Dixon-Coles IPF did not reach the 1e-6 tolerance in the two
   oldest folds (final deltas 5.4e-4 and 9.4e-5); the Elo ordinal-logit BFGS stopped with "Desired error not necessarily achieved"
   in two folds. The models' own contract uses such a fit as-is; the flags are in `s2_benchmark_metrics.json -> fit_health`. The scores
   of those folds are therefore a conservative reference for Sprint 3, not a tuned optimum.
5. **Many clubs in eval fixtures were never seen in training** (1,078 of 2,576 rows in the first fold, 227 of 1,513 in the last): UEFA
   qualifying clubs and Süper Lig clubs link to the rest only through a few European matches. They are predicted at league-average
   strength and counted. This is the plan's central reason to test league effects and the UEFA bridge in Sprint 3.
6. **The 138-match claims are arithmetically correct and not comparable to this benchmark.** The table recounts to 94, 84 and 76 hits
   out of 138 (68.1%, 60.9%, 55.1%); "always over" is 95/138 = 68.8% on the same fixtures, and "always both teams score" is 87/138 = 63.0%,
   so the model lost to both constant guesses in that window. The licensed benchmark has 53.5% overs and 54.3% BTTS over different leagues and seasons.
7. **Existing research benchmark audited, not reusable.** Its `predictions.jsonl` (20,520 records, `dv-c89f77323b6e`) matches its recorded hash
   once Windows CRLF line ends are normalised; it was scored on the football-data.co.uk dataset (BLOCKED for league models) and no model
   checksum was ever stored (models are refitted per fold).

## Acceptance criteria of the Sprint 2 prompt
| Criterion | Result | Evidence |
|---|---|---|
| Every league and target has a baseline comparison on the same fixture set | DONE for 1X2, over/under 2.5 and BTTS; asserted by test `test_every_model_is_scored_on_exactly_the_same_fixtures` and by N in every row | `s2_benchmark_metrics.json` |
| Chronological train / validation / calibration / holdout separation verified | DONE for train, inner-validation block, eval and holdout (calibration is not fitted in Sprint 2: raw reliability only) | `s2_protocol_manifest.json`, `s2_leakage_audit.json`, tests |
| Real model artifacts and data hashes stored | PARTIAL by design: no model file exists for the existing models (refitted per fold); data snapshot, config, licence manifest, fold id lists and every output are hash-pinned | `s2_run_manifest.json`, `registry.jsonl` |
| Claims such as 68.1% classified VERIFIED / UNVERIFIED | DONE | `s2_claims.json`, section below |
| Fold protocol, global reference scores and frozen holdout handed to Sprint 3 | DONE | section "Handover to Sprint 3" |

## Task 1: model and production-path audit
Read from the repository and its artifacts, nothing re-scored (`s2_global_artifact_audit.json`).
- Production-family models in `src/models`: always-home, per-league prior, Elo, Poisson, Dixon-Coles (and a research-only joint-MLE
  variant), XGBoost, LightGBM; calibration (S9), ensemble (S11), LLM (S8) sit around them. Probability order is HOME, DRAW, AWAY everywhere.
- The existing research walk-forward run `dv-c89f77323b6e_fv2_split-d642486e1cbf_walkforward`: 20,520 prediction records, models
  `always_home, dixon_coles, elo, historical_prior, lightgbm, market_implied, poisson, recent_form_naive, xgboost`, feature version `fv2`,
  git sha `798f979b029634efa142a787c169d09fe4531e08` (working tree dirty at run time), dependency-lock hash `942cc238...`, folds 2021-22 to 2023-24,
  final-test seasons 2024-25 and 2025-26. Track: **historical backtest** (information cutoff = kick-off); no prospective record is mixed in.
- Missing or unavailable, said plainly: model checksum `NOT_RECORDED`; training-data hash is the dataset content hash `cf2a5df5...`;
  the run cannot be reused for the league programme (blocked source).
- Metrics of that run came from fixture rows of EPL and La Liga only. The league programme did not copy them.

## Task 2: common test universe
Roles and folds are in ADR 0064 and `s2_protocol_manifest.json` (fixture-id hashes per fold, so any fixture list can be re-derived and checked).
Season roles per competition come from the Sprint 1 training decision (53 trainable complete, 5 partial, blocked seasons stay blocked).
Süper Lig has one dev fold (2020-21, 420 matches) because its usable seasons are 2019-20, 2020-21 and 2024-25 (holdout). The data volume
tiers of the plan are respected: no per-league model is trained here.

## Task 3: baselines
Available: see `s2_baseline_status.json`. **UNAVAILABLE:** market favourite and any odds baseline (the licensed universe has no odds; none invented, so
no overround rule was needed). **NOT_RUN_FEATURES_BLOCKED:** XGBoost, LightGBM, the global binary classifier for over/under and the production
ensemble (their feature pipeline reads the BLOCKED football-data.co.uk dataset; a score-only GBM is a Sprint 4 candidate, not a Sprint 2 baseline).

## Task 4: metric engine
1X2 uses the repository's shared engine unchanged. Added: binary log loss, Brier, accuracy (fractional credit at exactly 0.5), top-label ECE,
AUC (undefined with one class: reported as `null`), reliability tables, ISO-week block bootstrap with paired deltas. Probability policy and
clipping are documented in `src/league_models/bench_metrics.py`. Coverage: every model predicted every fixture (0 abstentions); latency is in
`artifacts/.../timings.json` (kept out of hashes). Per-fixture predictions carry fold, stage, snapshot and track labels.

## Task 5: reproducibility
`python -m src.league_models.benchmark --as-of 2026-10-20T00:00:00Z --output-dir artifacts/league_models/benchmark/s2_final` (about 8 minutes; the
Poisson/Dixon-Coles fits dominate). `--dry-run` prints the plan, `--verify DIR` re-hashes a run, `--reproduce DIR` re-runs and compares.
- Two independent full runs of the previous revision wrote byte-identical result files (all 9 hashes equal; only the audit file and the
  fit-health block were added afterwards).
- `--verify` of the final run: 9 of 9 files match their recorded hashes. `--reproduce` of the 2024-25 window: `REPRODUCED` (9 of 9).
- Full-run `--reproduce` of the final revision: `REPRODUCED`, 9 of 9 files identical (`evidence/s2_reproduce_full.json`).
- The run manifest stores config fingerprint, snapshot id (`snap-546e70fff7fb`), data snapshot hash, licence manifest hash, git sha, Python 3.14.6, numpy,
  dependency-lock hash, seeds. The registry entry is append-only; its id derives from the output hashes.
- Not claimed: reproduction on Python 3.12 or on another platform (not run); CI does not run the real-data benchmark (no source cache there).

## Task 6: claims
| Claim | Class | Basis |
|---|---|---|
| 1X2 94/138 = 68.1% | VERIFIED_ARITHMETIC | `reports/track_record_table.md` recounts to 94; 139 of 150 fixture directories in the window have an artifact generated on an earlier calendar day (conservative pre-match proof, kick-off time is not in the key) |
| Over/under 84/138 = 60.9% | VERIFIED_ARITHMETIC | same table |
| BTTS 76/138 = 55.1% | VERIFIED_ARITHMETIC | same table |
| "Always over" 68.8% | VERIFIED_ARITHMETIC | 95/138 on the same fixtures |
| Model beats always-over | NO in that window | 60.9% < 68.8% |
| Over share of the benchmark set equals 68.8% | FALSE | 53.5% over 10,712 matches of 2020-21 to 2024-25 |
| Small league samples (9/9, 6/7) | NOT EVIDENCE | no league claim made |
| Same fixture set as the licensed benchmark | FALSE | the 138 are October 2026 fixtures of the running season (RESERVED) |
The table is a derived report; "verified arithmetic" does not mean the forecasts were independently re-derived.

## Results (all numbers copied from `s2_benchmark_metrics.json` and `s2_paired_deltas.json`)

### 1X2, all folds pooled (N = 10712)

| Model | Log loss | 95% block CI | Brier | RPS | Accuracy | ECE (raw) |
|---|---|---|---|---|---|---|
| `always_home` | 19.5103 | [19.1337, 19.8747] | 1.1298 | 0.4413 | 0.4351 | 0.5649 |
| `historical_prior` | 1.0725 | [1.0678, 1.0777] | 0.6492 | 0.2317 | 0.4308 | 0.0100 |
| `elo` | 0.9990 | [0.9899, 1.0085] | 0.5960 | 0.2053 | 0.5194 | 0.0262 |
| `poisson` | 1.0225 | [1.0137, 1.0313] | 0.6102 | 0.2123 | 0.5012 | 0.0274 |
| `dixon_coles` | 1.0230 | [1.0141, 1.0319] | 0.6101 | 0.2123 | 0.5008 | 0.0227 |

Paired log-loss delta against the reference `dixon_coles` (model minus reference; negative = better), 1,000 ISO-week block draws:

| Model | Delta log loss | 95% interval | Delta Brier | 95% interval | Share of draws below 0 (log loss) |
|---|---|---|---|---|---|
| `always_home` | +18.4873 | [+18.1176, +18.8480] | +0.5196 | [+0.5003, +0.5391] | 0.000 |
| `elo` | -0.0239 | [-0.0303, -0.0172] | -0.0142 | [-0.0183, -0.0098] | 1.000 |
| `historical_prior` | +0.0495 | [+0.0411, +0.0572] | +0.0391 | [+0.0338, +0.0439] | 0.000 |
| `poisson` | -0.0005 | [-0.0012, +0.0001] | +0.0001 | [-0.0003, +0.0004] | 0.931 |

### 1X2 log loss by competition (same fixtures for every model)

| Competition | N | `dixon_coles` | `poisson` | `elo` | `historical_prior` | Elo minus DC (95% interval) |
|---|---|---|---|---|---|---|
| `ENG1` | 1520 | 0.9997 | 0.9973 | 0.9830 | 1.0672 | -0.0167 [-0.0315, -0.0026] |
| `ESP1` | 1520 | 0.9973 | 0.9986 | 1.0046 | 1.0742 | +0.0072 [-0.0061, +0.0197] |
| `FRA1` | 1752 | 1.0573 | 1.0553 | 1.0151 | 1.0802 | -0.0422 [-0.0609, -0.0246] |
| `GER1` | 1530 | 1.0123 | 1.0138 | 1.0120 | 1.0762 | -0.0003 [-0.0119, +0.0118] |
| `ITA1` | 1890 | 0.9965 | 0.9992 | 0.9876 | 1.0864 | -0.0089 [-0.0187, +0.0024] |
| `TUR1` | 420 | 1.1559 | 1.1563 | 1.0476 | 1.0860 | -0.1083 [-0.1553, -0.0597] |
| `UEFA_UCL` | 689 | 0.9375 | 0.9346 | 0.9319 | 1.0376 | -0.0056 [-0.0309, +0.0216] |
| `UEFA_UECL` | 576 | 1.1709 | 1.1638 | 1.0359 | 1.0575 | -0.1350 [-0.1774, -0.0930] |
| `UEFA_UEL` | 815 | 1.0208 | 1.0188 | 0.9917 | 1.0567 | -0.0291 [-0.0560, -0.0016] |

### 1X2 log loss by eval season

| Season | N | `dixon_coles` | `poisson` | `elo` | `historical_prior` |
|---|---|---|---|---|---|
| 2020-21 | 2576 | 1.0646 | 1.0630 | 1.0178 | 1.0827 |
| 2021-22 | 2231 | 1.0210 | 1.0213 | 1.0115 | 1.0774 |
| 2022-23 | 2233 | 1.0200 | 1.0190 | 0.9950 | 1.0659 |
| 2023-24 | 2159 | 0.9934 | 0.9943 | 0.9772 | 1.0707 |
| 2024-25 | 1513 | 1.0013 | 1.0003 | 0.9858 | 1.0604 |

### Over/under 2.5 goals, all folds pooled (N = 10712; positive-class share 0.5347)

| Model | Log loss | 95% block CI | Brier | Accuracy | ECE (raw) | AUC |
|---|---|---|---|---|---|---|
| `always_over` | 16.0699 | [15.7330, 16.3960] | 0.4653 | 0.5347 | 0.4653 | 0.5000 |
| `always_under` | 18.4688 | [18.1427, 18.8058] | 0.5347 | 0.4653 | 0.5347 | 0.5000 |
| `train_over_rate` | 0.6898 | [0.6869, 0.6926] | 0.2483 | 0.5474 | 0.0211 | 0.5314 |
| `poisson_totals` | 0.6964 | [0.6918, 0.7013] | 0.2510 | 0.5471 | 0.0439 | 0.5575 |
| `dixon_coles_totals` | 0.6964 | [0.6918, 0.7013] | 0.2510 | 0.5471 | 0.0439 | 0.5575 |

Paired against `dixon_coles_totals`:

| Model | Delta log loss | 95% interval | Share of draws below 0 |
|---|---|---|---|
| `always_over` | +15.3735 | [+15.0369, +15.6998] | 0.000 |
| `always_under` | +17.7724 | [+17.4490, +18.1096] | 0.000 |
| `poisson_totals` | +0.0000 | [-0.0000, +0.0000] | 0.070 |
| `train_over_rate` | -0.0066 | [-0.0117, -0.0016] | 0.994 |

By competition: positive-class share and log loss of the constant-rate baseline vs the Dixon-Coles distribution.

| Competition | Positive share | constant rate | Dixon-Coles |
|---|---|---|---|
| `ENG1` | 0.553 | 0.6897 | 0.6974 |
| `ESP1` | 0.455 | 0.6893 | 0.6827 |
| `FRA1` | 0.538 | 0.6910 | 0.7011 |
| `GER1` | 0.600 | 0.6749 | 0.6619 |
| `ITA1` | 0.511 | 0.7009 | 0.6991 |
| `TUR1` | 0.500 | 0.6974 | 0.7158 |
| `UEFA_UCL` | 0.579 | 0.6845 | 0.6993 |
| `UEFA_UECL` | 0.543 | 0.6898 | 0.7282 |
| `UEFA_UEL` | 0.547 | 0.6911 | 0.7338 |

### Both teams to score, all folds pooled (N = 10712; positive-class share 0.5429)

| Model | Log loss | 95% block CI | Brier | Accuracy | ECE (raw) | AUC |
|---|---|---|---|---|---|---|
| `always_yes` | 15.7862 | [15.4521, 16.1271] | 0.4571 | 0.5429 | 0.4571 | 0.5000 |
| `always_no` | 18.7526 | [18.4117, 19.0866] | 0.5429 | 0.4571 | 0.5429 | 0.5000 |
| `train_btts_rate` | 0.6891 | [0.6868, 0.6914] | 0.2480 | 0.5398 | 0.0152 | 0.5239 |
| `poisson_goals` | 0.6978 | [0.6946, 0.7009] | 0.2519 | 0.5245 | 0.0425 | 0.5326 |
| `dixon_coles_goals` | 0.6968 | [0.6935, 0.7000] | 0.2515 | 0.5289 | 0.0386 | 0.5326 |

Paired against `dixon_coles_goals`:

| Model | Delta log loss | 95% interval | Share of draws below 0 |
|---|---|---|---|
| `always_no` | +18.0558 | [+17.7155, +18.3910] | 0.000 |
| `always_yes` | +15.0894 | [+14.7565, +15.4294] | 0.000 |
| `poisson_goals` | +0.0010 | [+0.0007, +0.0013] | 0.000 |
| `train_btts_rate` | -0.0077 | [-0.0110, -0.0042] | 1.000 |

By competition: positive-class share and log loss of the constant-rate baseline vs the Dixon-Coles distribution.

| Competition | Positive share | constant rate | Dixon-Coles |
|---|---|---|---|
| `ENG1` | 0.530 | 0.6929 | 0.6955 |
| `ESP1` | 0.501 | 0.6933 | 0.6964 |
| `FRA1` | 0.557 | 0.6869 | 0.7011 |
| `GER1` | 0.597 | 0.6744 | 0.6650 |
| `ITA1` | 0.543 | 0.6931 | 0.6937 |
| `TUR1` | 0.567 | 0.6849 | 0.7117 |
| `UEFA_UCL` | 0.520 | 0.6947 | 0.7141 |
| `UEFA_UECL` | 0.526 | 0.6928 | 0.7279 |
| `UEFA_UEL` | 0.533 | 0.6922 | 0.7137 |

### Fit health (a fit that did not converge is used as-is by the existing models and is flagged here)

| Eval season | Elo optimizer | Dixon-Coles IPF converged | IPF final delta (tolerance 1e-6) | Eval rows with an unseen club | Max scoreline tail mass |
|---|---|---|---|---|---|
| 2020-21 | ok | False | 5.44e-04 | 1078 of 2576 | 0.0137 |
| 2021-22 | ok | False | 9.42e-05 | 464 of 2231 | 0.0096 |
| 2022-23 | NOT converged (Desired error not necessarily achi) | True | 1.00e-06 | 406 of 2233 | 0.0756 |
| 2023-24 | NOT converged (Desired error not necessarily achi) | True | 9.60e-07 | 316 of 2159 | 0.0051 |
| 2024-25 | ok | True | 9.50e-07 | 227 of 1513 | 0.0208 |

### Folds

| Eval season | Train seasons | Train matches | Eval matches | Fit cutoff (UTC) |
|---|---|---|---|---|
| 2020-21 | 2019-20 to 2019-20 | 1870 | 2576 | 2020-08-21T17:00:00+00:00 |
| 2021-22 | 2019-20 to 2020-21 | 4447 | 2231 | 2021-08-06T19:00:00+00:00 |
| 2022-23 | 2019-20 to 2021-22 | 6678 | 2233 | 2022-08-05T18:35:00+00:00 |
| 2023-24 | 2019-20 to 2022-23 | 8911 | 2159 | 2023-08-11T17:30:00+00:00 |
| 2024-25 | 2019-20 to 2023-24 | 11070 | 1513 | 2024-08-16T18:45:00+00:00 |



## Reading the numbers (what they do and do not say)
- Scores are pooled over folds and competitions; a pooled number hides league differences, so the per-competition table is part of the result. Intervals
  are ISO-week block bootstraps (matchweeks are not published); they describe sampling noise of this window, not future performance.
- Elo's edge is largest where clubs are least connected (Süper Lig -0.108, Conference League -0.135): the global Dixon-Coles gives unseen or thinly linked
  clubs league-average strength, Elo starts them at 1500 and learns within the season. This is evidence for league effects and a UEFA bridge, not yet a league model.
- Accuracy is secondary: on this set the Dixon-Coles totals reach 54.7% accuracy against 53.5% for "always over", yet lose on log loss to a constant rate.
- Everything is `BACKTEST_LIMITED`: result availability is inferred (kick-off + 3 h). The replay check found 0 violations at the t-24h stage in all folds;
  2 eval fixtures (2020-21 and 2021-22) carry only a date, so their stage cutoff is unreliable (flag, not removal).
- Süper Lig and the UEFA competitions are score-only and have thin history; nothing here supports a promotion decision.

## Handover to Sprint 3
- **Fold protocol:** `splits.make_folds` with `configs/league_models/benchmark.yaml` (changing it needs a new ADR); inner validation block via `splits.inner_split`
  (last train season of a fold); fold fixture-id hashes in `s2_protocol_manifest.json`.
- **Reference scores to beat (log loss, pooled, N = 10,712):** 1X2: Elo 0.9990 and Dixon-Coles 1.0230; over/under 2.5: constant rate 0.6898 (Dixon-Coles 0.6964);
  BTTS: constant rate 0.6891 (Dixon-Coles 0.6968). Per-competition and per-season values are in `s2_benchmark_metrics.json`.
- **Frozen:** 2,278 holdout fixtures (2025-26; Süper Lig 2024-25) listed by id hash only; 250 reserved (2026-27); 750 guard.
- **Open risks and blockers for Sprint 3:** unconverged early-fold fits; high unseen-club share; no neutral-venue handling for UEFA finals; Elo within-season
  update advantage; no odds and no match statistics in the licensed universe; kick-off times missing for some fixtures; only one Süper Lig dev fold;
  UEFA/domestic format versions remain `ASSISTANT_DECLARED_UNVERIFIED`; the 95 cached source files are archived on branch `league-source-snapshot` of the private data repository (verified by sha256 from a fresh clone), so exact reproduction no longer depends on the public repositories.

## What was NOT done
- No GBM, ensemble, calibration, league model, UEFA bridge, odds baseline, shadow or production wiring (later sprints or blocked).
- No re-score of the existing research benchmark, no Python 3.12 or cross-platform reproduction, no CI run of the real-data benchmark.
