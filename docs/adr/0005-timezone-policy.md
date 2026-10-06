# ADR 0005: Timezone policy

## Status
Accepted — 2026-09-26.

## Context
football-data.co.uk publishes kickoff times as UK wall-clock times. S1 converted them but stored naive
timestamps in DuckDB, and DST edge cases were untested. Reading TIMESTAMPTZ back returned the machine's
local timezone (observed: `Europe/Istanbul`).

## Decision
- Source timezone is per league config (`source_timezone`); conversion is `src/data/timezones.py`
  (`CONVERSION_VERSION = tzconv-1`, recorded in dataset meta).
- Canonical data is UTC-aware (`TIMESTAMPTZ`); every connection sets `TimeZone='UTC'` (`open_db`).
- Nonexistent (spring-forward gap) and ambiguous (autumn fold) wall-clock times are REJECTED
  (`invalid_local_time`), never guessed.
- Rows without a time get 00:00 UTC of the match day and `kickoff_time_known=false`; that is earlier than
  any real kickoff, hence leakage-conservative for cutoffs.
- Verified with known kickoffs: UK winter/summer, Spain summer/winter, DST transition days, midnight and
  year boundaries (`tests/test_timezones.py`).

## Alternatives
Treat all times as UTC (rejected: wrong by 1–2 h); store naive local times (rejected: ambiguous).

## Consequences
The assumption "source times are UK time for Spanish matches too" is consistent with checked examples
(Athletic–Barcelona 2019-08-16) but not proven for every match. Any conflicting source must get its own
`source_timezone`.
