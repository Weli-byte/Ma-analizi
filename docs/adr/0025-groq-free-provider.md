# ADR 0025 — Groq as a free-tier real provider

Status: accepted · 2026-10-02

Anthropic key is deferred about one month (owner has no budget); Anthropic stays `NOT_CONFIGURED`,
disabled in `configs/provider.yaml`. Groq (official `groq` SDK, `openai/gpt-oss-20b`, strict
`json_schema`, $0.075/$0.30 per 1M tokens, free tier available) is added as a fourth REAL
provider under the same rules as ADR 0024: real calls only, budget pre-flight, no fallback to
another provider. Free-tier rate limits surface as 429 and are retried with backoff.
Caveats: free-tier terms/data-use unverified for commercial use (RESEARCH_ONLY, like the data).
Key: `GROQ_API_KEY` in `.env`/environment. `max_total_tokens` raised 6000 -> 9000 for 3 providers.
