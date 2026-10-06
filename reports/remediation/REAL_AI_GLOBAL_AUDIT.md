# REAL_AI_GLOBAL_AUDIT (Phase A — audit only, no code changed)

Date: 2026-10-01 · Branch: remediation/s0-s3 · Scope: master prompt "ULTRA PRO MAX — REAL AI / GLOBAL DATA".

## Credential inventory (values never recorded)

| Provider | Expected env var (repo) | Expected env var (prompt) | Present on this machine |
|---|---|---|---|
| OpenAI | `OPENAI_API_KEY` | `OPENAI_API_KEY` | YES (shell env) |
| Anthropic | `ANTHROPIC_API_KEY` | `ANTHROPIC_API_KEY` | NO |
| Gemini | `GOOGLE_API_KEY` | `GEMINI_API_KEY` | `GEMINI_API_KEY` YES; `GOOGLE_API_KEY` NO |
| football-data.org | `FOOTBALL_DATA_ORG_API_KEY` | — | YES (`.env`, gitignored) |

`.env` holds only the football-data.org key. OpenAI/Gemini keys exist only in the process environment.
Anthropic live status: `NOT_CONFIGURED` until the owner supplies a key.

## Findings

| ID | Current state | Root cause | Required change | Affected files | Test strategy | Op. risk | Cost risk | Licensing risk | Status |
|---|---|---|---|---|---|---|---|---|---|
| A-01 | Adapters are real HTTPS via `urllib`, but OpenAI uses legacy `/v1/chat/completions` | S8 chose zero-SDK | Move OpenAI to Responses API, Anthropic to current Messages, Gemini to current official API, using official SDKs (`openai 2.45`, `anthropic 0.116` already installed; add `google-genai`) | `src/llm/providers.py` -> `src/llm/providers/*.py` | Live smoke tests | Med | Low | Low | OPEN |
| A-02 | Tests monkeypatch `_post_json` / `urlopen` (`tests/test_llm.py` L117-182, runner/CLI tests) | S8 spec said "mock provider calls" | Violates user rule. Replace with `REAL_PROVIDER_CAPTURE` fixtures + live tests | `tests/test_llm.py`, `tests/test_llm_audit.py` | Capture real responses once, parse offline; live tests in `tests/integration/` | Low | Low | Low | OPEN |
| A-03 | `configs/provider.yaml` models = `TBD`, all disabled; Gemini key var `GOOGLE_API_KEY` | Never operated | Verify current model IDs from official docs; align Gemini var to `GEMINI_API_KEY` (keep `GOOGLE_API_KEY` as documented alias only if explicit) | `configs/provider.yaml`, `src/config` | Config tests | Low | Low | Low | OPEN |
| A-04 | No budget config (max_requests/tokens/cost/concurrency/timeout/retry) | Not in S8 | Add to config; pre-flight fail before any call | `src/config`, `src/llm/runner.py` | Pure-function pre-flight tests | Med | HIGH | Low | OPEN |
| A-05 | Cost = hardcoded static table inside `providers.py`, outdated models (gpt-4o, claude-3-5, gemini-1.5), unknown model -> `0.0` (fabricated zero) | Quick estimate | Central pricing file with version+timestamp; unknown -> `NULL` | `providers.py`, new `configs/pricing.yaml` | Unit tests | Low | Med | Low | OPEN |
| A-06 | Missing usage fields default to `0` (`usage.get(..., 0)`) | Convenience | Must be `NULL/UNKNOWN`, never 0 | providers | Unit tests | Med | Low | Low | OPEN |
| A-07 | No request_id, raw_response_hash, retry_count, error class, created_at in normalized result | Minimal `LLMResponse` | Extend normalized result per Phase 1 | providers, `src/schemas/llm.py` | Schema tests | Low | Low | Low | OPEN |
| A-08 | Retry only on malformed JSON; no 429/5xx backoff/jitter, no error taxonomy | Not built | Error classification + exponential backoff + jitter | providers/runner | Capture-based classification + live | Med | Med | Low | OPEN |
| A-09 | Output via free text + fenced-JSON parse, silently renormalizes within 1e-3 | S8 parse design | Provider structured output + Pydantic; documented normalization policy | `src/llm/parse.py`, schemas | Schema tests | Med | Low | Low | OPEN |
| A-10 | Prompt has no id/version/hash/schema_version | Not built | Prompt registry + hashes; ADR | `src/llm/prompt.py` | Hash stability tests | Low | Low | Low | OPEN |
| A-11 | Snapshot serializer exists; no AVAILABLE_AT_CUTOFF vs POST_CUTOFF split / pre-call audit | S8 partial | Pre-call availability audit, refuse call on violation | `snapshot.py`, `runner.py` | Leakage tests | Med | Low | Low | OPEN |
| A-12 | No `live_smoke`, `benchmark` commands, no `ALLOW_REAL_LLM_CALLS` gate | Not built | Implement; `REAL_CALLS_DISABLED_BY_OPERATOR`, `LIVE_AI_READY` | `src/llm/live_smoke.py`, `benchmark.py` | Live smoke | Low | HIGH | Low | OPEN |
| A-13 | `LLM_REAL` model class, per-provider calibration, ensemble wiring absent | Not wired by design (S11) | Wire only successful real records; coverage report | `src/evaluation/*` | Existing S9/S11 tests + new | Low | Low | Low | OPEN |
| A-14 | S12 `FixtureProvider` only; no Lineup/Injury/Event/Odds/Statistics/xG interfaces, no freshness/health | S12 scope | Add interfaces with coverage/timestamp/rate-limit/license metadata; never fake | `src/ingestion/` | Contract tests | Low | Low | MED | OPEN |
| A-15 | No commercial data provider decision | Owner budget | Comparison framework only; no purchase | `docs/data_sources/provider_evaluation.md` | Doc review | Low | Low | HIGH | OPEN (owner decision) |
| A-16 | S13–S19 absent (snapshots, live, odds, MLOps, dashboard, API, commercial) | Not started | Implement per phases, each with ADR | many | Per-sprint | Med | Low | Med | OPEN |
| A-17 | Research vs production data separation not structural | Single pipeline | `src/data/research` / `production` split | `src/data` | Import-boundary test | Low | Low | HIGH | OPEN |

## Blockers needing the owner

1. **Anthropic API key** — needed at Phase C. Without it: `NOT_CONFIGURED`, `REAL_AI_READY=FALSE`.
2. OpenAI + Gemini live calls spend real money: owner to confirm a small smoke budget (proposal: max 3 calls, < $0.05 total).
3. Commercial data vendor (lineups/injuries/live/odds/xG) — owner decision; S13 lineup, S14 live, S15 odds cannot be real without it. Will report `NOT_CONFIGURED`, never fake.
4. Plan conflict: CLAUDE.md "implement ONLY the current sprint's scope" vs this prompt (S13–S19). Treating this prompt as the explicit owner override.
