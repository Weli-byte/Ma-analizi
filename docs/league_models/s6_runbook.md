# Sprint 6 runbook: rollback, resilience and on-call checklist

Scope: the league-model shadow / canary layer (ADR 0068). The global model is never removed; every route has a GLOBAL fallback; production traffic fraction is 0.

## One-line rollback (kill switch)
1. Set `kill_switch: true` in `configs/league_models/rollout.yaml` (it is `true` by default), or create the flag file `artifacts/league_models/rollout/KILL_SWITCH` (a fail-closed alarm does this by itself).
2. Verify: `python -m src.league_models.rollout status` prints `GLOBAL ONLY (KILL_SWITCH_CONFIG)` or `(KILL_SWITCH_FLAG_FILE)`.
3. Nothing is deleted: `prospective.jsonl`, `outcomes.jsonl`, `alerts.jsonl`, the audit log and the Sprint 5 artifacts stay. Re-enabling is a deliberate act: remove the flag file AND set `kill_switch: false` AND a non-zero fraction.

## Scenario table (every row has a test in `tests/test_league_models_s6.py`)
| Scenario | What the system does | Alarm | Operator action |
|---|---|---|---|
| Revoked or unknown data licence | records using it are flagged; unknown / BLOCKED source = critical, fail closed | `license:<id>` | stop shadow, confirm the terms in writing, document in the licence manifest |
| Provider 429 / 5xx / network | run ends `PROVIDER_FAILED`, zero records, no guess | `provider_error` (warning), `provider_unavailable` after 3 runs (critical, fail closed) | wait for the quota window; check the key; do not switch provider silently |
| No provider key | run ends `NOT_CONFIGURED` | `provider_not_configured` | set `FOOTBALL_DATA_ORG_API_KEY` in `.env` (never in logs or git) |
| Corrupted / missing artifact | route falls back to GLOBAL with an alarm; a broken GLOBAL artifact raises | `artifact:<model>:<target>:<league>` (global = critical) | restore from the run output; compare sha256 with the registry |
| Artifact store absent on this machine | NOT_VERIFIED, not a rollback | `artifact_store_missing` | run on the machine that holds `artifacts/league_models/s5/final` |
| Schema mismatch (old / unknown record version) | critical, fail closed | `schema_mismatch` | migrate with a new version; never edit stored records |
| Zero coverage (fixtures due and mapped, no record) | critical, fail closed | `zero_coverage` | read the last line of `shadow_runs.jsonl` (`skipped`, `model_errors`) |
| Missing / unfitted candidate | candidate skipped with its reason, global still recorded | `model_missing` | inspect the `NOT_TRAINED` reason |
| Slow inference | warning | `slow_inference` | profile; the ceiling is `shadow.max_inference_ms_per_fixture` |
| Model or input drift | reported as drift (PSI), NOT as an accuracy change | `drift_warn` / `drift_critical` (info / warning) | wait for resolved results before judging accuracy |
| Duplicate / conflicting fixture ids | run refused (`DUPLICATE_FIXTURE_IDS`); identity conflict is critical | `records_refused`, `fixture_identity_conflict` | fix the alias; never merge by hand |
| Database / disk outage | `StoreUnavailable`, nothing acknowledged | run fails; `scheduler_stale` follows | restore the disk; replays are idempotent |
| Incomplete lineups | lineups are never a feature: the flag stays `UNKNOWN` | `lineups_not_unknown` only if a record claims otherwise | none |
| Late results | pending until kick-off + 6 h; alarm only after 48 h | `outcome_lag` | check the result source |
| Candidate worse than global on >= 100 resolved pairs | rollback by policy | `performance_regression:<family>:<stage>` | keep GLOBAL; investigate offline |

## On-call checklist (daily while shadow runs)
- [ ] `python -m src.league_models.rollout status` shows the expected routing (default: GLOBAL ONLY).
- [ ] `artifacts/league_models/rollout/alerts.jsonl`: any `critical`? Is there a `KILL_SWITCH` flag file? If yes, read the reason inside it before removing anything.
- [ ] Last line of `shadow_runs.jsonl`: `status`, `provider_errors`, `records`, `model_errors`.
- [ ] `python -m src.league_models.rollout scorecard`: `resolved_fixtures` grows after matches finish; `TOO_FEW_PAIRS_NO_CLAIM` is the expected verdict until 30 pairs.
- [ ] Nothing user-visible changed: `canary.active_fraction` is 0.0 and `kill_switch` is true unless the owner changed them.
- [ ] No secret in `logs/` or the run reports (the key is read from `.env` only).

## What is NOT automated (owner decisions)
Registering the scheduled task, opening any canary stage, any API or UI change, confirming football-data.org commercial / ML terms, any paid or external-write call.
