# ADR 0064 - League-model programme, Sprint 2: global reference benchmark

Date: 2026-10-20. Status: accepted; Sprint 2 built and run on the real cached source files. Sprints 3 to 6 not started.
Plan: `docs/league_models/plan.md` (PDF pages 5 to 7). Report: `docs/league_models/sprint02_global_benchmark.md`. Builds on ADR 0063.

## Decision
Sprint 2 freezes a **global reference** and measures every later league model against it on the same fixtures and the
same time windows. It trains nothing new for production and changes no existing model: the production model classes
(`src/models`) are only instantiated with the hyper-parameters of `configs/model.yaml` and fitted on the licence-clean
universe of Sprint 1.

1. **One test universe.** Finished fixtures of the licence-clean universe (openfootball scores; the production-training licence
   gate runs first and fails closed). Every (competition, season) has exactly one role: `DEV` (folds), `HOLDOUT`
   (frozen), `RESERVED` (running season 2026-27, kept for prospective checks), `GUARD` (the seasons that
   `configs/evaluation.yaml` locks as final test for the research benchmark of EPL and La Liga), `BLOCKED`. A fixture is in one role,
   so it can never fall into two splits. Holdout, reserved and guard fixtures are carried by id only: the benchmark code has no
   attribute that holds their scores.
2. **Holdout (ADR 0063 decision 2):** 2025-26 for every league that has it; Süper Lig 2024-25. Never read for fitting, tuning,
   calibration, selection or weights; the one-shot evaluation is Sprint 5.
3. **Expanding season-by-season walk-forward** over DEV rows. Train = DEV rows of earlier seasons whose inferred
   availability (kick-off + 3 h) is at or before the earliest kick-off of the eval season (an embargo); eval = every DEV
   row of that season, all competitions together. A fold with fewer than `min_train_matches` (500) is refused, not run.
   The last train season of a fold is reserved as the inner-validation block for tuning candidates (Sprint 3+); the eval
   season is never part of it. Calibration is not fitted in Sprint 2 (raw reliability diagnostics only).
4. **Targets and baselines** (all from one goal distribution where a model has one): 1X2 (HOME/DRAW/AWAY): always-home, per-league
   prior, Elo, Poisson, Dixon-Coles. 2.5 over/under: always-over, always-under, train over-rate, Poisson and Dixon-Coles
   totals. BTTS: always-yes, always-no, train rate, Poisson and Dixon-Coles goals. Elo has no goal distribution, so it has no
   totals/BTTS entry. The degenerate hard guesses (p = 1 / 0) are kept on purpose as reference points and are the only
   baselines that hit the log-loss clip.
5. **Not run, and why (recorded, never faked):** market-favourite and any odds baseline `UNAVAILABLE` (the licensed
   universe has no odds); XGBoost / LightGBM, the global binary classifier and the production ensemble
   `NOT_RUN_FEATURES_BLOCKED` (their feature pipeline reads the football-data.co.uk dataset, BLOCKED for league models). The existing
   research benchmark is audited read-only (`global_artifact_audit.json`) and is not reusable here.
6. **Metrics.** 1X2 reuses `src.evaluation.metrics` unchanged (log loss primary; Brier, RPS, accuracy with the fractional tie
   rule, top-label ECE). Binary targets add log loss, Brier, accuracy, ECE and AUC (AUC only when both classes exist). Probability
   policy: finite, in [0, 1], normalised once in the model wrapper; log loss clips to `EPS` = 1e-15. Reported per ALL, league,
   season and league x season, with N, date range, stage and snapshot id. Confidence intervals and paired deltas use a
   **date-block bootstrap** (blocks = ISO weeks, because the sources publish no matchweek), 1,000 draws, fixed seed; a paired
   delta is `model - reference` on the same resampled blocks (negative = better). No single winner is declared.
7. **Time safety.** Default stage `t-24h`. The run repeats a replay check (the previous match of each team must be available at
   the stage cutoff) and asserts disjointness; a violation is reported in `leakage_audit.json`. Every row stays `BACKTEST_LIMITED`.
8. **Reproducibility.** `python -m src.league_models.benchmark` takes `--dry-run`, `--config`, `--stage`, `--eval-start/--eval-end`,
   `--target`, `--output-dir`; it writes a run manifest (config fingerprint, snapshot id, data-snapshot hash, git sha, Python and
   numpy versions, dependency-lock hash, output hashes), verifies every written file by hash, and appends an immutable registry
   entry whose id derives from the output hashes (a conflicting entry is refused). `--verify DIR` re-hashes a run;
   `--reproduce DIR` re-runs into a scratch directory and reports `REPRODUCED` or `NOT_REPRODUCIBLE`. Wall-clock timings are kept
   out of the hashes.
9. **Claims.** The 138-match report is a *reported observation* (an evaluation window, not training data). It is recounted
   from `reports/track_record_table.md` and classified; whether the licensed benchmark and the 138 share a fixture set is
   checked and stated (they do not).

## Consequences and limits
- Everything is score-only and `BACKTEST_LIMITED`; the global Poisson/Dixon-Coles joins teams of different leagues only through
  UEFA matches, so unseen-team rows and long scoreline tails are measured and reported (`rows_with_unseen_team`, `max_tail_mass`).
- UEFA finals are scored with a home-advantage term as if played at the home side's ground (declared limitation; neutral-venue
  handling is a Sprint 3 candidate feature).
- The reference model for paired deltas is Dixon-Coles because it is the production statistical model with a full goal
  distribution; it is a yardstick, not a claim that it is the best model.
- Sprint 3 must use the same folds, the same `inner_split`, and the frozen holdout; it may not change `benchmark.yaml` without a new ADR.
- ruff `E501` is ignored for four Sprint 2 modules (wide evidence strings), the same precedent as `src/markets`.

## Rollback
Delete `src/league_models/{splits,bench_metrics,benchmark,claims}.py`, `configs/league_models/benchmark.yaml`,
`tests/test_league_models_s2.py` and the `docs/league_models/s2_*` files. Nothing in production imports them.
