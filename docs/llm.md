# LLM probability benchmark (S8, `src/llm/`, ADR 0019)

Run: `python -m src.llm.cli --provider openai [--root DIR] [--limit N]` (also `anthropic`,
`google`). Disabled by default — enable in `configs/provider.yaml` (`enabled: true`) and set the
named environment variable (e.g. `OPENAI_API_KEY`); nothing is ever hardcoded. A real run costs
money (provider API billing) and is the project owner's call, not automatic.

Outputs: `artifacts/llm_runs/<data_version>_<provider>_<model>/` (gitignored): `predictions.jsonl`
(immutable `PredictionRecord`s, one per successful call — same schema every other model uses),
`calls.jsonl` (every `LLMCallRecord`, including failures), `summary.md`, `hashes.json`.

## What gets sent

`src/llm/snapshot.py::build_snapshot` serializes ONLY: fixture/league/season/kickoff identifiers,
`information_cutoff`, team IDs, every already-computed, already-leakage-safe
`EvalRow.features` value that is not `None`, and odds. It never reads `EvalRow.outcome`,
`home_goals`, or `away_goals` — tested explicitly (`test_snapshot_never_carries_outcome_or_goals`).
It adds no new leakage-control logic of its own; it inherits whatever `src.features` already
enforced.

## Output contract

Strict JSON only: `{"home_probability", "draw_probability", "away_probability", "confidence",
"short_reasoning"}`. `src/llm/parse.py` tolerates a fenced code block, renormalizes probabilities
within 1e-3 of summing to 1 (provider rounding), and rejects anything else —
`src/llm/runner.py` retries up to `max_retries` (default 2), then records
`status="malformed_json_exhausted"` with no `PredictionRecord` produced.

## Providers

`src/llm/providers.py` — `OpenAIProvider`/`AnthropicProvider`/`GoogleProvider`, thin
`urllib.request` adapters (no vendor SDK dependency; see ADR 0019 for why). Cost is a static,
documented per-1K-token rate table (`COST_PER_1K_TOKENS_USD`) — an estimate, never a real
invoice; an unrecognized model costs exactly `$0.0`, never a guess.

## Historical vs prospective (memorization risk)

Every `LLMCallRecord` carries `track` (`ExperimentType.HISTORICAL_BACKTEST` or `PROSPECTIVE`).
For a historical replay, `PredictionRecord.generated_at` follows this repo's existing backtest
convention (`= information_cutoff`), but `LLMCallRecord.generated_at` is the REAL time the
response was received — necessarily long after the match's `kickoff_utc`. The LLM's training
data may already contain that match's real result. This gap is reported, not hidden; S10 closes
it with a prediction lock and post-kickoff mutation guard. `src/llm/cli.py` only runs the
historical validation split today — there is no live fixture feed yet (S13/S14).

## Tests

`tests/test_llm.py` — every provider call is mocked (`_post_json` or the `Provider` instance
itself); no network access or API key is needed to run the suite, per the sprint's own
instruction. Covers: snapshot leakage exclusion, prompt determinism, strict-JSON parse
(accept/reject/renormalize), all three provider adapters' request/response shape, retry and
exhaustion, provider-error handling, `ProviderConfig` wiring (`resolve_provider`), and an
end-to-end CLI run against the golden fixture project.
