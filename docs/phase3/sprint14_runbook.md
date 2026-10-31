# Sprint 14 alarm runbook and post-incident template (ADR 0092)

Owner of every alarm: the project owner (single-person team); escalation = the owner's Telegram (ADR 0077). Severity: **critical** = a served number may be wrong or stale; **warning** = a
source or a statistic moved and must be looked at; **info** = reported, no action.

| Alarm (from `python -m src.drift.report`) | Severity | First action | Stop / roll back |
|---|---|---|---|
| `freshness:live_record` or `freshness:forecast` | critical | open the last `markets-cloud` run; rerun it by hand | the site keeps the last forecast with its time and stage; nothing is presented as new |
| `freshness:results:<league>` | warning | check the league's sources (football-data.org, ESPN settlement, openfootball); a postponed round is not a fault | none needed: an unsettled match is never scored |
| `freshness:odds` | warning | provider quota (`odds-exact` logs: `SKIPPED, below the reserve`); wait for the renewal or a paid plan | the headline falls back to the model where no exact odds exist (existing rule) |
| `schema` | critical | compare the file with the reader's required fields; a provider change is fixed in the adapter, never by editing stored files | revert the adapter change |
| `duplicates:results` / `consistency` | critical | inspect the two records; a merge or alias fix goes through review (ADR 0057) | the live record excludes the fixture until fixed |
| `coverage:<league>` | warning | did a source stop listing the league (403, schedule change)? | none: no forecast is invented |
| `input_drift:<feature>` / `input_drift:league_mix` | warning | read the shares and means in the report; a new league or a season start explains most moves | none: drift alone never changes a model |
| `performance_drift:<market>` | critical | compare with the trivial baselines and the market; build a retrain candidate (`python -m src.drift.retrain`) | the candidate stays SHADOW; promotion only through the sprint 17 gate with a human approval and the current model as rollback target |

Thresholds: `docs/phase3/s14_thresholds.json` (validation history, margin 1.25); minimum 100 rows per window, 4 ISO weeks per performance window; one announcement per alarm key per day.

## Post-incident review (copy for each incident)
- What happened, when (UTC), how it was noticed (alarm key or user report):
- What users saw (stale / wrong / missing) and for how long:
- Root cause (evidence: run id, file, commit):
- What was done to stop it, what to recover it:
- What changes so it cannot recur (test, check, alarm), with the PR:
- Was any stored forecast or result changed? (must be "no": corrections are new records)
