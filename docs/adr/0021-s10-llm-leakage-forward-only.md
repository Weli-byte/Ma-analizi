# ADR 0021: S10 LLM leakage guards and the audit command

## Status
Accepted — 2026-10-01.

## Context
S10's sprint brief asks for: information cutoff, a prediction lock, an immutable response
archive, a post-kickoff mutation guard, snapshot hashing, historical/prospective experiment
separation, an audit command checking five specific leakage classes, and excluding+counting
critical leakage records from a benchmark result. S8 (ADR 0019) already built most of the
underlying mechanics (`information_cutoff`, `snapshot_hash`, `ExperimentType.HISTORICAL_BACKTEST`
/`PROSPECTIVE` via `LLMCallRecord.track`) and S0-S3's `PredictionLedger` already rejects any
change to a logical prediction's content (`LedgerConflict`). This ADR covers what was genuinely
still open.

## Decision: post-kickoff mutation guard is a REAL guard, not a relabeling

Before this ADR, `src/llm/runner.py` set `PredictionRecord.generated_at = cutoff` (the backtest
synthetic-cutoff convention) for BOTH tracks. For `HISTORICAL_BACKTEST` that is correct and
stays unchanged. For `PROSPECTIVE`, using the synthetic cutoff instead of the REAL call time
would make the guard vacuous — a stale "as of cutoff" timestamp always validates, no matter when
the call actually happened. `run_one` now sets `generated_at = call.generated_at` (the real
wall-clock response time) for `PROSPECTIVE`. `PredictionRecord`'s own validator
(`generated_at <= kickoff_utc`, already existing since S0-S3) then genuinely refuses to construct
a record for a live call made after kickoff — the match already started. The refusal is caught
(`pydantic.ValidationError`) and turned into `LLMCallRecord.status = "post_kickoff_rejected"`
rather than crashing the run or silently dropping the call's own record.

## Decision: "prediction lock" is the EXISTING `PredictionLedger`, not a new record type

`PredictionLedger._apply` already raises `LedgerConflict` on any content change for a logical
prediction (`src/schemas/lifecycle.py`, S0-S3). The sprint's named lock fields
(`fixture_id`, `information_cutoff`, `prompt_version`, `source_snapshot_hash`,
`response_timestamp`) already exist, split across `PredictionRecord`
(`fixture_id`/`information_cutoff`) and `LLMCallRecord` (`prompt_version`/`snapshot_hash`/
`generated_at`). A new `PredictionLock` schema would duplicate fields the ledger and call record
already carry for no additional guarantee — rejected (see Alternatives).

## Decision: immutable response archive stays OUT of scope for S10

`LLMCallRecord.raw_response_sha256` (S8) commits to the exact response text without storing it.
Storing the full text is a real feature (needed for reproducing exactly what a provider said)
but is a separate concern from leakage control — ADR 0019 already deferred it, explicitly to
S10, but on reflection it is orthogonal to every leakage class S10's audit actually checks (none
of them need the full text, only the hash, to detect tampering). Deferred again, to whichever
sprint first needs to literally replay a response (most plausibly S11 ensemble, if it ever wants
provider explanations) rather than invented here without a consumer.

## Decision: the audit command is defense-in-depth over PERSISTED files, not a re-derivation

Every one of the five named checks (`generated_at > kickoff`, `cutoff > kickoff`, `feature
available_at > cutoff`, missing provider timestamp, changed prediction) is ALREADY enforced by a
schema validator at construction time, or (for the feature-layer check) by
`src.features.leakage_audit` at the feature-computation layer, which `src.llm.snapshot` inherits
by only ever reading already-leakage-safe `EvalRow.features` (ADR 0019). A file written by this
repo's own code therefore cannot fail these checks. `src/llm/audit.py` exists to catch a
DIFFERENT failure mode: a hand-edited, corrupted, or externally-modified `predictions.jsonl`/
`calls.jsonl` on disk. It re-parses each line through the real schema (`PredictionRecord.from_json`,
which already does hash-tamper detection; `LLMCallRecord.model_validate`), classifies the
resulting error by message substring into one of the five+ named kinds, and reports violations
grouped by severity. The `available_at > cutoff` check specifically is NOT re-implemented here —
it already runs inside `build_features` (which `run_llm_cli` depends on transitively) and
re-deriving it from a persisted snapshot hash alone is not possible (a hash doesn't carry the
original per-feature `available_at` metadata); this is documented, not silently skipped.

## Decision: critical-leakage exclusion

`src.llm.audit.partition_clean(predictions, violations)` removes any `PredictionRecord` whose
`fixture_id` appears in a CRITICAL violation (`CRITICAL_KINDS`: tampered content, an
unparseable/invalid record, a missing call timestamp) and returns `(clean, excluded_count)` —
the count is always returned, never silently dropped. Non-critical findings (currently none are
classified non-critical in practice, since every implemented check here indicates a real problem
by construction) would still surface in the report without exclusion.

## Decision: historical/prospective separation stays structural, not leaderboard-merged

`src/llm/cli.py` only runs `HISTORICAL_BACKTEST` today (writes to `artifacts/llm_runs/`,
separate from `artifacts/runs/`'s classical-model S9 leaderboard). `PROSPECTIVE` has no live
fixture feed to run against yet (S13/S14). The separation the sprint asks for ("historical
classic-model backtest ile prospective LLM benchmarki ayri experiment type olarak tutulmali") is
therefore already true BY CONSTRUCTION — there is no shared leaderboard mixing the two, and
`LLMCallRecord.track` makes the distinction queryable wherever LLM results ARE read. Wiring LLM
results into S9's `leaderboard.py` (which is track-agnostic today, since it only ever sees
classical-model `ModelResult`s) is left for whenever `PROSPECTIVE` actually produces results —
at that point, track must become an explicit `scope` dimension, the same way `league`/`season`
are, so a historical replay's memorization-risk numbers can never silently blend into a genuine
forward-looking benchmark.

## Alternatives considered
- **A new `PredictionLock` schema** — rejected: duplicates fields already on
  `PredictionRecord`/`LLMCallRecord`/`PredictionLedger` with no new guarantee; the ledger's
  existing `LedgerConflict` already IS the lock.
- **Store full response text in `LLMCallRecord`** — rejected again (ADR 0019 already deferred
  it): no consumer needs it yet, and the hash already supports tamper detection.
- **Re-derive `available_at > cutoff` from an audit scan** — rejected: not reconstructable from a
  hash; the real check already runs at feature-computation time and is documented as such here
  rather than faked with a weaker proxy.
