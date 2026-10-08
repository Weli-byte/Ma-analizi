# API (S18)

```
set FORECAST_API_KEYS=<your-key>      # comma separated; never commit
uvicorn src.api.app:create_app --factory --port 8000
curl -H "X-API-Key: <your-key>" http://127.0.0.1:8000/v1/fixtures
```
Routes: `/v1/health` (open), `/v1/fixtures`, `/v1/fixtures/{id}`, `/v1/fixtures/{id}/predictions`, `/v1/models`,
`/v1/benchmarks`, `/v1/teams/{id}/forecast`, `/v1/value-research`. Fixture id = `HOME__AWAY__YYYY-MM-DD`.
See ADR 0035.
