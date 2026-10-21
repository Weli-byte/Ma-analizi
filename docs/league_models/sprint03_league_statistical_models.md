# Sprint 3: league statistical models and the UEFA bridge: report

Status: **Sprint 3 built and run on the real cached source files (2026-10-21).** Nothing is deployed, the global production model is untouched,
no candidate is promoted: promotion is Sprint 5. Plan: [`plan.md`](plan.md) (PDF pages 8 to 10), decisions: ADR 0065 (builds on ADR 0063 and 0064).
Everything below comes from `python -m src.league_models.stat_benchmark --as-of 2026-10-20T00:00:00Z` (command output in [`evidence/`](evidence/),
machine-readable results in the `s3_*.json` files next to this one, per-fixture predictions in
`artifacts/league_models/stat/s3_final/fold_predictions.jsonl`, git-ignored like every artifact).

## Headline findings (read these first)
1. **Same folds, same fixtures as Sprint 2.** The five expanding walk-forward folds (eval seasons 2020-21 to 2024-25, N = 10,712) are byte-identical to the
   committed Sprint 2 folds (the run refuses to start otherwise). The 2,278 holdout, 250 reserved and 750 guard fixtures are never read.
2. **The best candidate is the per-country time-aware Elo with UEFA-learned offsets (`E_elo_bridge`).** Pooled 1X2 log loss 0.9958 against 1.0102 for the
   global ridge Dixon-Coles reference `A_dc` (paired delta -0.0145, 95% interval -0.0200 to -0.0092). Against the Sprint 2 global Elo (`elo_s2`, 0.9990) it is
   better pooled by -0.0032 (95% interval -0.0060 to -0.0006), a small edge; per competition the interval excludes 0 only for the Bundesliga. So this is evidence for per-country
   scales plus a bridge, a modest improvement over the existing Elo, not a large proven win.
3. **The Poisson-family league adaptations did not win.** Competition terms (`B_dc_league`), a local fit per competition (`C_dc_local`) and the shrinkage blend
   (`D_blend`) are no better than the global `A_dc` (pooled deltas +0.0010, +0.0055, +0.0005). The local DC is significantly *worse* pooled (interval above 0): too little
   data per competition. The blend is well calibrated (raw ECE 0.0110 vs 0.0186) but not more accurate in log loss.
4. **A stronger ridge helps more than any league term.** `A_dc_shrink` (team ridge x4) beats `A_dc` pooled (-0.0032, interval -0.0046 to -0.0020, also on over/under and
   BTTS). The default ridge strength of `A_dc` is therefore not tuned; tuning it on the inner-validation block is a Sprint 4 task (not done here, no tuning claim is made).
5. **Decisions for the nine competitions** (dev out-of-sample evidence only): `E_elo_bridge` is a *candidate* for Premier League, Ligue 1, Serie A and Conference League;
   Bundesliga, La Liga, Süper Lig, Champions League and Europa League keep the global model. Local models are production-ineligible under 500 training matches
   (Conference League) and need out-of-sample proof between 500 and 1,500 (Ligue 1, Süper Lig, Champions League, Europa League).
6. **League hypotheses.** Serie A: the low-score correction helps (SUPPORTED). Ligue 1: stronger shrinkage helps (SUPPORTED). Conference League: the cross-league bridge helps
   (SUPPORTED). Premier League rest days, La Liga home advantage, Bundesliga goal density, Süper Lig home/away gap, Champions and Europa League bridge: INCONCLUSIVE. Süper Lig
   squad continuity: NOT_TESTABLE (no squad data in the licensed universe). No feature weight was fixed from any hypothesis.
7. **The UEFA bridge exists from real international history.** NOT_AVAILABLE in the first fold (118 international training matches, below the 200 minimum: global + competition
   prior, UNCERTAIN), AVAILABLE in the other four folds (449 to 1,668 international matches, 28 to 44 countries). The ordering is plausible (England, Portugal, Netherlands, Germany on top).
8. **Score matrices are valid.** Minimum cell probability above 0, row sums 1 to 4e-16 after normalisation, pre-normalisation sums 0.995 to 1.0, tail mass beyond 12 goals at most 0.0046.
   The 0-0 mass is over-predicted (7.2% predicted vs 6.4% observed), 1-1 slightly under-predicted (11.5% vs 11.9%).
9. **No fit failed silently.** All six global ridge fits of every fold converged (95 to 942 L-BFGS iterations, table below); the only NOT_TRAINED events are local fits with too little history
   (Ligue 1 and Europa League in the first fold, Conference League in the second), each replaced by an explicit, counted fallback.

## Acceptance criteria of the Sprint 3 prompt
| Criterion | Result | Evidence |
|---|---|---|
| A model / fallback decision and rationale for each of the nine competitions | DONE (dev evidence only) | `s3_league_decisions.json`, table below |
| Dixon-Coles and Elo outputs as-of consistent; valid score matrices | DONE: replay check 0 violations in all folds; matrix audit above; same-kick-off fixtures predicted before any is applied (test) | `s3_leakage_audit.json`, `s3_score_audit.json`, tests |
| UEFA common-strength bridge built only from real international history, or NOT_AVAILABLE | DONE: built from the real UEFA matches before each cutoff; NOT_AVAILABLE in fold 1 | `s3_uefa_bridge.json` |
| Global model not deleted or silently changed | DONE: no production file touched; `A_dc` is a new class, `elo_s2` is the existing `EloModel` unchanged | `git diff` of `src/models` is empty |
| Comparable out-of-sample artifact delivered to Sprint 4 on the same folds | DONE | handover section |

## What was built
- `src/league_models/stat_models.py`: `LeagueStatModel` interface, `RidgePoissonDC` (log-linear Poisson with ridge identifiability, optional competition terms, country levels
  = the bridge, rest covariate, time decay, Dixon-Coles rho inside its positivity interval with a negative mass rejecting the model), `TimeAwareElo` (per-country scales, season
  reversion to the country mean, promoted-club prior, goal-difference update, kick-off ordered replay with same-kick-off isolation, UEFA country offsets, per-club match counts and
  UNCERTAIN flags, ordinal-logit mapping fitted on the training replay only).
- `src/league_models/stat_benchmark.py`: the five candidates plus ablations on the Sprint 2 folds, the inner-validation blend weights, decisions, hypotheses, 0-0 / 1-1 audit,
  immutable run manifest and registry, `--dry-run`, `--verify`, `--reproduce`, `--compare-committed`.
- `configs/league_models/stat_models.yaml`: every hyper-parameter (engineering defaults, not tuned).
- `tests/test_league_models_s3.py`: 30 tests (positivity, finite probabilities, matrix consistency, rho interval and rejection, ridge shrinkage, parameter recovery, unseen clubs, country levels
  identified only by cross-country matches, rest-feature isolation, Elo reversion / promoted prior / same-kick-off / offsets, fail-closed paths, determinism, fallbacks, decisions).

## Results (all numbers copied from `s3_stat_metrics.json`, `s3_paired_deltas.json`, `s3_league_decisions.json`, `s3_hypotheses.json`, `s3_uefa_bridge.json`, `s3_score_audit.json`)

### 1X2, all folds pooled (N = 10712)

| Model | Log loss | 95% block CI | Brier | RPS | Accuracy | ECE (raw) | Paired delta vs `A_dc` (95% interval) |
|---|---|---|---|---|---|---|---|
| `A_dc` | 1.0102 | [1.0024, 1.0182] | 0.6033 | 0.2092 | 0.5039 | 0.0186 | reference |
| `A_dc_norho` | 1.0104 | [1.0024, 1.0185] | 0.6035 | 0.2092 | 0.5041 | 0.0219 | +0.0002 [-0.0005, +0.0008] |
| `A_dc_rest` | 1.0103 | [1.0024, 1.0183] | 0.6033 | 0.2092 | 0.5038 | 0.0182 | +0.0000 [-0.0000, +0.0001] |
| `A_dc_shrink` | 1.0070 | [0.9997, 1.0144] | 0.6018 | 0.2085 | 0.5045 | 0.0146 | -0.0032 [-0.0046, -0.0020] |
| `B_dc_league` | 1.0113 | [1.0026, 1.0196] | 0.6037 | 0.2094 | 0.5066 | 0.0152 | +0.0010 [-0.0006, +0.0029] |
| `B_dc_league_nohome` | 1.0107 | [1.0023, 1.0191] | 0.6033 | 0.2092 | 0.5048 | 0.0173 | +0.0004 [-0.0011, +0.0020] |
| `C_dc_local` | 1.0158 | [1.0075, 1.0240] | 0.6072 | 0.2111 | 0.4999 | 0.0157 | +0.0055 [+0.0026, +0.0089] |
| `D_blend` | 1.0108 | [1.0020, 1.0191] | 0.6034 | 0.2093 | 0.5076 | 0.0110 | +0.0005 [-0.0011, +0.0023] |
| `E_elo_bridge` | 0.9958 | [0.9877, 1.0039] | 0.5940 | 0.2045 | 0.5216 | 0.0087 | -0.0145 [-0.0200, -0.0092] |
| `F_elo_local` | 1.0014 | [0.9923, 1.0102] | 0.5978 | 0.2064 | 0.5152 | 0.0157 | -0.0089 [-0.0155, -0.0027] |
| `elo_s2` | 0.9990 | [0.9899, 1.0085] | 0.5960 | 0.2053 | 0.5194 | 0.0262 | -0.0112 [-0.0164, -0.0060] |
| `historical_prior` | 1.0725 | [1.0678, 1.0777] | 0.6492 | 0.2317 | 0.4308 | 0.0100 | +0.0623 [+0.0551, +0.0691] |

### Decision per competition (dev out-of-sample, final holdout untouched)

| Competition | Training matches (last fold) | Volume tier | Best candidate vs `A_dc` (log loss delta, 95% interval) | `E_elo_bridge` minus `elo_s2` | Decision |
|---|---|---|---|---|---|
| `ENG1` | 1900 | over 1500 (local candidates) | `E_elo_bridge` -0.0220 [-0.0382, -0.0062] | -0.0073 [-0.0155, +0.0006] | **CANDIDATE_E_elo_bridge** |
| `ESP1` | 1900 | over 1500 (local candidates) | `C_dc_local` -0.0023 [-0.0062, +0.0021] | -0.0016 [-0.0075, +0.0041] | **GLOBAL_A_dc** |
| `FRA1` | 1446 | 500 to 1500 (local candidate, proof required) | `E_elo_bridge` -0.0342 [-0.0524, -0.0176] | -0.0029 [-0.0073, +0.0020] | **CANDIDATE_E_elo_bridge** |
| `GER1` | 1530 | over 1500 (local candidates) | `E_elo_bridge` -0.0067 [-0.0214, +0.0077] | -0.0074 [-0.0145, -0.0011] | **GLOBAL_A_dc** |
| `ITA1` | 1900 | over 1500 (local candidates) | `E_elo_bridge` -0.0118 [-0.0219, -0.0013] | -0.0032 [-0.0098, +0.0029] | **CANDIDATE_E_elo_bridge** |
| `TUR1` | 726 | 500 to 1500 (local candidate, proof required) | `F_elo_local` -0.0126 [-0.0363, +0.0110] | -0.0056 [-0.0176, +0.0072] | **GLOBAL_A_dc** |
| `UEFA_UCL` | 619 | 500 to 1500 (local candidate, proof required) | `E_elo_bridge` -0.0036 [-0.0274, +0.0178] | -0.0031 [-0.0160, +0.0099] | **GLOBAL_A_dc** |
| `UEFA_UECL` | 423 | under 500 (local off) | `F_elo_local` -0.0595 [-0.1045, -0.0174] | -0.0020 [-0.0175, +0.0124] | **CANDIDATE_E_elo_bridge** |
| `UEFA_UEL` | 626 | 500 to 1500 (local candidate, proof required) | `E_elo_bridge` +0.0016 [-0.0206, +0.0241] | +0.0085 [-0.0017, +0.0190] | **GLOBAL_A_dc** |

### Over/under 2.5, all folds pooled (N = 10712; positive share 0.5347)

| Model | Log loss | Brier | Accuracy | ECE (raw) | AUC | Paired delta vs the Dixon-Coles distribution |
|---|---|---|---|---|---|---|
| `train_over_rate` | 0.6915 | 0.2492 | 0.5347 | 0.0092 | 0.4919 | +0.0008 [-0.0035, +0.0050] |
| `A_dc_totals` | 0.6907 | 0.2484 | 0.5563 | 0.0331 | 0.5625 | reference |
| `A_dc_shrink_totals` | 0.6881 | 0.2473 | 0.5559 | 0.0289 | 0.5634 | -0.0026 [-0.0033, -0.0020] |
| `B_dc_league_totals` | 0.6915 | 0.2487 | 0.5548 | 0.0324 | 0.5638 | +0.0008 [-0.0001, +0.0017] |
| `C_dc_local_totals` | 0.6935 | 0.2496 | 0.5497 | 0.0355 | 0.5592 | +0.0028 [+0.0010, +0.0049] |
| `D_blend_totals` | 0.6913 | 0.2486 | 0.5535 | 0.0323 | 0.5636 | +0.0006 [-0.0003, +0.0015] |

### Both teams to score, all folds pooled (N = 10712; positive share 0.5429)

| Model | Log loss | Brier | Accuracy | ECE (raw) | AUC | Paired delta vs the Dixon-Coles distribution |
|---|---|---|---|---|---|---|
| `train_btts_rate` | 0.6896 | 0.2482 | 0.5429 | 0.0057 | 0.4962 | -0.0026 [-0.0052, +0.0002] |
| `A_dc_goals` | 0.6922 | 0.2494 | 0.5407 | 0.0248 | 0.5358 | reference |
| `A_dc_shrink_goals` | 0.6905 | 0.2486 | 0.5411 | 0.0224 | 0.5356 | -0.0017 [-0.0023, -0.0010] |
| `B_dc_league_goals` | 0.6933 | 0.2499 | 0.5445 | 0.0299 | 0.5365 | +0.0011 [+0.0003, +0.0019] |
| `C_dc_local_goals` | 0.6942 | 0.2503 | 0.5386 | 0.0304 | 0.5344 | +0.0020 [+0.0005, +0.0036] |
| `D_blend_goals` | 0.6932 | 0.2498 | 0.5429 | 0.0275 | 0.5362 | +0.0010 [+0.0001, +0.0018] |

### League hypotheses (ablation on dev out-of-sample rows; variant minus base log loss, negative = the hypothesis helps)

| Hypothesis | Competition | N | Delta (95% interval) | Verdict |
|---|---|---|---|---|
| Serie A: the Dixon-Coles low-score correction (rho) improves 1X2 | `ITA1` | 1890 | -0.0029 [-0.0046, -0.0015] | SUPPORTED |
| Premier League: rest days before a match carry information | `ENG1` | 1520 | -0.0000 [-0.0002, +0.0002] | INCONCLUSIVE |
| La Liga: a competition-specific home advantage improves 1X2 | `ESP1` | 1520 | -0.0024 [-0.0057, +0.0009] | INCONCLUSIVE |
| Bundesliga: a competition goal-rate intercept improves over/under 2.5 | `GER1` | 1530 | +0.0007 [-0.0006, +0.0021] | INCONCLUSIVE |
| Ligue 1: stronger shrinkage of club strengths improves 1X2 | `FRA1` | 1752 | -0.0090 [-0.0136, -0.0047] | SUPPORTED |
| Süper Lig: a competition-specific home advantage improves 1X2 | `TUR1` | 420 | +0.0016 [-0.0028, +0.0061] | INCONCLUSIVE |
| Champions League: competition + country levels (bridge) improve 1X2 | `UEFA_UCL` | 689 | +0.0022 [-0.0067, +0.0114] | INCONCLUSIVE |
| Europa League: competition + country levels (bridge) improve 1X2 | `UEFA_UEL` | 815 | +0.0049 [-0.0057, +0.0165] | INCONCLUSIVE |
| Conference League: competition + country levels (bridge) improve 1X2 | `UEFA_UECL` | 576 | -0.0217 [-0.0387, -0.0042] | SUPPORTED |
| Süper Lig: squad continuity matters | `TUR1` | - | - | NOT_TESTABLE (the licensed universe has no squad, transfer or lineup data) |

### UEFA bridge (country levels of `B_dc_league`)

| Eval season | Status | International training matches | Countries seen | Strongest (net level) | Weakest |
|---|---|---|---|---|---|
| 2020-21 | NOT_AVAILABLE | 118 | 16 | - | - |
| 2021-22 | AVAILABLE | 449 | 28 | ENG +0.74, POR +0.70, GER +0.56 | TUR -0.56, AZE -0.68 |
| 2022-23 | AVAILABLE | 854 | 37 | NED +0.89, ENG +0.88, POR +0.87 | ARM -1.05, GIB -1.18 |
| 2023-24 | AVAILABLE | 1261 | 41 | ENG +0.91, POR +0.88, NED +0.88 | LVA -0.95, GIB -1.04 |
| 2024-25 | AVAILABLE | 1668 | 44 | ENG +0.97, POR +0.90, NED +0.85 | LVA -0.90, GIB -0.95 |

### Score matrix audit (all folds pooled)

| Model | P(0-0) predicted / observed | P(1-1) predicted / observed | Most-likely-score hit rate | Min cell | Max tail mass outside grid | Pre-normalisation sum range |
|---|---|---|---|---|---|---|
| `A_dc` | 0.0723 / 0.0640 | 0.1151 / 0.1187 | 0.1244 | 1.0e-23 | 0.0029 | [0.9971, 1.0000] |
| `B_dc_league` | 0.0727 / 0.0640 | 0.1146 / 0.1187 | 0.1230 | 6.1e-26 | 0.0046 | [0.9954, 1.0000] |
| `C_dc_local` | 0.0739 / 0.0640 | 0.1155 / 0.1187 | 0.1210 | 1.0e-23 | 0.0197 | [0.9803, 1.0000] |
| `D_blend` | 0.0733 / 0.0640 | 0.1151 / 0.1187 | 0.1238 | 2.9e-22 | 0.0197 | [0.9984, 1.0000] |

### Fit health and fallbacks per fold

| Eval season | Ridge DC fits converged | Explicit fallbacks (NOT_TRAINED local fits) |
|---|---|---|
| 2020-21 | 6 of 6 | C_dc_local:FRA1, C_dc_local:UEFA_UEL, F_elo_local:FRA1, F_elo_local:UEFA_UEL |
| 2021-22 | 6 of 6 | C_dc_local:UEFA_UECL, F_elo_local:UEFA_UECL |
| 2022-23 | 6 of 6 | none |
| 2023-24 | 6 of 6 | none |
| 2024-25 | 6 of 6 | none |

## Reading the numbers (what they do and do not say)
- Everything is score-only, `BACKTEST_LIMITED` and dev out-of-sample: the frozen holdout was not used for fitting, tuning, blend weights, selection or any threshold. A "CANDIDATE"
  decision means "worth carrying into Sprints 4 and 5", never "approved".
- The decision rule is fixed in `stat_models.yaml` and in `league_decisions.json`: paired log-loss interval entirely below 0 versus `A_dc`, Brier better, raw ECE not worse by more than 0.01,
  the volume tier allowing it, no fold fallback. Nine competitions with block-bootstrap intervals invite chance findings; the Premier League, Ligue 1, Serie A and Conference League
  candidates all share one model (`E_elo_bridge`), which is consistent with a real pooled effect rather than four independent wins.
- `E_elo_bridge` and `elo_s2` differ in per-country scales, season reversion, promoted prior, goal-difference updates and the bridge; the ablation of each part was not run in this sprint, so
  the gain cannot be attributed to a single component.
- Poisson-family candidates have no per-competition advantage here; the one robust Poisson-side result is that stronger shrinkage is better than the default, which says the default
  `l2_team` is too weak, not that league terms are useless.
- Süper Lig has one dev fold (420 matches); Conference League 423 training matches in its last fold: both are far below what a local model needs.
- The first fold (2020-21) trains on one season only; its bridge is NOT_AVAILABLE and its Elo has many UNCERTAIN clubs. Pooled scores include it.

## Reproducibility
`python -m src.league_models.stat_benchmark --as-of 2026-10-20T00:00:00Z --output-dir artifacts/league_models/stat/s3_final` takes about three minutes. `--verify DIR` re-hashes a
run, `--reproduce DIR` re-runs and compares every output hash, `--compare-committed docs/league_models` compares with the committed `s3_*` files (exact for structural files, numeric within
1e-3 for metrics). Measured: `--verify` of the final run 11 of 11 files match; `--reproduce` under Python 3.14.6 `REPRODUCED` (11 of 11, `evidence/s3_reproduce_python314.json`) and under Python 3.12.14 `REPRODUCED` (11 of 11, `evidence/s3_reproduce_python312.json`), both on Windows. The Linux CI job (Ubuntu 24.04, Python 3.12, archived snapshot) re-ran Sprint 2 and Sprint 3 and passed the comparison with the committed files: the protocol manifest byte-identical, text of decisions and verdicts identical, numbers within 1e-3 (0.01 for the bootstrap draw share and binned ECE). The first Linux runs showed last-digit float differences and one ECE bin-edge crossing, which is why byte-identity is not demanded across operating systems. Seeds: none needed (L-BFGS-B and BFGS are deterministic), bootstrap seed in `benchmark.yaml`.

## Handover to Sprint 4
- **Folds and universe:** identical to Sprint 2 (`s3_protocol_manifest.json` carries the parity check); inner validation block via `splits.inner_split`; the frozen holdout stays frozen.
- **Reference scores to beat (pooled 1X2 log loss, N = 10,712):** `E_elo_bridge` 0.9958, `elo_s2` 0.9990, `A_dc` 1.0102 (and `A_dc_shrink` 1.0070 as the better-regularised Poisson reference);
  over/under 2.5: `A_dc_shrink_totals` 0.6881, `A_dc_totals` 0.6907, constant rate 0.6915; BTTS: `A_dc_shrink_goals` 0.6905, constant rate 0.6896, `A_dc_goals` 0.6922.
- **Per-fold out-of-sample predictions** with provenance labels: `fold_predictions.jsonl` (1X2, over, BTTS, P(0-0), P(1-1), most likely score per model).
- **Open items for Sprint 4:** tune the ridge strength and Elo constants on the inner-validation block only; ablate the parts of the Elo bridge; test GBM adapters against both references;
  UEFA finals still use a home advantage; clubs unseen in training (2,491 eval rows) are predicted at league-average / country level.

## What was NOT done
- No GBM, calibration, isotonic/temperature step, ensemble, approval gate, shadow or production wiring (Sprints 4 to 6).
- No tuning of ridge strengths, Elo constants or blend grid on any data; the defaults are engineering choices recorded in the config.
- No standalone UEFA GBM (the plan forbids it); no xG, shots, odds, lineups or squad features (not in the licensed universe).
- No cross-operating-system claim beyond what the CI job and the local reproductions in `evidence/` show.
