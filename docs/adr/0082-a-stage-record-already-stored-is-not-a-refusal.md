# ADR 0082 — A stage record that is already stored is not a refusal (2026-10-27)

## Finding
The cloud shadow loop (ADR 0076, rounds every 10 minutes) produced a CRITICAL `records_refused` alert and a Telegram message on 2026-10-26. Cause: the `t-24h` window of a fixture spans many rounds.
A result that arrives between two rounds lengthens the fitted history, so the snapshot hash and with it the content hash of the same fixture / stage / model version / family differs.
`ProspectiveStore.put` correctly refuses to overwrite (records are immutable), but the run ended `RECORDS_REFUSED` and monitoring called it critical although nothing was wrong: the stage had its record.
The run of 2026-10-26 21:01 UTC counted 14 such conflicts (fixtures fdorg-558555, fdorg-558557) next to 2 `EXISTS`.

## Decision
- In `run_shadow`, a record whose key (fixture | stage | model version | family) is already stored with a different content hash is counted as `EXISTS` and skipped; `report["stage_already_recorded"]` counts them. The first prediction of the stage stands (the same rule as before: the stage record is the first one made inside its window).
- Nothing is overwritten, `ProspectiveStore.put` still raises on a content conflict (the guard stays, `run_shadow` no longer reaches it for this case), a new model version still creates a new record, and an identical replay still goes through `put` (so the score-forecast sidecar is still completed).
- `RECORDS_REFUSED` now means a record was refused by validation (for example a post-kickoff stamp).

## Not changed
No model, calibration, gate or stored record. The pre-registered gates are untouched.
