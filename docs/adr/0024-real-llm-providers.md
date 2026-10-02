# ADR 0024 — Real LLM providers, no mock path (supersedes the provider parts of ADR 0019)

Status: accepted · 2026-10-01 · owner instruction ("REAL AI" master prompt)

## Decision
- Every provider adapter calls the REAL vendor API through the vendor's **official SDK**:
  OpenAI `responses.create` (Responses API, strict `json_schema`), Google `google-genai`
  `models.generate_content` (`response_json_schema`), Anthropic `messages.create`
  (`output_config.format`). `src/llm/providers/{openai,gemini,anthropic}_provider.py`.
- **No mock/fake/simulated provider exists.** The earlier `urllib` adapters and every test that
  monkeypatched `_post_json`/`urlopen` or used a fake provider class were deleted.
  Provider behaviour is proven by (a) real calls: `python -m src.llm.live_smoke`,
  `pytest -m live tests/integration`, and (b) `REAL_PROVIDER_CAPTURE` fixtures recorded from
  those calls (`tests/fixtures/real_provider_captures/`). A provider is operational only after
  a real response was received and schema-validated. No key => `NOT_CONFIGURED`, never PASS.
- Normalized result (`LLMResponse`): provider, model, request_id, created_at_utc, latency_ms,
  input/output/total tokens, raw_response_hash, retry_count. A field the vendor did not report is
  `None`, never 0. Cost is an estimate from `configs/pricing.yaml` (versioned, timestamped);
  unknown model => `None`.
- Error taxonomy + retry (`providers/base.py`): 429, 401/403, 400, 404, 408, 5xx, network,
  content-policy, schema. Only 429/timeout/5xx/network retry, with exponential backoff + jitter,
  bounded by `budget.retry_limit` (SDK-internal retries disabled so `retry_count` is true).
  Messages are scrubbed of key-shaped strings.
- **Budget pre-flight** (`budget.py`, `configs/provider.yaml: budget`): worst case (every request
  retried, full `max_output_tokens`) is computed BEFORE any call; exceeding any limit raises and
  nothing is called. The historical CLI additionally needs `ALLOW_REAL_LLM_CALLS=true`, else it
  prints `REAL_CALLS_DISABLED_BY_OPERATOR` (exit 4) and the benchmark is NOT complete.
- **Contract** (`contract.py`, `forecast-output-v1`): Pydantic. Probabilities in [0,1], sum to 1
  within 1e-3 (provider rounding); inside the tolerance they are divided by their sum for the
  PredictionRecord, outside it the output is REJECTED. Raw response is hashed.
- **Prompt versioning** (`prompt.py`): `prompt_id`, `prompt_version` (`llm-prompt-v2`, bumped from
  v1 because the system/user split and the optional-field schema change model-visible wording),
  system/user prompt hashes, schema version. The system prompt hash is pinned in a test.
- **Cutoff gate** (`snapshot.audit_snapshot`) runs before every call; a PROSPECTIVE request at or
  after kickoff is refused before the provider is contacted (no cost, no prediction).
- Failure policy: a failed call yields no prediction; nothing is substituted.
- Gemini provider key is `GEMINI_API_KEY`; provider name is `gemini` (was `google`).

## Models / prices (verified from official docs 2026-10-01)
`gpt-6-luna` ($0.10 / $0.50 per 1M in/out), `gemini-3.1-flash-lite` ($0.25 / $1.50).
Anthropic model: not yet chosen (no key available to verify).

## Consequences
New direct dependencies: `openai`, `anthropic`, `google-genai`. Live tests are excluded from
the default run (`-m "not live"`); the live smoke costs about $0.0004.

## Addendum 2026-10-02 — real end-to-end forecast (phase 39)
`python -m src.llm.forecast` (needs `ALLOW_REAL_LLM_CALLS=true`) forecasts one REAL upcoming
fixture from football-data.org: team names resolved through the existing `TeamDirectory`
(source `football-data-org`; two aliases were approved for the first run, owner to re-confirm),
leakage-safe features at `information_cutoff = now`, cutoff audit, budget pre-flight, one real
call per keyed provider, immutable PredictionRecords + raw responses persisted. PROSPECTIVE
cutoff must not be in the future. First run: Arsenal FC vs Leeds United FC (2026-10-10), OpenAI,
Gemini and Groq all returned valid forecasts (about $0.0008 total). Known limit: the
football-data.co.uk history ends 2026-08-27, so newer results are not in the features.
`max_total_tokens` raised to 15000 because real snapshots are ~1k tokens.

## Addendum 2026-10-02 (2) — `retry-after`, output budget
The first 20-match benchmark had Groq at 40% coverage: free-tier 429s were retried after only
1-2 s. `ProviderError.retry_after_s` now carries the server's `retry-after` header and
`with_retries` waits at least that long (capped at 60 s; base backoff 2 s). `max_output_tokens`
raised 400 -> 800 because one OpenAI response was truncated by reasoning tokens. The rerun reached
60/60 requests. Budget raised by the owner for this run (`max_requests_per_run` 60,
`max_total_tokens` 500000, worst-case `max_estimated_cost_usd` 0.15; real cost $0.021).
