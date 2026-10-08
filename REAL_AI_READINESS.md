# REAL AI READINESS (verified 2026-10-08)

| Provider | State | Evidence |
|---|---|---|
| OpenAI (Responses API, strict json_schema) | REAL, verified | live tests green in CI (`live-ai` run 37752169709); 20-match benchmark; real forecast Arsenal-Leeds |
| Google Gemini (google-genai) | REAL, verified | same |
| Groq (gpt-oss-20b, free) | REAL, verified; ~27% of calls hit free-tier 429 (retried with `retry-after`) | same; coverage reported per model |
| Anthropic | NOT_CONFIGURED: no API key (owner plans one in about a month). Adapter exists; never called, so never claimed verified | `tests/integration/test_anthropic_live.py` fails loudly without a key |

- No mock/simulated/random provider exists in code or tests. Tests use real captured responses
  (`tests/fixtures/real_provider_captures/`) or real calls (`pytest -m live tests/integration`, weekly
  `live-ai.yml` with repository secrets).
- Real calls need `ALLOW_REAL_LLM_CALLS=true` plus a budget (`configs/provider.yaml`, prices in `configs/pricing.yaml`).
  Observed spend so far is a few cents (dashboard "LLM usage & cost", estimates, not invoices).
- Historical benchmark (20 matches/model): HISTORICAL track, the models may have memorized results, n is small,
  no winner is declared. Prospective (forward-only) evaluation needs real elapsed time.
