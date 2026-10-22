# ADR 0068 - League-model programme, Sprint 6: shadow mode, canary policy, monitoring and rollback

Date: 2026-10-22. Status: accepted; Sprint 6 built; the shadow layer ran against the real schedule provider. No model is served to users and none is eligible to be.
Plan: `docs/league_models/plan.md` (PDF pages 17 to 19). Report: `docs/league_models/sprint06_shadow_and_rollback.md`. Builds on ADR 0063 to 0067.

## Decision
Sprint 5 approved nothing for canary, so Sprint 6 builds and exercises the safe machinery around that fact instead of rolling anything out: an immutable prospective record layer, a shadow
runner for the four `SHADOW_INCONCLUSIVE` candidates next to the global model, a deterministic canary router that is closed by default, monitoring with fail-closed alarms, a kill switch, and a
scorecard that keeps prospective results apart from the backtest. Production behaviour is unchanged; the production API and web UI are not touched.

1. **Safe defaults** (`configs/league_models/rollout.yaml`): `kill_switch: true`, `canary.active_fraction: 0.0`, shadow records `visible_to_users: false` (a record that claims otherwise is refused). The staged canary plan (shadow-only,
   1% internal, 5 / 20 / 50 / 100%) is written in advance with minimum observation days and minimum resolved pairs; advancing is advice with reasons (`stage_gate`) and a config change by the owner, never automatic.
2. **Immutable prospective records** (`rollout/records.py`): fixture, league, kick-off, `prediction_as_of_utc`, stage, model version, family, the five probabilities (null where a model does not predict that target), snapshot hash, data
   licence ids, coverage flags (lineups UNKNOWN, odds NOT_USED), fallback reason, created_at, content hash. The key (fixture, stage, model version, family) is idempotent; a rerun with different content is a conflict and is
   refused; a post-kickoff prediction, a stage outside its window, a malformed probability or an incomplete 1X2 is refused; a storage failure raises (nothing is acknowledged that was not written). Results arrive in a
   separate append-only outcome store; a different final score for the same fixture is logged as a conflict and never overwrites.
3. **Shadow run** (`rollout/shadow.py`): schedule from the football-data.org free tier (read-only, CONDITIONAL licence), canonical mapping through the existing `TeamDirectory` (never auto-registers), fixtures whose stage window
   is DUE now, models fitted only on licence-clean openfootball history that is finished at `now`, global (Elo for 1X2, Dixon-Coles for over/under and BTTS, with the Sprint 5 calibrators restored from their recorded parameters)
   and every registry candidate whose gate status allows shadow, predicted on the same snapshot. An unfitted candidate is reported and skipped, never replaced by a guess; a provider failure produces zero records.
4. **EligibilityGate and canary router** (`rollout/control.py`): a model is served only if its Sprint 5 status is `APPROVED_FOR_CANARY` AND every data licence it uses is `PERMITTED`. football-data.org is `CONDITIONAL`, so any model that uses it
   as a data source can never reach users (reason `LICENSE_CONDITIONAL`), whatever its score. Routing is deterministic (salted hash bucket), the audit log stores a salted hash of the user and the served version, never the user.
   A corrupted artifact falls back to GLOBAL with an alarm; a broken global model raises.
5. **Monitoring** (`rollout/monitoring.py`): schema mismatch, malformed probabilities, fixture identity conflicts, claimed lineups, user-visible shadow records, provider failures (three consecutive = critical), zero coverage, refused
   records, missing models, slow inference, stale scheduler, licence status, artifact hashes, outcome lag, drift (PSI) and per-candidate performance. A fail-closed alarm creates the kill-switch flag file and routing returns to GLOBAL;
   stored records and artifacts are untouched. A performance alarm needs at least 100 resolved pairs, a result is "pending" until kick-off + 6 h, and drift is reported separately from measured accuracy.
6. **Scorecard and dashboard** (`rollout/evaluation.py`, `rollout/dashboard.py`): prospective pairs by stage and candidate with real N and date range, paired block-bootstrap interval only with at least 30 pairs
   (`TOO_FEW_PAIRS_NO_CLAIM` otherwise), `live_verified` only when at least one real pre-match record exists (and it says nothing about accuracy); the backtest is shown in a separate box.
7. **Rollback rehearsal** (`rollout/rehearsal.py`): a staging simulation with a SYNTHETIC approved model (labelled TEST_ONLY) proves canary on, fail-closed trip, all users GLOBAL, records byte-identical, idempotent replay and old-schema
   detection. The project has no separate staging deployment; that is reported NOT_VERIFIED.
8. **Not done on purpose** (needs the owner): registering the scheduled task (`scripts/install_shadow_task.ps1` is ready, not run), any change of the production API or web UI, any traffic routing, any paid call, any write to an external system.

## Consequences and limits
- The shadow layer only produces evidence; with no approved model there is nothing to canary. Candidates that used football-data.org for the schedule could not be served even if approved, until its terms are confirmed in writing.
- The t-24h window is three hours long; the later stages need the 10-minute cadence of the snapshot engine. Only stages that were actually DUE when the runner ran have records.
- Operational models are refitted on all licence-clean history (the guard seasons stay out). The frozen holdout fixtures are ordinary history here because their one evaluation is finished.
- The scorecard's numbers for a small N are descriptive; no improvement is claimed below 30 resolved pairs and no alarm fires below 100.

## Rollback
Set `kill_switch: true` (already the default) or create the flag file; delete `src/league_models/rollout/`, `configs/league_models/rollout.yaml`, `tests/test_league_models_s6.py`, `scripts/shadow_tick.ps1`, `scripts/install_shadow_task.ps1`
and the `s6_*` docs. Stored prospective records are append-only evidence and are not deleted by a rollback.
