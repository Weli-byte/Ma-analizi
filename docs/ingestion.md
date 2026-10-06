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

## Real adapter: football-data.org (ADR 0023 amendment, free tier, 2026-10-01)

`src/ingestion/football_data_org.py::FootballDataOrgProvider` — a REAL `FixtureProvider`
implementation against football-data.org's free API (v4): 10 calls/minute, 12 competitions, no
payment method required (register at https://www.football-data.org/client/register). Chosen
because the project owner has no budget right now, not as a resolved commercial answer — its
terms remain unverified (`docs/data_sources/licensing.md`), classification stays
`RESEARCH_ONLY`.

**Connected 2026-10-01** with the project owner's own free API key (`.env`, gitignored, loaded
automatically by every `python -m ...` entrypoint via `cli_utils.load_dotenv`).
`configs/ingestion.yaml`'s `football-data-org` entry is `enabled: true`, `leagues: [PL, PD, BL1,
SA, FL1, CL, DED, PPL]` (8 of the free tier's 12 competitions — the major leagues + Champions
League). Verified end-to-end against the real API: `list_leagues` (13 competitions returned),
`list_seasons`, and `list_fixtures` (380 fixtures for one Premier League season) all confirmed
working. Resolve it the same way S8 resolves an LLM provider:

```python
from src.config import config_dir_for, load_config
from src.ingestion import resolve_ingestion_provider, sync_league_season

cfg = load_config("ingestion", config_dir_for(root))
provider, api_key, leagues = resolve_ingestion_provider(cfg, "football-data-org")
result, upserts = sync_league_season(provider, "2021", "2023-24", directory, "ENG")
```

(`"2021"` or `"PL"` both work as the league id — football-data.org's v4 API accepts either its
numeric id or its short code interchangeably.) No standalone CLI yet — `sync_league_season` is
called directly or wired into a script when a real sync is actually wanted; nothing in this
repo runs it automatically (no scheduled job, no test calls the real API).

## Tests

`tests/test_ingestion.py` — rate limiting, cache/audit round-trip and TTL, upsert (new/unchanged/
changed/idempotent/pending team resolution/never-auto-registers), coverage, and `sync_league_season`
orchestration. There is NO mock provider (ADR 0024/0032): orchestration runs the REAL football-data.org
adapter over a real loopback HTTP socket against REAL captured responses
(`src/ingestion/endpoints.py` allows redirecting an endpoint to loopback only); a provider failure is a
real connection refusal. `tests/test_football_data_org.py` — the real adapter parsing real captures
(leagues, seasons, fixtures, status mapping) and `resolve_ingestion_provider` wiring; the real-server
403/429 paths are `tests/integration/test_fdorg_errors_live.py`.
