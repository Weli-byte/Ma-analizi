# ADR 0006: Match lifecycle and result availability

## Status
Accepted — 2026-09-26.

## Context
S2 assumed `result_available_at = kickoff + 3h` as a hidden constant; fixtures had only
scheduled/live/finished/postponed/cancelled statuses.

## Decision
- `FixtureStatus`: scheduled, postponed, in_progress, finished, abandoned, cancelled, rescheduled.
- `Fixture` fields: `kickoff_utc` (= scheduled kickoff, also `scheduled_kickoff_utc`),
  `actual_kickoff_utc`, `finished_at_utc`, `result_available_at_utc`,
  `result_available_at_source ∈ {observed, inferred}`. FINISHED requires a score, an availability time and
  its source; other statuses cannot carry results.
- Historical data has no publication time: `result_available_at_utc = kickoff + result_lag_hours`
  (`configs/features.yaml`, default 3) with source **`inferred`**. It is a PROVISIONAL research policy and
  must never be presented as observed truth. Live ingestion (S12) must supply observed times.
- History (`MatchHistory`) admits only FINISHED matches with a known availability time
  `<= information_cutoff`. Postponed/cancelled/abandoned/scheduled matches never enter features.

## Alternatives
Use kickoff as availability (rejected: leaks results of running matches).

## Consequences
For matches that overrun 3 h (rare) inferred times are too early: a small residual leakage risk that only
observed timestamps can remove.
