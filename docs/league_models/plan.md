# League-based models: plan summary (saved 2026-10-20)

Source: `docs/reference/Lig_Bazli_Modeller_6_Sprint_ULTRA_PRO_MAX_GUNCEL.pdf` (27 pages, dated 2026-10-20; text copy
`docs/reference/lig_bazli_modeller_plan.raw.txt`). Pages 1 to 8 are the technical plan; pages 9 to 27 are one copy-paste
Claude Code prompt per sprint (6 sprints). This file is a faithful summary plus the repo-specific decisions taken while
implementing it (ADR 0063). The PDF stays the source of truth.

## Goal in one paragraph
Add a league-aware prediction layer **without touching the existing global production model** (Elo + Poisson/Dixon-Coles +
XGBoost/LightGBM + calibration + ensemble). Training a separate model per league is **not** mandatory: with little history
a global model plus league effects and partial pooling is safer. A separate league model reaches production only if it shows
a meaningful superiority on an independent, forward-in-time test; otherwise the global model stays.

## Scope and targets
- **Nine competitions:** Premier League, Serie A, La Liga, Bundesliga, Ligue 1, Süper Lig; Champions League, Europa League,
  Conference League as a separate regime.
- **Targets:** 1X2, over/under 2.5 goals, both teams to score (BTTS). Exact score is derived from the joint goal distribution.
- **Metrics:** 1X2 primary = multiclass log loss; secondary Brier, RPS, accuracy, ECE. Binary targets: log loss, Brier, ROC-AUC,
  accuracy. Block (date/matchweek) bootstrap confidence intervals and paired deltas; coverage and abstention reported.
- **Publication gate:** better score than the global model on an independent chronological holdout, with calibration not
  worse. No superiority means the global model is kept.
- **Forbidden:** future data, post-match statistics, retroactively updated lineups/odds, fake xG, invented API answers.

## Data volume tiers (starting engineering rules, not scientific limits)
| Completed matches (per league/target) | Default approach | Decision |
|---|---|---|
| < 500 | global + league indicator + regularisation | no independent league model |
| 500 to 1,500 | global + league effect; local DC/Elo candidates | promote only with out-of-sample superiority |
| > 1,500 | local GBM, DC and hierarchical candidates | compare with nested walk-forward |
| UEFA competitions | cross-league strength bridge + global model | no independent GBM if the sample is small |
Collection target: 3 to 5 completed seasons per league plus the current season (about 380 matches for a 20-team league,
306 for 18 teams). A collection goal, not a statistical threshold.

## Four competing candidates
- **A. Global reference:** one model over all licensed leagues; league_id, season, league goal average, home advantage as
  features. The production reference is never deleted.
- **B. Global + league adaptation:** small league-wise correction or interaction (regularised logistic correction, random
  intercept, hierarchical calibration).
- **C. Full league model:** separate Poisson/DC (and XGBoost/LightGBM if enough data) per league, that league's history only.
- **D. Partial pooling / mixture of experts:** `p = (1 - w_league) * p_global + w_league * p_league`, `w_league` learned from
  out-of-training predictions and shrunk toward 0 with little data.
- **Production default:** B and D first; C only with enough data and independent superiority. Ensemble weights are never tuned on test/holdout.

## League hypotheses (to test, never coded as facts)
Premier League: tempo and opponent strength; Serie A: low scoring, draws; La Liga: top-team inequality, home effect;
Bundesliga: goal density; Ligue 1: strength concentration, season transitions; Süper Lig: squad continuity, home/away gap
(a 9/9 observation is not evidence); Champions League: cross-league transfer, changing format; Europa League: opposition variety,
rotation (only if verified); Conference League: few data, strong shrinkage (a 7-match 86% is not lasting performance).

## Time safety (as-of)
Store `prediction_as_of` and `fixture_kickoff_utc`; check `feature_event_time <= prediction_as_of` and
`feature_available_at <= prediction_as_of` separately. Form only from earlier matches (watch same-day and postponed
matches). Windows 5/10 matches, 30/90/365 days, opponent-adjusted, exponential time weights. Elo scales kept separate per
league/tournament, regression to the mean at season start, league prior for promoted teams. Missing xG is NULL plus a
coverage flag (shots are a separate feature, never renamed xG). A stage-by-stage availability matrix for T-24h, T-90m,
T-30m and kickoff; an unannounced starting eleven can never be a T-24h input.

## Ten-step protocol
1 data audit; 2 feature snapshot per match; 3 chronological split (early seasons train, later blocks validation, newest
season/window = untouched final holdout); 4 nested walk-forward (hyper-parameters and weights only in the inner layer);
5 re-score the global baselines on the same fixtures; 6 A/B/C/D candidates on the same snapshot/test set/target;
7 calibration on a separate chronological split (hierarchical/global calibrator for small leagues); 8 model selection
(primary log loss, Brier, accuracy, ECE, bootstrap; no improvement means baseline); 9 one-shot frozen holdout; 10 prospective
validation (real pre-match records; backtest and live metrics kept apart).

## Example promotion policy (to be fixed in writing beforehand)
Holdout log-loss difference < 0 and the block-bootstrap 95% interval mostly below 0; ECE not clearly worse; coverage not
dropping; acceptable operational latency.

## Over/under, BTTS, exact score
Compare two approaches on the same out-of-sample set: (i) league Poisson/DC score matrix giving P(total > 2.5), P(BTTS), P(i-j)
(matrix sums to 1, watch the truncation tail); (ii) target-specific binary LightGBM/XGBoost with its own calibration. The
current 60.9% over/under versus 68.8% "always over" is the critical comparison: check the over share, league mix, time range
and class imbalance before calling a league model successful.

## UEFA bridge
Local league Elo is not automatically a common scale. Build a bridge from clubs' past international matches with
country/league random effects and team strengths updated jointly; use only international matches played before the
prediction time; shrink newcomers to their league prior; version format changes by date.

## Production architecture (Sprint 5 and 6)
`fixture -> canonical team/league mapping -> as_of feature store -> global model -> league adapter/local model (if eligible)
-> gate/router -> calibration -> p(1,X,2), p(O/U), p(BTTS), score matrix -> immutable PredictionRecord -> UI/API`.
Components: LeagueModelRegistry, EligibilityGate, PredictionRouter (GLOBAL / ADAPTED / LOCAL / BLENDED with a fallback reason),
FeatureSnapshotStore, EvaluationStore, Monitoring. New models start in **shadow mode**; existing results are never silently
changed. Missing model or licensed data means fall back to the global model (visible to users/audit) or an explicit error,
never a fabricated answer.

## The six sprints and their exit criteria
| # | Sprint | Exit criterion |
|---|---|---|
| 1 | Real data, licences, time safety: coverage matrix, canonical IDs, licence manifest, as_of schema, leakage tests | real multi-season dataset and 100% time audit |
| 2 | Global reference: freeze production, record every baseline on the same out-of-sample folds | reproducible global benchmark report |
| 3 | League statistical models: DC/Poisson/Elo per league and the UEFA bridge | candidate artifact and out-of-sample predictions for every league |
| 4 | ML adapters: league-aware global GBM, local GBM, shrinkage blend; Optuna on train/validation only | model cards, error analysis, candidate comparison |
| 5 | Calibration and decision: calibration, holdout, block bootstrap, eligibility and approval gate | approval only for leagues with proven improvement, others stay global |
| 6 | Shadow and rollout: shadow predictions, live tracking, canary, alarms, rollback | production records, dashboard, rollback rehearsal |
Run in order with sprint gates; never all six at once, unattended, or as an unconditional production deploy.

## Test suite and red lines (apply to every sprint)
Time leakage; fixture ID / team alias collisions (no match twice in training); promotion/relegation, season change,
postponements, clock changes, UEFA format changes; NULL kept for missing xG/lineups/odds; single-class short periods, few
matches, heavy favourites; separate test set never leaking into Optuna/calibration/blend weights/selection; deterministic
seeds, dependency lock, artifact hash, licence audit logs; production smoke with a real provider and a real fixture, no
`LIVE_VERIFIED` label without a real API check.
Red lines: no invented match/odds/xG/lineup/API answer/model success/screenshot; mock results never presented as real
verification (synthetic unit-test examples are labelled `TEST_ONLY`); no data from a source whose rights are not granted
(football-data.co.uk is BLOCKED for commercial/AI use; API-Football gives no third-party publication right; football-data.org
commercial/ML rights must be confirmed; The Odds API terms of 2026-08-31 shared by the user: commercial UI, ML and storage
allowed, raw-data resale forbidden); no keys printed or committed; unknown access is reported as
`BLOCKED` / `NOT_CONFIGURED` / `NOT_VERIFIED`; the final holdout is never used for tuning, Optuna, selection, calibration or weights.

## Decision table (starting defaults)
| Group | First production candidate | Promotion of a local model |
|---|---|---|
| Big five leagues | global + league adapter + DC | OOS and holdout superiority |
| Süper Lig | global + league adapter | enough seasons + OOS superiority |
| UEFA three tournaments | global + cross-league bridge | tournament-specific data and independent superiority |

## Caveats the plan states about itself
It was designed from the user-reported 138-match results and earlier discussions; the repository, live coverage, current model
parameters and real production logs were **not** audited, so league behaviours are hypotheses, thresholds are proposals and
example scores are illustrative. The 138-match record is an evaluation window, not a training set.

## Repo status of this plan
See ADR 0063 for the decisions and the per-sprint status; Sprint 1 outputs live in this folder
(`sprint01_data_audit.md` and the files it lists); Sprint 2 outputs: `sprint02_global_benchmark.md` and the `s2_*.json` files (ADR 0064); Sprint 3 outputs: `sprint03_league_statistical_models.md` and the `s3_*` files (ADR 0065); Sprint 4 outputs: `sprint04_ml_adapters.md` and the `s4_*` files (ADR 0066).
