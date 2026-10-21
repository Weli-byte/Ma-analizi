# Sprint 5: calibration, the one-shot frozen holdout and the approval gate: report

Status: **Sprint 5 built and run on the real cached source files (2026-10-21). The frozen holdout was read exactly once (2026-10-21 16:17 UTC) and is now consumed.**
Nothing is deployed, the global production model is untouched. **No league-aware model is approved: every league and target stays on the global model.** Plan:
[`plan.md`](plan.md) (PDF pages 13 to 16), decisions: ADR 0067 (builds on ADR 0063 to 0066). Everything below comes from `python -m src.league_models.s5_benchmark`
(`--stage dev`, then `--stage holdout` once; command output in [`evidence/`](evidence/), machine-readable results in the `s5_*` files next to this one).

## Headline findings (read these first)
1. **The holdout was evaluated once and the policy was fixed before it.** The pre-registration (`s5_preregistration.json`: policy and configuration hashes, snapshot id, calibrator choices, the candidate screened
   for each cell, the holdout listing hash) was committed and pushed (commit `c6a12ced`) before the holdout run; the holdout run re-produced it bit for bit (sha256 `e72f92de...`), required a clean tree, and wrote
   the access log (`s5_holdout_access_log.json`, one `CONSUMED` entry: 2,278 fixtures, listing hash `d1821171...`) before any score was returned. A second read is refused by the code. No threshold, model or
   weight was changed after seeing a holdout number; a pseudo-holdout rehearsal (the last development season, own log) was used to debug the code, and it led to one policy change (below), made before the real read.
2. **Final decisions (27 league x target cells): 0 approved for canary, 4 shadow / inconclusive, 2 rejected, 5 blocked, 16 global kept.** The dev screen sent 11 cells to the confirmation test (one candidate per cell,
   beating every reference on calibrated walk-forward log loss); 6 of them had holdout rows. None passed: the interval includes 0 against at least one reference in 4 cells, the candidate is significantly worse in 2 cells
   (Premier League over/under, Ligue 1 BTTS), and 5 cells (Europa League, Conference League) are blocked because the source has no 2025-26 file for those competitions (`BLOCKED_NO_DATA`, Sprint 1).
3. **On the pooled holdout the global models are as good as, or better than, every league-aware model.** 1X2 (N = 2,278, calibrated): production Elo `elo_s2` 0.9894, Sprint 3 Elo bridge `E_elo_bridge` 0.9912, tuned
   league-blind GBM 0.9922, league-aware `B_gbm_league` 0.9938, `D_gbm_blend` 0.9930, local `C_gbm_local` 1.0219. Over/under: production `dixon_coles` 0.6805, tuned DC 0.6799, `B_gbm_league` 0.6816. BTTS: production
   `dixon_coles` 0.6867, `B_gbm_league` 0.6901, base rate 0.6891. The three cells that looked promising in Sprint 4 (La Liga BTTS, Bundesliga BTTS and over/under) were not confirmed: they were not selected by the dev
   screen under calibration (the calibrated Dixon-Coles baseline closed the gap), which is what the multiple-comparison warning of Sprint 4 predicted.
4. **Calibration helps the production baselines, which makes the bar higher.** Walk-forward selection chose a calibrator for 14 of 27 model-target pairs (13 stayed `none`). The production Elo's 1X2 ECE falls from 0.034 to
   0.020 and its log loss from 0.9931 to 0.9894 (hierarchical temperature); the production Dixon-Coles totals / goals improve from 0.6824 to 0.6805 and 0.6879 to 0.6867. The boosted trees were already well calibrated
   (`none` chosen). Isotonic was chosen once and never beat a smooth method by much; per-league isotonic is not used.
5. **The won / lost matrix has no win.** For the 21 league-target cells with holdout rows (five domestic leagues, Champions League, Süper Lig; the Europa and Conference League have no 2025-26 fixtures): 0 WON, 3 LOST
   (candidate significantly worse than a reference), 15 INSUFFICIENT_EVIDENCE, 3 NO_DATA (Süper Lig, whose 338 holdout matches cannot be assigned a candidate because it has only 420 development evaluation rows in one fold).
6. **Accuracy never moves a decision.** Holdout accuracy of every model is 0.498 to 0.526 (1X2) and within 0.015 of the majority class for over/under (0.5439) and BTTS (0.5448); the gate does not use it.
7. **The 68.8% "always over" figure cannot be re-verified.** The 138-match report is not in the licensed universe. On the 2,278 holdout fixtures the over 2.5 rate is 0.5439 and BTTS yes 0.5448. A market-odds baseline is
   NOT_AVAILABLE (no licensed timestamped odds).

## Acceptance criteria of the Sprint 5 prompt
| Criterion | Result | Evidence |
|---|---|---|
| Real paired delta and uncertainty per league / target | DONE: 10,000-draw iso-week block bootstrap, every confirmed cell against every reference; descriptive deltas for all 21 cells with holdout rows | `s5_approval_decisions.json`, `s5_results_matrix.json` |
| Holdout evaluated once and not leaked into tuning | DONE: pre-registration committed first, access log with one entry, lock refuses a second read, tests | `s5_holdout_access_log.json`, `s5_preregistration.json`, `tests/test_league_models_s5.py` |
| Gate closes failing models; global fallback safe | DONE: REJECTED / BLOCKED / SHADOW statuses, router falls back to GLOBAL with a reason and an alarm, raises if the global model is broken | `s5_approval_decisions.json`, `s5_router_dry_run.json` |
| Approved models only APPROVED_FOR_CANARY; no rollout | DONE (vacuously: none approved; production fraction 0.0; nothing changed for users) | `configs/league_models/approval_policy.yaml` |
| Registry and live-monitoring contract handed to Sprint 6 | DONE | `s5_model_registry.json`, `s5_monitoring_contract.json`, `s5_rollback_plan.md` |

## What was built
- `src/league_models/calibrators.py`: `none` / `temperature` / `affine` / hierarchical (league offset with a ridge) / global isotonic calibrators in log-odds space, simplex and bounds checks, explicit `NOT_FITTED_*`
  fallback to the identity, inner walk-forward selection.
- `src/league_models/holdout_lock.py`: the single door to the frozen holdout (append-only log written before the read, one read, clean tree, listing must match).
- `src/league_models/approval_gate.py` + `configs/league_models/approval_policy.yaml`: the pre-declared policy, Holm adjustment, statuses.
- `src/league_models/prediction_router.py`: the router contract (GLOBAL / LEAGUE_ADAPTED / LOCAL / BLENDED), artifact validation, dry run.
- `src/league_models/s5_benchmark.py`: dev stage, pre-registration, holdout stage, rehearsal, reports, registry, CLI (`--verify`, `--compare-committed`).
- `tests/test_league_models_s5.py` (26 tests), the CI step, ADR 0067.

## Results (numbers copied from `s5_holdout_report.json`; calibrated = after the frozen calibrators)

### 1X2, frozen holdout (N = 2,278: 2025-26 for ENG1, ESP1, FRA1, GER1, ITA1, UEFA_UCL; Süper Lig 2024-25)
| Model | Log loss raw | Log loss calibrated | Brier calibrated | Accuracy | ECE raw | ECE calibrated |
|---|---|---|---|---|---|---|
| `elo_s2` (production Elo, reference) | 0.9931 | 0.9894 | 0.5891 | 0.5224 | 0.0341 | 0.0198 |
| `E_elo_bridge` (Sprint 3) | 0.9912 | 0.9912 | 0.5903 | 0.5193 | 0.0141 | 0.0141 |
| `A_gbm_tuned` (tuned league-blind control, reference) | 0.9922 | 0.9922 | 0.5906 | 0.5263 | 0.0170 | 0.0170 |
| `D_gbm_blend` | 0.9930 | 0.9930 | 0.5911 | 0.5219 | 0.0195 | 0.0195 |
| `B_gbm_league` | 0.9938 | 0.9938 | 0.5915 | 0.5224 | 0.0167 | 0.0167 |
| `B_dc_league` | 1.0025 | 1.0030 | 0.5989 | 0.5119 | 0.0177 | 0.0193 |
| `D_blend` | 1.0025 | 1.0025 | 0.5985 | 0.5070 | 0.0180 | 0.0180 |
| `A_dc_ridge_tuned` (reference) | 1.0078 | 1.0078 | 0.6023 | 0.5026 | 0.0174 | 0.0174 |
| `C_gbm_local` | 1.0219 | 1.0219 | 0.6099 | 0.4982 | 0.0232 | 0.0232 |

### Over/under 2.5 and BTTS, frozen holdout (base rates 0.5439 and 0.5448)
| Target | Model | Log loss raw | Log loss calibrated | Accuracy | ECE calibrated |
|---|---|---|---|---|---|
| Over/under | `A_dc_ridge_tuned_totals` (reference) | 0.6810 | 0.6799 | 0.5540 | 0.0143 |
| Over/under | `dixon_coles_totals` (production, reference) | 0.6824 | 0.6805 | 0.5584 | 0.0189 |
| Over/under | `A_gbm_tuned_totals` (reference) | 0.6812 | 0.6812 | 0.5514 | 0.0185 |
| Over/under | `B_gbm_league_totals` | 0.6816 | 0.6816 | 0.5562 | 0.0170 |
| Over/under | `train_over_rate` | 0.6894 | 0.6854 | 0.5439 | 0.0252 |
| BTTS | `dixon_coles_goals` (production, reference) | 0.6879 | 0.6867 | 0.5430 | 0.0247 |
| BTTS | `A_dc_ridge_tuned_goals` (reference) | 0.6879 | 0.6869 | 0.5448 | 0.0104 |
| BTTS | `A_gbm_tuned_goals` (reference) | 0.6877 | 0.6883 | 0.5448 | 0.0020 |
| BTTS | `B_gbm_league_goals` | 0.6901 | 0.6901 | 0.5347 | 0.0252 |
| BTTS | `train_btts_rate` | 0.6891 | 0.6891 | 0.5448 | 0.0001 |

### Gate decisions of the cells that were confirmed on the holdout (`s5_approval_decisions.json`)
| Cell | Candidate | Holdout rows | Delta log loss vs production reference [95% interval] | Status | Reason |
|---|---|---|---|---|---|
| Premier League 1X2 | `E_elo_bridge` | 380 | `elo_s2` -0.0049 [-0.0172, +0.0076] | SHADOW_INCONCLUSIVE | interval includes 0 against two references |
| Ligue 1 1X2 | `E_elo_bridge` | 305 | `elo_s2` +0.0063 [-0.0060, +0.0188] | SHADOW_INCONCLUSIVE | includes 0, Brier not better |
| Serie A 1X2 | `B_gbm_league` | 380 | `elo_s2` +0.0079 [-0.0104, +0.0250] | SHADOW_INCONCLUSIVE | includes 0, Brier not better |
| La Liga over/under | `B_dc_league` totals | 380 | `dixon_coles` -0.0005 [-0.0019, +0.0009] | SHADOW_INCONCLUSIVE | includes 0 |
| Premier League over/under | `B_gbm_league` totals | 380 | `dixon_coles` +0.0127 [0.0000, +0.0263] | REJECTED | significantly worse than two references |
| Ligue 1 BTTS | `B_gbm_league` goals | 305 | `dixon_coles` +0.0113 [-0.0006, +0.0246] | REJECTED | significantly worse than the tuned DC; ECE worse than the tuned control |
| Europa League (1X2, BTTS, over/under), Conference League (BTTS, over/under) | various | 0 | n/a | BLOCKED | no 2025-26 fixtures in the licensed source |
Holm family size 6 (the confirmed cells with rows), family-wise alpha 0.05; no cell had a p-value that survives it (the smallest maximum-over-references p is 0.22).

### Calibration (`s5_calibration_manifest.json`)
- Inner walk-forward over the five development folds, first scored fold 2022-23 (fit on the folds before, score the next); methods: none, temperature, affine, hierarchical, and isotonic for binary targets.
- Chosen: none 13, hierarchical 8, affine 5, isotonic 1 (27 model-target pairs). Examples of the walk-forward mean log loss (1X2 `elo_s2`: none 0.9860, hierarchical 0.9851 chosen; `B_gbm_league`: none 0.9839 chosen;
  over/under `dixon_coles`: none 0.6946, hierarchical 0.6844 chosen). A method must beat `none` by 0.0005 to be used. ECE is reported, never used to choose.
- A league has few rows; the hierarchical calibrator keeps a small league near the global fit (ridge on the league offset). No per-league isotonic was used.

### Latency (`s5_holdout_report.json`)
Representative probe (default-parameter models on the last development fold, prediction only): Elo 0.005 ms, tuned DC 0.003 ms, one tuned GBM (three targets) 0.023 ms, the GBM blend 0.045 ms per fixture. The 100 ms policy ceiling
was never close.

## Policy change made before the real holdout read (rehearsal)
The rehearsal on the pseudo-holdout showed that the pooled-ECE regression margin of 0.01 rejected models on estimation noise (an ECE over about 1,500 rows moves by roughly 0.01 between near-identical models). The margin was
raised to 0.02 in the policy, committed and fingerprinted into the pre-registration before the holdout was read. It is recorded here because it was informed by a rehearsal, not by a holdout number.

## Honest limits
- The holdout is one season (2025-26; Süper Lig 2024-25), 2,278 matches; a cell has 189 to 380 rows. Intervals are wide; "inconclusive" is the honest verdict for most cells and not evidence of equality.
- The Europa League and Conference League have no 2025-26 file in the licensed source (they stay global by default, not by evidence); the Süper Lig had too little development history to be screened.
- `results_matrix.json` carries the sentence "27 cells are shown" in its definition string; the file holds the 21 league-target cells that have holdout rows (the code string was corrected afterwards, the immutable
  holdout files were not rewritten).
- The development fixtures are training rows of the final refit and out-of-fold calibration rows (cross-fitting, documented in ADR 0067); the calibrators come from models trained on less data than the final ones.
- Latency is a probe, not a production measurement. Everything is score-only, odds-free and `BACKTEST_LIMITED`.
- The thresholds of the gate are a product decision, not a mathematical guarantee.

## Handover to Sprint 6
- `s5_model_registry.json` (15 entries: the production baselines as GLOBAL, the screened candidates with their gate status and artifact hashes), `s5_router_dry_run.json` (production always GLOBAL; canary / shadow selects only
  eligible entries; no alarm in the dry run), `s5_monitoring_contract.json` (what the live monitor records and alarms on), `s5_rollback_plan.md`.
- No model holds `APPROVED_FOR_CANARY`, so a Sprint 6 canary has nothing to roll out. The four `SHADOW_INCONCLUSIVE` entries (Premier League and Ligue 1 `E_elo_bridge`, Serie A `B_gbm_league`, La Liga over/under
  `B_dc_league`) can be observed in shadow mode on real pre-match records to collect the prospective evidence this holdout could not give; the global models keep serving. The holdout cannot be used again: new evidence
  needs new data (the running 2026-27 season is reserved for exactly that).

## Commands and checks
```
python -m src.league_models.s5_benchmark --as-of 2026-10-20T00:00:00Z --stage dev --output-dir artifacts/league_models/s5/dev --docs-dir docs/league_models
python -m src.league_models.s5_benchmark --as-of 2026-10-20T00:00:00Z --stage holdout --rehearsal --output-dir artifacts/league_models/s5/rehearsal      # pseudo-holdout, own log
python -m src.league_models.s5_benchmark --as-of 2026-10-20T00:00:00Z --stage holdout --output-dir artifacts/league_models/s5/final --docs-dir docs/league_models   # ONCE (done)
python -m src.league_models.s5_benchmark --verify artifacts/league_models/s5/final
```
- Local checks (2026-10-21): `ruff check .` clean; the Sprint 5 tests: 26 passed; `--verify` of the final run 14 of 14 files; the dev stage under Python 3.12.14 reproduced the pre-registration hash `e72f92de...` and passed `--compare-committed` (`evidence/s5_cross_python_check.json`).
- CI: CI_STATUS
