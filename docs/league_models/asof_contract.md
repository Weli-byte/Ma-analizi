# As-of contract (Sprint 1, task 4)

Code: `src/league_models/asof.py`. Tests: `tests/test_league_models_s1.py`. Stage cutoffs are the S13 snapshot stages
(`src/snapshot/stages.py`: t-24h, t-90m, t-30m and kickoff = T-5m), not a second definition.

## Two clocks, checked separately
Every piece of source information used for a prediction is a `SourceObservation` with:

| Field | Meaning |
|---|---|
| `prediction_as_of_utc` | the moment the prediction is made (a stage cutoff); never after kick-off |
| `fixture_kickoff_utc` | kick-off of the fixture being predicted |
| `source_event_time_utc` | when the event happened (the match was played, the quote was taken) |
| `source_available_at_utc` | when OUR system could first have seen it |
| `available_at_source` | `observed` (a real timestamp) or `inferred` (a rule such as kick-off + 3 h, ADR 0006) |
| `ingest_time_utc` | when it was stored |
| `source_version`, `content_hash` (sha256), `raw_snapshot_pointer`, `quality_flags` | lineage |

`check_observation` returns violations `EVENT_AFTER_AS_OF`, `AVAILABLE_AFTER_AS_OF` and `STAGE_FORBIDDEN`. Both clocks must be
`<= prediction_as_of_utc`. Information is never replayed backwards: an updated score, odds quote or lineup is a NEW observation
with its own `source_available_at_utc`.

## BACKTEST_LIMITED
The permitted sources (openfootball) publish no result timestamp, so the availability of every historical result is
**inferred** (kick-off + `result_lag_hours` = 3). Historical as-of correctness can therefore not be proven for any training
row: all rows carry the label `BACKTEST_LIMITED` (`quality_label`), as the plan requires. Only observations with an observed
timestamp (our own snapshots since S13) can be `AS_OF_VERIFIED`.

## What each stage may see
| Field group | t-24h (T-1440m) | t-90m (T-90m) | t-30m (T-30m) | kickoff (T-5m) | Rule |
|---|---|---|---|---|---|
| `historical_results` | ALLOWED | ALLOWED | ALLOWED | ALLOWED | only finished matches with source_available_at <= as_of |
| `schedule_and_venue` | ALLOWED | ALLOWED | ALLOWED | ALLOWED | kick-off time as published at as_of; a later change is not replayed |
| `team_form_windows` | ALLOWED | ALLOWED | ALLOWED | ALLOWED | 5/10 matches, 30/90/365 days from earlier matches only; same-day and postponed matches ordered |
| `elo_and_strength_ratings` | ALLOWED | ALLOWED | ALLOWED | ALLOWED | computed from matches available at as_of; per-league and per-tournament scales kept apart |
| `injuries_and_suspensions` | CONDITIONAL | CONDITIONAL | CONDITIONAL | CONDITIONAL | only records observed at or before as_of; status labels, never back-dated |
| `lineups_confirmed` | FORBIDDEN | CONDITIONAL | ALLOWED | ALLOWED | an unannounced starting eleven can never be a t-24h input; t-90m only if already published |
| `referee_assignment` | CONDITIONAL | CONDITIONAL | ALLOWED | ALLOWED | only if announced and observed at or before as_of |
| `odds_exact_observed` | CONDITIONAL | CONDITIONAL | CONDITIONAL | CONDITIONAL | only quotes with timestamp_quality exact and observed_at <= as_of |
| `odds_closing` | FORBIDDEN | FORBIDDEN | FORBIDDEN | CONDITIONAL | never a feature; at kick-off only as a reference baseline (REFERENCE_MARKET_BASELINE) |
| `live_score_state` | FORBIDDEN | FORBIDDEN | FORBIDDEN | FORBIDDEN | pre-match stages never see an in-play state |
| `match_statistics_current_match` | FORBIDDEN | FORBIDDEN | FORBIDDEN | FORBIDDEN | shots, corners, cards, xG of the fixture being predicted are post-match facts |

`ALLOWED` still requires both time checks. `CONDITIONAL` needs the stated rule. `FORBIDDEN` is never allowed at that stage.
An unannounced starting eleven can never be a t-24h input; closing odds are never a feature.

## Training-row lineage
`TrainingRowLineage(row_id, prediction_as_of_utc, max_source_available_at_utc, row_hash, quality_label)` refuses construction when a
source used by the row became available after `as_of`. `row_hash` is the sha256 of the row's stable JSON.

## Temporal audit (100% of fixtures and stages)
`temporal_audit` checks EVERY (fixture, stage) pair with a sorted index: no history element is known after the cutoff, the
fixture's own result is never in its own history, no match kicked off at or after the cutoff is in it, and no match is "known"
before its own kick-off. A second plain list-scan implementation re-checks a deterministic sample and must agree. Real result:
`docs/league_models/temporal_audit.json` (0 violations, 0 disagreements).
