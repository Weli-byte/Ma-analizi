# ADR 0065 - League-model programme, Sprint 3: league statistical candidates and the UEFA bridge

Date: 2026-10-21. Status: accepted; Sprint 3 built and run on the real cached source files. Sprints 4 to 6 not started.
Plan: `docs/league_models/plan.md` (PDF pages 8 to 10). Report: `docs/league_models/sprint03_league_statistical_models.md`. Builds on ADR 0063 and 0064.

## Decision
Sprint 3 trains regularised statistical candidates on the Sprint 2 folds and records, for each of the nine competitions, whether a league-aware
candidate beats the global reference out of sample. Nothing is promoted (Sprint 5), the production models in `src/models` are untouched.

1. **Same universe and folds as Sprint 2.** `stat_benchmark` builds the universe with the Sprint 2 code and refuses to run unless its folds are
   byte-identical to the committed `s2_protocol_manifest.json`. The frozen holdout, reserved and guard fixtures are never read.
2. **New model code, not a change of the old one.** `RidgePoissonDC` (log-linear Poisson, ridge identifiability toward 0, optional competition intercept and
   home-advantage deviation, optional country levels, optional rest covariate and time decay, Dixon-Coles rho restricted to the interval where tau stays >= 0,
   a negative mass rejects the model, grid 0..12 with tail mass and pre-normalisation sum reported, one matrix for 1X2 / over 2.5 / BTTS / exact score) replaces
   the slow IPF fit for this programme; `TimeAwareElo` implements per-country scales, season reversion to the country mean, a promoted-club prior, goal-difference
   updates, kick-off-ordered replay with same-kick-off isolation and UEFA country offsets. `src/models` is not edited; the existing global `EloModel` and
   `HistoricalPrior` are re-run unchanged as references (`elo_s2`, `historical_prior`).
3. **The cross-league bridge** is the set of country offsets of `B_dc_league` (and the offsets of `E_elo_bridge`), identified only by matches between clubs of
   different countries (UEFA) before each fold's cutoff. Below 200 international training matches the bridge is NOT_AVAILABLE for that fold (global + competition prior,
   UNCERTAIN); a country with under 20 international matches is flagged UNCERTAIN. No standalone UEFA model is trained.
4. **Candidates:** A global DC; B global DC + competition terms + country levels; C one DC per competition; D matrix-level blend `(1 - w) B + w C` with `w` per
   competition learned on the inner-validation season and shrunk by `n / (n + 300)`; E per-country Elo with bridge; F one Elo per competition. Ablations: no rho, rest days,
   stronger ridge, no competition home advantage. A local fit under 100 matches is NOT_TRAINED and replaced by an explicit, counted fallback; the reference `A_dc` or
   `E_elo_bridge` failing to train stops the run (fail closed). Acceptance of a fit is by the gradient (per row), never by an optimizer flag alone.
5. **Decision rule (fixed in `stat_models.yaml`, applied by `league_decisions.json`):** a candidate is eligible for a competition only with a paired log-loss block
   interval entirely below 0 against `A_dc`, a better Brier score, a raw ECE not worse than 0.01, a volume tier that allows it (local models off under 500 training matches,
   proof required from 500 to 1,500), and no fold fallback. The verdict is DEV-only evidence; the existing Elo is reported next to it because Sprint 2 found it the stronger
   reference.
6. **League hypotheses are ablation tests, not facts:** SUPPORTED only if the whole 95% block interval of (variant minus base) is below 0 on dev out-of-sample rows,
   NOT_SUPPORTED if above, otherwise INCONCLUSIVE; squad continuity is NOT_TESTABLE without squad data. No feature weight is fixed from a hypothesis.
7. **Hyper-parameters are engineering defaults in `configs/league_models/stat_models.yaml`, not tuned.** The only quantities learned from data per fold are the model
   parameters and the blend weights (inner-validation block). Tuning on the inner-validation block is a Sprint 4 task.
8. **Reproducibility:** same commands as Sprint 2 (`--dry-run`, `--verify`, `--reproduce`, `--compare-committed`), immutable registry entry, run manifest with config
   fingerprints, snapshot id and output hashes.

## CI
`.github/workflows/league-benchmark.yml` also re-runs Sprint 3 on the archived snapshot under Python 3.12 and compares with the committed `s3_*.json` files (structural files
byte-for-byte, metric files within 1e-3).

## Consequences and limits
- Score-only and `BACKTEST_LIMITED`; clubs unseen in training are predicted at league-average / country level and counted; UEFA finals keep a home advantage.
- The Elo gain over the existing Elo (-0.0032 pooled) cannot be attributed to one component (no component ablation in this sprint).
- The default ridge is too weak (a 4x stronger ridge is better pooled); this is reported, not fixed here.

## Rollback
Delete `src/league_models/{stat_models,stat_benchmark}.py`, `configs/league_models/stat_models.yaml`, `tests/test_league_models_s3.py`, the `docs/league_models/s3_*`
files and the Sprint 3 step of the workflow. Nothing in production imports them.
