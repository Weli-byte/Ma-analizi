# Global fixture ingestion (S12, `src/ingestion/`, ADR 0023)

No commercial vendor is connected. This package is the adapter interface + upsert/coverage/
cache/rate-limit INFRASTRUCTURE, ready for a real `FixtureProvider` implementation once the
project owner picks one (`docs/data_sources/commercial_migration_plan.md`). Separate from
`src/data/`'s football-data.co.uk CSV pipeline (different trust model, different licensing
status).

## `FixtureProvider` (`provider.py`)

Required: `list_leagues`, `list_seasons`, `list_fixtures`. Left as an interface (raises
`NotImplementedError` by default, S13+ scope): `list_lineups`, `list_injuries`, `list_events`,
`list_statistics`, `list_odds`. `ProviderError` (call failed) is distinct from an empty result
(the provider legitimately has nothing) — `RateLimitedError` is the specific subclass
`rate_limit.with_backoff` retries on.

## Pipeline

```
sync_league_season(provider, league_id, season, directory, country, ...)
  -> fetch (rate-limited, cached, backed off)
  -> upsert_fixture per RawFixture (team identity via the existing TeamDirectory)
  -> CoverageMatrix.record_success / record_error
```

- `upsert.py` — `build_fixture` resolves team identity and validates against the existing
  `schemas.Fixture`; an unresolved team name is never auto-registered (ADR 0010) — it comes back
  in `pending`, for `python -m src.data.team_resolution review`. A `FINISHED` fixture's
  `result_available_at_source` is `"observed"` (ingestion time), never `"inferred"` (that label
  is specific to the CSV pipeline's lack of any publication timestamp).
- `coverage.py` — `CoverageMatrix`: one cell per `(league, season, endpoint)`, `last_success_utc`
  + `last_error`. An error never erases a prior success. `stale_cells(max_age_hours)` is the
  freshness monitor.
- `cache.py` — `ResponseCache`: content-hashed JSON per `(provider, league, season, endpoint)`
  key; doubles as the raw-response audit store (every fetch is kept, TTL only affects whether a
  FRESH fetch is skipped, not whether the file stays on disk).
- `rate_limit.py` — `RateLimiter` (token bucket) + `with_backoff` (exponential, `RateLimitedError`
  only).

## Tests

`tests/test_ingestion.py` — every test uses a `MockProvider` (subclasses `FixtureProvider` so the
default `NotImplementedError` bodies are inherited); no network. 37 tests covering the protocol,
rate limiting, cache/audit round-trip and TTL, upsert (new/unchanged/changed/idempotent/pending
team resolution/never-auto-registers), coverage (success/error/staleness/JSON round-trip), and
full `sync_league_season` orchestration including provider-error and cache-hit paths.
