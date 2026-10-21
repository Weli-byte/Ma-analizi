# ADR 0066 - League-model programme, Sprint 4: league-aware gradient-boosting adapters

Date: 2026-10-21. Status: accepted; Sprint 4 built and run on the real cached source files. Sprints 5 and 6 not started.
Plan: `docs/league_models/plan.md` (PDF pages 10 to 13). Report: `docs/league_models/sprint04_ml_adapters.md`. Builds on ADR 0063, 0064 and 0065.

## Decision
Sprint 4 builds boosted-tree candidates on the Sprint 2 / 3 folds and records, for each of the nine competitions and each of the three targets,
whether a league-aware candidate beats a frozen global GBM **and** an equally tuned league-blind control out of sample. Nothing is promoted
(Sprint 5), nothing is calibrated, the production models in `src/models` are untouched.

1. **Same universe, same folds.** `ml_benchmark` rebuilds the universe with the Sprint 2 code and refuses to run unless its folds are byte-identical
   to the committed `s2_` / `s3_protocol_manifest.json`. The frozen holdout, reserved and guard fixtures are never read (they are carried by id only).
   The Sprint 3 candidates and references (`A_dc`, `B_dc_league`, `D_blend`, `E_elo_bridge`, `elo_s2`, ...) are re-run inside the same call on the same
   fixtures, so every model of both sprints is scored on identical matches.
2. **One feature contract** (`ml_features.py`, schema hash recorded): league identity (categorical, unseen league = NaN + `league_known`), league-season
   priors (last 300 available matches of the competition), Elo strength with the UEFA bridge (Sprint 3 `TimeAwareElo`), rolling goals for / against / points
   (5 and 10 matches), venue form, rest and 30-day congestion, new-club flag, days into the season. Missing = NaN + `_available` flag, never 0. Odds, xG, lineups,
   shots and injuries do not exist in the licensed universe and are not columns. A match enters the state only when `kickoff + lag <= stage cutoff` (t-24h),
   fixtures sharing a kick-off never see each other, the Elo is driven match by match under the same rule. Training and evaluation rows come from one code path.
3. **Candidates** (all three targets, 1X2 three-class, over/under 2.5 and BTTS binary, each with its own probability contract; exact score stays with the
   Dixon-Coles matrix, no score is derived from a GBM): `A_gbm` frozen global (league-blind, fixed parameters); `A_gbm_tuned` the same league-blind features tuned
   like B (**the control**); `B_gbm_league_default` and `B_gbm_league` (league-aware, fixed / tuned); `B_xgb_league` (XGBoost cross-check: the first Linux CI run showed that XGBoost CPU results differ between operating systems, so it is reported but is neither a decision candidate nor compared across machines; every LightGBM and statistical result matched Linux within 1e-3); `C_gbm_local` one model per
   competition; `D_gbm_blend` `(1 - w) B + w C`; `A_dc_ridge_tuned` (Sprint 3 finding 4: the DC team ridge tuned on the inner block). Ablations of B drop one feature group
   (rest, league priors, Elo, venue form) with the tuned parameters and no extra search.
4. **Tuning** is Optuna (TPE, seeded, coarse categorical grid, 14 trials, early stopping) on the **inner-validation season only** (the last training season; with a single
   training season the training rows are cut in time 70 / 30 with a purge). Every trial of every study is stored (`s4_tuning_trials.json`); a study stopped by the
   wall clock would be recorded as not reproducible (none was). CPU only, one thread, `deterministic=True`; no GPU is used or required.
5. **Local models** follow the plan's tiers: under 500 training matches of the competition, or a class with under 10 examples, the local model is `LOCAL_INELIGIBLE`
   and the row falls back to B (explicit, counted); 500 to 1,500 is evidence only (proof required); capacity is capped. **Blend weights** come from out-of-fit
   predictions on the inner-validation season, must beat B by 0.002 log loss, are shrunk by `n / (n + 300)` and are 0 otherwise.
6. **Decision rule** (per competition and target, DEV evidence only): paired block-bootstrap interval of the log-loss difference entirely below 0 against `A_gbm`
   **and** against `A_gbm_tuned` (and, for over/under and BTTS, against the train-rate baseline), Brier better, raw ECE not worse than 0.01, accuracy not rising while log loss
   worsens, a tier allowing local models, a non-zero local weight for the blend, and (1X2) not significantly worse than the existing Elo. The control is the important rule:
   without it, any tuned model "beats" a frozen one. A multiple-comparison note (135 comparisons) is part of the decisions file.
7. **Hypotheses** (Premier League rest / congestion, Serie A draws, La Liga Elo gap, Bundesliga goal density, Ligue 1 identity, Süper Lig home advantage, UEFA bridge) are
   feature-group ablations with the Sprint 3 verdict rule; squad continuity and any market claim are `NOT_TESTABLE`. Permutation importance uses the inner-validation
   season only and is reported as an association; SHAP was not computed (no new dependency, same information at group level).
8. **Reproducibility:** `--dry-run`, `--verify`, `--reproduce`, `--compare-committed`; run manifest with config fingerprints, snapshot id, library versions, schema hash and output
   hashes (including every model file); a registry entry; `training_runs.json` per fitted model (family, league, target, fold, schema hash, parameters, rounds, seed, git sha,
   train end, license ids, artifact path). The committed copy omits the model-file hashes (text of a booster can differ in the last digit between operating systems); the run output
   keeps them and `--verify` re-hashes them.

## CI
`.github/workflows/league-benchmark.yml` also re-runs Sprint 4 on the archived snapshot under Python 3.12 and compares with the committed `s4_*.json` files (protocol manifest and feature
schema byte-for-byte, everything else text-exact with numbers within 1e-3 and the discontinuous quantities within 0.01; the XGBoost cross-check is skipped, and the committed `training_runs` carry neither model-file hashes nor the git sha, which are per-machine provenance kept in the run output).

## Consequences and limits
- Score-only and `BACKTEST_LIMITED`; odds-free only (the odds-aware model card is NOT_AVAILABLE, no market baseline). The production XGBoost / LightGBM stack cannot run on this universe,
  so `A_gbm` is a score-only re-implementation, not the production model.
- Elo strength carries most of the signal (permutation importance); tuning adds most of the gain over the frozen GBM; league awareness adds little on 1X2 (see the report).
- The tuned parameters are chosen on one season per fold; the first fold has a 70 / 30 time cut of one season, which is a small block.
- Selection on the inner-validation season (tuning, early stopping) also feeds the blend weights, a small optimistic bias for B and C that favours w = 0 (conservative).
- The Elo feature uses the Sprint 3 country mean for newly seen clubs, computed from matches that are available at the row's cutoff (the availability rule is applied to its updates).

## Rollback
Delete `src/league_models/{ml_features,ml_models,ml_benchmark}.py`, `configs/league_models/ml_models.yaml`, `tests/test_league_models_s4.py`, the `docs/league_models/s4_*` files, the pyproject
per-file ignore for `ml_*.py` and the Sprint 4 steps of the workflow. Nothing in production imports them.
