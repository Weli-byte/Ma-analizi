# Sprint 6: shadow mode, canary policy, monitoring and rollback: report and final delivery of the league-model programme

Status: **Sprint 6 built (2026-10-22). The shadow layer ran against the real schedule provider and wrote 8 real, immutable pre-match records for 4 real fixtures. No result is known yet, so there is no prospective accuracy
number. No model is served to users and none is eligible to be: GO for shadow collection, NO-GO for any canary.** Plan: [`plan.md`](plan.md) (PDF pages 17 to 19), decisions: ADR 0068 (builds on ADR 0063 to 0067).
Delivery files: [`s6_integration_inventory.md`](s6_integration_inventory.md), [`s6_runbook.md`](s6_runbook.md) (rollback runbook + on-call checklist), [`s6_deployment_manifest.yaml`](s6_deployment_manifest.yaml),
[`s6_alert_and_dashboard_definitions.yaml`](s6_alert_and_dashboard_definitions.yaml), [`s6_shadow_first_run.json`](s6_shadow_first_run.json), [`evidence/`](evidence/).

## Headline findings (read these first)
1. **Production default is global and the shadow layer cannot change what users see.** `kill_switch: true`, canary fraction 0.0, every shadow record `visible_to_users: false` (a record claiming otherwise is refused), the production API and web
   UI are untouched. All routing returns GLOBAL until the owner changes a flag; a fail-closed alarm also creates the kill-switch flag file by itself.
2. **Real pre-match records exist (N = 8 records, 4 fixtures, kick-offs 2026-10-23 18:45 to 19:00 UTC).** For each fixture the production-family global model and the Sprint 5 shadow candidate for that league were asked on the same snapshot at
   2026-10-22 18:49 and 19:06 UTC (stage t-24h): Serie A Torino-Monza (global + `B_gbm_league` 1X2), Ligue 1 Brest-Nice (global + `E_elo_bridge`), Premier League Ipswich-Nottingham Forest (global + `E_elo_bridge`), La Liga Alavés-Málaga
   (global + `B_dc_league` over/under). The schedule came from the football-data.org free tier (read-only); the models were fitted on 15,111 licence-clean openfootball matches finished before the call. The replay at 19:06 reported 4 `EXISTS`, 4 `CREATED`, 0 conflicts:
   idempotency was observed on the real store. `live_verified` is true in the scorecard and means only that real pre-match records exist.
3. **Prospective accuracy: unknown, deliberately.** 0 of the 4 fixtures have kicked off; 0 results are stored; the scorecard says `TOO_FEW_PAIRS_NO_CLAIM` by construction below 30 resolved pairs and the alarms stay silent below 100. The first resolved pairs
   arrive after the 2026-10-23 matches (`python -m src.league_models.rollout reconcile`). The backtest (Sprint 5 holdout, N = 2,278) is a different protocol and is never merged with these numbers.
4. **Canary: NO-GO, for two independent reasons.** Sprint 5 approved no model (`APPROVED_FOR_CANARY` count 0), and the schedule source football-data.org is `CONDITIONAL` in the licence manifest, so the eligibility gate would block any model fed by it
   (`LICENSE_CONDITIONAL`) even if approved. Opening a canary needs both a proven candidate and written terms.
5. **Rollback is proven in a simulation, not in a real staging.** A scratch-directory rehearsal with a SYNTHETIC approved model (TEST_ONLY) showed: 50% canary split, a fail-closed alarm trips the kill switch, every user returns to GLOBAL, stored records are
   byte-identical afterwards, a replay is idempotent, an old-schema record is detected, and the audit log has no raw user id (`evidence/s6_rollback_rehearsal.json`, 7 of 7 steps). The project has no separate staging deployment: real staging is NOT_VERIFIED.
6. **One real defect was found by the real run and fixed.** The first live run (18:48 UTC) skipped both due fixtures as `UNRESOLVED_TEAM`: the football-data.org alias table lacked Torino, Monza, Brest and Nice. Nothing was guessed and nothing was registered.
   The mapper now tries the exact same string in the approved `openfootball` alias namespace (never fuzzy, never a new club) and flags the record `identity: EXACT_ALIAS_FROM_OPENFOOTBALL_NAMESPACE`; the next run stored the records.
7. **The monitoring layer sees what it should and stays quiet when it should.** The real monitor run raised 2 alerts and 0 critical: `license:football_data_org` (info: CONDITIONAL, research evidence only) and `drift_insufficient_rows` (info: 4 records are too few to assess drift).

## Acceptance criteria of the Sprint 6 prompt
| Criterion | Result | Evidence |
|---|---|---|
| Production default global; shadow does not change the visible prediction | DONE by construction and tested; no production file changed | `rollout.yaml`, `test_default_routing_is_global_and_the_kill_switch_beats_everything`, `s6_integration_inventory.md` |
| Real pre-match immutable predictions and result linking work | DONE for the records (8 real); result linking is implemented and tested, but NOT_VERIFIED on real data until the 2026-10-23 matches finish | `s6_shadow_first_run.json`, `test_a_record_is_idempotent_immutable...`, `test_outcomes_are_a_separate_append_only_store` |
| Canary opens only for S5-approved and legally eligible models | DONE (gate + licence); nothing qualifies, fraction 0 | `control.py`, `test_only_an_approved_and_licensed_model_can_ever_be_served` |
| Rollback proven in staging; kill switch works | kill switch DONE and tested; staging rehearsal DONE as a TEST_ONLY simulation; a real staging environment does not exist: NOT_VERIFIED | `evidence/s6_rollback_rehearsal.json` |
| E2E on real data or honest NOT_VERIFIED | PARTIAL: ingest, canonical mapping, as-of snapshot, global + shadow, immutable record verified on real data; reconciliation, metrics and UI/API NOT_VERIFIED (no result yet; no UI/API surface by design) | `s6_shadow_first_run.json` |
| Prospective and backtest metrics separate, small-N warnings visible | DONE | `evaluation.py`, `dashboard.py`, `evidence/s6_dashboard_example.html` |

## What was built
- `src/league_models/rollout/`: `records.py` (immutable prospective records, outcome store), `shadow.py` (ingest, mapping, as-of fit, global + candidate prediction), `control.py` (config, LeagueModelRegistry, EligibilityGate, deterministic CanaryRouter, kill switch, stage advice),
  `monitoring.py` (about 20 alarm types, see the definitions file; fail-closed trip), `evaluation.py` (reconciliation, prospective scorecard, lag), `dashboard.py` (static dashboard), `rehearsal.py`, `__main__.py` (`shadow | reconcile | monitor | scorecard | dashboard | status`).
- `configs/league_models/rollout.yaml` (flags, staged canary plan, thresholds), `calibrators.py` gained `Calibrator.from_dict` (restores the Sprint 5 calibrators), `scripts/shadow_tick.ps1` + `scripts/install_shadow_task.ps1` (written, **not registered**).
- `tests/test_league_models_s6.py` (24 tests: records, kill switch, canary, scorecard, 15+ resilience scenarios, shadow run with a fake provider labelled TEST_ONLY), ADR 0068, runbook, manifests.

## Canary policy (written in advance; `rollout.yaml`)
| Stage | Fraction | Minimum observation | Minimum resolved pairs |
|---|---|---|---|
| shadow only | 0% | 14 days | 100 |
| internal users | 1% | 7 days | 100 |
| canary 5 | 5% | 14 days | 200 |
| canary 20 | 20% | 14 days | 300 |
| canary 50 | 50% | 14 days | 400 |
| full | 100% | 28 days | 600 |
To advance: prospective paired log-loss interval entirely below 0 against the global model, no critical alarm in the window, licence PERMITTED. `stage_gate` returns advice with reasons; changing a fraction is the owner's edit. With few pairs no improvement is claimed.

## Model lineage audit (shadow families, 2026-10-22)
| Family | Version | Used for | Fitted on | Calibrator |
|---|---|---|---|---|
| global (`elo_s2` + `dixon_coles`) | `global-76bbe7a2f4` | 1X2, over/under 2.5, BTTS | 15,111 openfootball matches, snapshot `snap-546e70fff7fb` | Sprint 5 final calibrators (hierarchical 1X2, hierarchical totals, affine goals) restored from `s5_calibration_manifest.json` |
| `B_gbm_league:1x2` | `B_gbm_league-53a987ea93` | Serie A 1X2 | same history, tuned on the last full season | none (chosen by walk-forward) |
| `E_elo_bridge:1x2` | `E_elo_bridge-bbbbdc183d` | Premier League and Ligue 1 1X2 | same history | none |
| `B_dc_league:over_under` | recorded in the store | La Liga over/under | same history | hierarchical totals |
Every record carries its snapshot hash, the licence ids (`openfootball_domestic`, `football_data_org`) and the flags `lineups: UNKNOWN`, `odds: NOT_USED`.

## 30-day monitoring plan (2026-10-23 to 2026-11-22)
1. Week 1: owner decision on registering `FootballShadowTick` (30-minute cadence; the t-24h windows are 3 hours long). Without it, records exist only when someone runs the shadow command inside a window.
2. Daily: the on-call checklist in the runbook; `reconcile` after each matchday; read `alerts.jsonl`; never remove a kill-switch flag without reading its reason.
3. Day 7, 14, 30: scorecard review. Expected N: each matchday of the four competitions has about 30 fixtures, of which only the four candidate cells are scored paired, so roughly 3 to 4 resolved pairs per candidate per matchday; reaching the 100-pair floor of the first stage needs many weeks. This is why no claim is made now.
4. Day 30: report real N, date range and the paired interval per candidate; a candidate with an interval above 0 stays in shadow or is retired; none can reach users before the licence question is settled.
5. Standing items: confirm football-data.org commercial / ML terms in writing; decide on a licensed schedule source; add the missing alias rows for football-data.org (owner-approved).

## Sprint 1 to 6 matrix
| Sprint | Delivered | Real result | Blocked / not delivered |
|---|---|---|---|
| 1 Data, licences, time safety | licence manifest, canonical ids, as-of contract, coverage matrix, leakage tests, source archive | 16,239 finished licence-clean matches; 0 identity collisions; 64,956 (fixture, stage) time checks, 0 violations | football-data.co.uk BLOCKED; 10 competition-seasons have no data; no shots / xG / lineups / injuries / transfers / timestamped odds in the licensed universe |
| 2 Global reference | frozen walk-forward over five folds, baselines on identical fixtures, claims audit | production Elo 0.9990, Dixon-Coles 1.0230 pooled log loss; constant rate beats the goal model on over/under and BTTS | the production GBM / ensemble could not be re-scored (their features read the blocked source) |
| 3 League statistical models | ridge DC, time-aware Elo, UEFA bridge, blend, hypotheses | Elo bridge best (0.9958); Poisson league terms did not beat global | no standalone UEFA model; squad continuity untestable |
| 4 ML adapters | feature contract, tuned GBM, tuned control, local GBM, blend, ablations, error analysis | tuning, not league awareness, explains the gain; league awareness helps BTTS only | SHAP not computed; XGBoost differs across operating systems (cross-check only) |
| 5 Calibration, holdout, gate | calibrators, one-shot holdout (read once), pre-registered gate, router dry run | 27 cells: 0 approved, 4 shadow, 2 rejected, 5 blocked, 16 global | Europa and Conference League have no 2025-26 file; Süper Lig too thin to screen |
| 6 Shadow, canary, monitoring, rollback | this report | 8 real pre-match records; kill switch and rollback simulated; canary NO-GO | no real staging, no resolved results yet, scheduler not registered, no UI / API surface |

## Missing from the plan (complete list, honest)
**Data and licence**
1. Shots, corners, cards, xG, lineups, injuries, suspensions, transfers / squad continuity, travel: not in any licensed source (owner decision: score-only). Hypotheses that need them (Premier League tempo, Süper Lig squad continuity, Bundesliga shot-based goal density, travel / rest beyond match gaps, rotation in the Europa League) are NOT_TESTABLE.
2. Timestamped odds and a market baseline (the plan asks for one when licensed): none; the Odds API exact odds cover few leagues and are CONDITIONAL; no odds-aware model card.
3. football-data.org commercial and ML rights are unconfirmed (CONDITIONAL): the shadow schedule and any canary depend on that answer. The alias table for that source lacks several clubs (Torino, Monza, Brest, Nice among them). API-Football gives no publication right.
4. Missing seasons: Europa League and Conference League 2025-26 (no file at the source), Süper Lig 2025-26, Ligue 1 2019-20 incomplete, 10 competition-seasons without data; the Premier League / La Liga guard seasons stay out of operational fits.
5. UEFA format versions and Süper Lig league sizes remain `ASSISTANT_DECLARED_UNVERIFIED`; group versus knockout stage is not marked in the scores, so no stage context feature exists.
**Modelling**
6. A full hierarchical (random-effect) model of team strength across leagues and a joint bridge: only ridge-penalised country levels and Elo offsets exist. A regularised logistic correction on global probabilities (candidate B in the plan) exists only as league-aware refits and the hierarchical calibrator.
7. Opponent-adjusted form, 90 / 365-day windows and exponential time weights as features (only 5 / 10-match windows, a 30-day congestion count and a time-decay ablation).
8. Nested walk-forward for every choice: tuning used one inner season per fold (a 70 / 30 time cut in the first fold); blend weights share that block.
9. The exact-score target is scored only in the Sprint 3 audit; the Sprint 5 holdout report has no correct-score top-1 versus likelihood table. The "always over 68.8%" figure of the 138-match report cannot be re-verified (those matches are not in the licensed universe).
10. Expected goals per team in the prediction record (`expected_goals_home/away`) and the score matrix are not stored in the prospective record.
**Evaluation**
11. Per-league reliability plots / SVG diagrams, effective-N tables per fixture group beyond rows and iso-week blocks, and stage-level (T-90m, T-30m, kickoff) scorecards: only pooled reliability bins and a t-24h scorecard exist (the score-only universe has the same information at every stage).
12. Prospective accuracy: not a single resolved prospective pair exists yet; the plan's step 10 has started but has no result.
13. Latency is a representative probe, not a production measurement; the isotonic calibrator cannot be restored from its summary.
**Product and operations**
14. No UI or API surface for shadow or league-model predictions, no sample-size caution banner or source-attribution panel in the product, no reconciliation view in the web app (the static dashboard exists).
15. The shadow task is not registered (owner approval); no cloud scheduler job; shadow alerts are not yet merged into `src.mlops.report`; no external alert channel (e-mail / push).
16. No real staging deployment, so the rollback rehearsal is a simulation; no live smoke of the full chain to UI / API; secrets handling was checked by design and tests, not by a secrets scanner.
17. Config keys of the plan (`leagues.<id>.enabled`, `minimum_completed_matches`, `candidate_families`, `lookback_seasons`, ...) are expressed in `rollout.yaml`, `ml_models.yaml` and `approval_policy.yaml` rather than in a single `leagues:` block; the model artifact manifest has no `training_cutoff` / `validation_windows` fields in one file (they are spread over the run manifests).
18. LLM predictions are out of scope for this programme; the existing LLM infrastructure was not touched.
**Decisions that stay with the owner:** register the shadow task, confirm the football-data.org terms, choose a licensed schedule / statistics source if league models are to be shown to users, open any canary stage, any change to the production API / UI.

## Commands and checks
```
python -m src.league_models.rollout shadow        # real schedule read (free tier), immutable records
python -m src.league_models.rollout reconcile     # results -> append-only outcomes
python -m src.league_models.rollout monitor       # alarms; a fail-closed alarm trips the kill switch
python -m src.league_models.rollout scorecard     # prospective scorecard (separate from the backtest)
python -m src.league_models.rollout dashboard     # static dashboard
python -m src.league_models.rollout status
python -m src.league_models.rollout.rehearsal     # TEST_ONLY rollback simulation
```
- Local checks (2026-10-22): `ruff check .` clean; full `pytest` run without failure (24 Sprint 6 tests); real shadow runs at 17:14, 18:48, 18:49 and 19:06 UTC (`s6_shadow_first_run.json`).
- CI (GitHub Actions, PR #89, 2026-10-22): the `league-benchmark` job (Ubuntu 24.04, Python 3.12) re-ran Sprints 2 to 5 (development stage) and passed every comparison; `test (3.12)`, `test (3.14)`, `real-data-sanity` and `lock-up-to-date` passed after the lock files were regenerated (three times in two days: upstream packages release every few hours, e.g. pandas 3.1.0 and python-dateutil 3.0.0 in the last regeneration; the daily relock workflow remains the long-term answer). The benchmark numbers did not change with the new lock.
