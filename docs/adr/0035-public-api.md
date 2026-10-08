# ADR 0035 — Public API (S18)

Status: accepted · 2026-10-08

## Decision
- FastAPI app `src.api.app:create_app` (run: `uvicorn src.api.app:create_app --factory`), versioned under `/v1`,
  OpenAPI at `/v1/openapi.json`, docs at `/v1/docs`. Routes: fixtures, fixture, fixture predictions, models,
  benchmarks, team forecast, value-research, health.
- Service layer (`src/api/service.py`) is the only data access; it reuses the dashboard's artifact readers.
  No storage structure is exposed. Every prediction carries prediction_id, model_version, data_version,
  feature_version, generated_at, information_cutoff, provider and prompt_version (None = unknown, never invented).
- Auth: `X-API-Key`, accepted keys in `FORECAST_API_KEYS` (comma separated, compared by SHA-256 digest with
  `hmac.compare_digest`; no key in code or logs). Fail closed: no configured key -> 503 on data routes.
- Rate limit: fixed window per key (default 60/min, `FORECAST_API_RATE_PER_MIN`), 429 + `Retry-After`.
  In-process state: a multi-worker deployment needs a shared store (open item for Phase O/S19).
- Pagination `limit` 1..100 + `offset`; one error schema `{"error": {code, message, request_id}}`.
- `/v1/value-research` is paper-only: edge/EV only for COMPLETE `exact` quote sets (ADR 0030); otherwise
  NOT_ELIGIBLE with the reason and no numbers. Not betting advice.
- Every response says `license_status: RESEARCH_ONLY` (data licences, `docs/data_sources/licensing.md`): the
  API must not be published commercially before that is resolved.
