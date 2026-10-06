# ADR 0019: S8 LLM probability benchmark — adapter design, JSON contract, track separation

## Status
Accepted — 2026-10-01.

## Context
`CLAUDE.md`'s sprint order reaches S8: benchmark GPT/Claude/Gemini's own 1X2 probability
estimate under the same structured snapshot every statistical/ML model in this repo already
uses, without adding a new feature-computation or leakage-control surface (that already lives
in `src.features`). `src/config/__init__.py::ProviderConfig` was RESERVED for this sprint since
the S0-S3 remediation; this ADR is the first time it is actually consumed.

## Decisions

**Adapters, not vendor SDKs.** `src/llm/providers.py` implements OpenAI/Anthropic/Google as thin
`urllib.request` wrappers behind one `Provider` protocol (`complete(prompt, model, api_key) ->
LLMResponse`), rather than adding `openai`/`anthropic`/`google-generativeai` as dependencies.
Rationale: zero new third-party packages (this project's dependency surface is already large per
S6's GBM stack and the lock-drift incidents in the hardening pass), and the three REST contracts
are small and stable enough that a vendor SDK buys little. Every HTTP call goes through one
`_post_json` seam, which is exactly what tests mock — no network access or API key is needed to
exercise this package (`tests/test_llm.py`), matching the sprint's own instruction.

**`PredictionRecord` is reused, not reinvented.** An LLM's 1X2 call becomes an ordinary
`PredictionRecord` (`model_id=f"llm_{provider}_{slugified_model}"`, `model_version` = this
runner's own semver, since `PredictionRecord.model_version` is regex-constrained to semver and
provider model strings like `gpt-4o` are not). It flows through the SAME `PredictionLedger`,
and will flow through the SAME `metrics`/leaderboard machinery once S9 computes one. A separate
`LLMCallRecord` (`src/schemas/llm.py`) carries the call-level facts that must NEVER be part of a
prediction's content hash: latency, token counts, estimated cost, retry count, and critically
`track` (historical vs prospective).

**Strict JSON contract with bounded retry.** `src/llm/parse.py` accepts only the documented
schema (`home_probability`/`draw_probability`/`away_probability`/`confidence`/
`short_reasoning`), tolerates a fenced code block (providers do this despite being told not to),
renormalizes probabilities that sum to 1±1e-3 (provider-side rounding), and rejects everything
else. `src/llm/runner.py` retries up to `max_retries` (default 2) on a malformed body before
giving up and recording `status="malformed_json_exhausted"` with NO `PredictionRecord` (a failed
call is never silently coerced into a fake prediction).

**Historical vs prospective track, two different `generated_at` semantics.** Reusing
`ExperimentType.HISTORICAL_BACKTEST`/`PROSPECTIVE` (already defined, S0-S3) instead of a new
enum. `PredictionRecord.generated_at` follows the SAME backtest convention every model in this
repo already uses (`= information_cutoff`, a synthetic "as of" time, so the record's own
`generated_at <= kickoff_utc` invariant holds and metrics compare like-with-like).
`LLMCallRecord.generated_at` is the REAL wall-clock time the response was received — for a
HISTORICAL_BACKTEST call this is necessarily long after `kickoff_utc`, which is EXACTLY the
memorization-risk gap: the LLM's training data may already contain the real result for a replayed
historical match. S8's job is to make this gap queryable and reported, not to close it (S10 does
that with prediction locks and a post-kickoff mutation guard).

**No live/prospective data source yet.** `src/llm/cli.py` only runs the historical validation
split (this repo has no live fixture feed; that is S13/S14 scope). The runner itself already
supports `PROSPECTIVE` for when that exists, tested with a synthetic row in `tests/test_llm.py`,
so S13/S14 do not need to touch `src/llm`.

**Cost is a static, documented estimate, never a real invoice.** `COST_PER_1K_TOKENS_USD` is a
small hardcoded table reviewed at implementation time, not fetched live. An unknown
provider:model pair costs exactly `$0.0` rather than guessing — explicit absence, never a
fabricated number.

## Consequences
- Running this for real costs money and needs the project owner's own API keys
  (`configs/provider.yaml`, `enabled: false` by default, keys from environment only, never
  committed). Nothing in this ADR or its commit enables a provider or makes a real call.
- `ProviderConfig.providers`/`ProviderEntry.*` are no longer `RESERVED_FIELDS` — removed from
  that set in the same commit, now genuinely consumed by `src.llm.runner.resolve_provider`.
- S9 (calibration/leaderboard) can treat `llm_*` model_ids exactly like every other model — no
  special-casing needed in `metrics`/`runner`/`walk_forward`.
- S10 (LLM leakage) extends this ADR, not replaces it: prediction lock, immutable response
  archive, post-kickoff mutation guard, and the audit command that flags `LLMCallRecord`s whose
  `track=HISTORICAL_BACKTEST` and real `generated_at` are far enough past `kickoff_utc` to be a
  genuine memorization concern.

## Alternatives considered
- **Vendor SDKs** (`openai`, `anthropic`, `google-generativeai`) — rejected for now: large new
  dependency surface, version-pinning risk (this pass already hit lock-drift twice), and three
  stable REST contracts are a small enough surface for thin adapters. Revisit if a provider
  changes its API in a way that makes hand-rolled requests fragile.
- **A new `LLMPredictionRecord` schema instead of reusing `PredictionRecord`** — rejected: every
  downstream consumer (ledger, metrics, walk-forward) already knows how to handle
  `PredictionRecord`; a parallel schema would need its own ledger/metrics wiring for no benefit.
- **Store the full raw response on `LLMCallRecord`** — rejected for S8: only a sha256 is kept
  here; the project's own stated plan hands "immutable response archive" to S10 explicitly, and
  storing full text in every call record now would be redundant with that later archive.
