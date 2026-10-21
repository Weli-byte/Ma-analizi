# ADR 0067 - League-model programme, Sprint 5: calibration, the one-shot frozen holdout and the approval gate

Date: 2026-10-21. Status: accepted; Sprint 5 built and run on the real cached source files. Sprint 6 not started.
Plan: `docs/league_models/plan.md` (PDF pages 13 to 16). Report: `docs/league_models/sprint05_calibration_and_gate.md`. Builds on ADR 0063 to 0066.

## Decision
Sprint 5 calibrates the Sprint 2 / 3 / 4 probabilities on out-of-fold development predictions, measures league-aware candidates against the global references on the frozen holdout
exactly once, and turns the promotion decision into a pre-declared, auditable policy. Nothing is deployed; an approved model can only be selected for canary / shadow (production fraction 0.0).

1. **Roles and windows** (`s5_split_manifest.json`). TRAIN: the development seasons before the evaluated season (expanding, the Sprint 2 folds). INNER VALIDATION: the last training season (tuning,
   early stopping, blend weights). OOF CALIBRATION / SELECTION: the development folds' out-of-fold predictions: the calibrator method is chosen by inner walk-forward (fit on folds before k, score fold k) and
   the final calibrator is fitted on all of them. FROZEN HOLDOUT: 2025-26 (Süper Lig 2024-25), 2,278 fixtures, disjoint from every other role. Documented exception: for the final refit the development fixtures
   are training rows while their out-of-fold predictions were used for calibration (cross-fitting). A calibrator never sees a prediction made by a model that was trained on that fixture; a refit is needed
   because Elo-type models are stale after a skipped season. Earlier sprints never read the holdout (the Sprint 2 `Universe` carries it by id only), so it is still untouched.
2. **Calibrators** (`calibrators.py`): `none`, `temperature`, `affine`, `hier` (affine plus a ridge-penalised league offset: the hierarchical calibrator that replaces a per-league isotonic fit on little data), and
   global `isotonic` for binary targets. All work in log-odds space, so a 1X2 row stays on the simplex and a binary probability stays in (0, 1); malformed input raises; a degenerate fit (one class, too few rows,
   non-finite) is recorded `NOT_FITTED_*` and applies the identity. Per model and target the method is chosen by mean walk-forward log loss and only leaves `none` when it gains at least 0.0005; ECE is
   reported, never used to select. The baselines are calibrated by the same procedure as the candidates.
3. **Pre-registration and the lock** (`holdout_lock.py`). The `dev` stage writes `s5_preregistration.json`: policy and configuration hashes, snapshot id, calibration choices, the screened candidate per league x target
   cell and the holdout listing hash. The `holdout` stage re-runs the dev stage, refuses to go on unless it reproduces the committed pre-registration, requires a clean working tree, writes the access to an append-only
   log BEFORE any score is returned, and refuses any second access (`s5_holdout_access_log.json`). A `--rehearsal` runs the identical code on a pseudo-holdout (the last development season) with its own log, so the real
   holdout is never needed to debug the code.
4. **Dev screen.** In each cell the candidates whose calibrated walk-forward log loss is below EVERY reference are screened; the best one is the single candidate confirmed on the holdout. This keeps the family of
   confirmation tests small and fixed in advance. A cell with no such candidate stays on the global model (`GLOBAL_KEPT`) and is not tested for approval (it is still reported descriptively).
5. **References.** A candidate must beat every reference of its target: 1X2 the production Elo (`elo_s2`), the tuned league-blind GBM (the Sprint 4 control) and the tuned global Dixon-Coles; over/under and BTTS the
   production `dixon_coles` totals / goals, the same two controls. The train-rate baseline is reported next to them.
6. **Confirmation test and gate** (`approval_gate.py`, `configs/league_models/approval_policy.yaml`). Paired block bootstrap (iso-week blocks, 10,000 draws), one-sided, p = share of draws with delta >= 0, the largest
   p over the references (an intersection test), Holm across the screened cells at family-wise 5%. Statuses: `APPROVED_FOR_CANARY` (every gate passes and the Holm-adjusted test is passed against every reference),
   `SHADOW_INCONCLUSIVE` (the interval includes 0, too few rows, or a local tier below its proof level), `REJECTED` (significantly worse than a reference, a material calibration or latency regression, an invalid artifact),
   `BLOCKED` (no data, coverage under 95%, revoked licence, broken lineage), `GLOBAL_KEPT`. Accuracy is reported and can never open the gate. The thresholds are a product decision, not a mathematical guarantee.
7. **Router contract** (`prediction_router.py`): modes GLOBAL / LEAGUE_ADAPTED / LOCAL / BLENDED, selection only (it never predicts), production use always routes to GLOBAL until Sprint 6, only an `APPROVED_FOR_CANARY` entry
   is selectable for canary and (with `SHADOW_INCONCLUSIVE`) shadow, an artifact that is missing or does not match its hash falls back to GLOBAL with an alarm, a broken global model raises (no invented prediction),
   forcing an unapproved model raises.
8. **Registry and artifacts.** GBM models have booster files with real hashes; Elo / Dixon-Coles models are deterministic refits, registered as recipe artifacts (canonical JSON with a real hash). Nothing is registered
   that was not produced.
9. **Reporting.** Won / lost / insufficient-evidence matrix for the 27 cells, paired deltas with intervals, effective N (rows and iso-week blocks), reliability bins, confusion matrices, raw and calibrated metrics, latency
   probe, base rates. The 68.8% always-over figure of the 138-match report is NOT_VERIFIABLE (those matches are not in the licensed universe); the market-odds baseline is NOT_AVAILABLE (no licensed timestamped odds).

## CI
`.github/workflows/league-benchmark.yml` re-runs the Sprint 5 development stage on the archived snapshot and compares it with the committed `s5_*` files. The holdout stage is never run in CI (a second read is forbidden);
its committed report is verified by hash only.

## Consequences and limits
- Multiple comparisons: 27 cells x several candidates; only one candidate per cell is confirmed and Holm is applied over the confirmed cells, but a nominal difference is not read as a win.
- Cells without enough development history (the Süper Lig has 420 development evaluation rows, all in the first fold) cannot be screened and stay on the global model.
- Refit models see 2024-25, the calibrators were learned from predictions of models trained on less data: a small, documented mismatch.
- Latency is a representative probe (default-parameter models on the last development fold), not a production measurement.
- Score-only and `BACKTEST_LIMITED`; odds-free.

## Rollback
Delete `src/league_models/{calibrators,approval_gate,prediction_router,holdout_lock,s5_benchmark}.py`, `configs/league_models/approval_policy.yaml`, `tests/test_league_models_s5.py`, the `docs/league_models/s5_*`
files, the pyproject ignores and the Sprint 5 steps of the workflow. The holdout access log cannot be rolled back: a consumed holdout stays consumed. Nothing in production imports these modules.
