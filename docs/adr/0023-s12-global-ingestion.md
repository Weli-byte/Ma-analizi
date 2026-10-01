# ADR 0023: S12 global fixture ingestion — adapter interface, no vendor wired yet

## Status
Accepted — 2026-10-01.

## Context
S12 asks for a provider adapter (leagues/seasons/fixtures, plus an interface left for
lineups/injuries/events/statistics/odds), idempotent fixture upsert, team identity, rate-limit/
backoff, a raw-response audit cache, a coverage matrix, and a freshness monitor — against
"10-20 high-data-quality leagues," with provider errors distinguished from legitimately-empty
responses, and "mock API integration tests" as the explicit test strategy. `docs/data_sources/
commercial_migration_plan.md` (S0-S7 hardening) already established that NO commercial vendor
has been selected — that remains the project owner's open decision. This sprint builds the
INFRASTRUCTURE the chosen vendor's adapter will plug into, tested against a mock provider,
exactly as S8 built LLM provider adapters before any real API key existed.

## Decision: a new `src/ingestion/` package, separate from `src/data/`

`src/data/` is the football-data.co.uk CSV pipeline (RESEARCH_ONLY, `docs/data_sources/
licensing.md`). `src/ingestion/` is for a commercial, global, API-based fixture lifecycle — a
different data shape (push/pull API, not a static CSV), a different trust model (provider
errors, rate limits, partial/live updates), and a different licensing status once a vendor is
chosen. Keeping them as separate packages means S12's work never risks touching the existing,
already-hardened CSV pipeline, and the eventual migration (ADR per `commercial_migration_plan.md`)
is additive, not a rewrite.

## Decision: `FixtureProvider` is a `Protocol`, required methods only for fixtures/leagues/seasons

`list_leagues`/`list_seasons`/`list_fixtures` are required. `list_lineups`/`list_injuries`/
`list_events`/`list_statistics`/`list_odds` are declared with a DEFAULT BODY that raises
`NotImplementedError` — "interface birak" (leave an interface) is read literally: a future S13/
S14/S15 adapter implementation overrides the one(s) it supports; an adapter that doesn't
override one still fails LOUDLY if called, never silently returns `[]`. An empty list from a
supported endpoint means "the provider legitimately has nothing for this query" (e.g., a league
with no fixtures left in a season) — conflating that with "unsupported" would be exactly the
kind of silent-fallback this project's rules forbid elsewhere (missing values, availability
reporting).

## Decision: provider error vs. empty response

`ProviderError` (and its `RateLimitedError` subclass) is the ONLY path for "something went
wrong." A `list_fixtures` call that returns `[]` is a successful call with a legitimately empty
result — `sync.py` records it as a coverage SUCCESS, not a failure. Only an actual raised
exception is a coverage ERROR. This is the sprint's own "provider error ile empty response'u
ayir" instruction, enforced structurally (two different code paths), not by convention.

## Decision: team identity reuses `src.data.teams.TeamDirectory`, no parallel resolver

`upsert.build_fixture` calls the EXISTING `TeamDirectory.resolve()` (S0-S3, ADR 0010) with the
provider's own name as the alias `source`. An unresolved team name is never auto-registered
(ADR 0010's safety-first default, re-verified here by
`test_build_fixture_never_auto_registers_an_unresolved_team`) — it comes back as `pending`, for
the EXISTING `python -m src.data.team_resolution review` workflow to handle, not a new
ingestion-specific review queue.

## Decision: result availability for a provider-sourced FINISHED fixture is "observed," not
"inferred"

The CSV pipeline's `result_available_at_source="inferred"` exists because that source has no
publication timestamp at all (ADR 0006/0008). A commercial API is queried in close-to-real-time
— when `upsert.build_fixture` sees `status=FINISHED` with a score, the ingestion time itself
(`ingested_at`, defaulting to `datetime.now(UTC)`) IS the genuine observation time, so
`result_available_at_source="observed"`. Never backdated to kickoff or invented.

## Decision: coverage matrix never lets an error erase a prior success

`CoverageMatrix.record_error` keeps the cell's existing `last_success_utc` and adds
`last_error` alongside it. A transient failure (one bad poll) must not make a freshness monitor
think an endpoint has NEVER worked — `stale_cells()` correctly distinguishes "never succeeded"
(empty `last_success_utc`, immediately stale) from "succeeded once, now erroring" (stale only
once `max_age_hours` has actually elapsed since that real success).

## Decision: cache IS the raw-response audit storage, not a separate mechanism

`ResponseCache` writes one content-hashed JSON file per `(provider, league, season, endpoint)`
key, with a `fetched_at_unix` timestamp, REGARDLESS of `ttl_seconds` — a "stale" cache entry (for
re-fetch purposes) is still left on disk as the audit record of what was fetched and when. One
mechanism serves both the sprint's "cache" and "raw response audit storage" tasks, rather than
two parallel stores that could drift out of sync with each other.

## Decision: rate limiting is a token bucket; backoff is specifically for rate-limit errors

`RateLimiter.acquire()` blocks (via an injectable `sleep_fn`, never actually sleeping in tests)
once the bucket is exhausted. `with_backoff` retries ONLY on `RateLimitedError` with exponential
delay — a plain `ProviderError` (auth failure, malformed request, 500) is NOT assumed transient
and propagates on the first attempt (`test_with_backoff_does_not_retry_a_plain_provider_error`).
Retrying a non-transient failure forever would hide a real bug behind apparent "resilience."

## Alternatives considered
- **Wire a specific named commercial vendor now** (API-Football, Sportmonks, ...) — rejected:
  `commercial_migration_plan.md` explicitly leaves this to the project owner; picking one here
  would be a licensing/cost decision this agent has no authority to make, and the adapter
  interface makes doing so later a pure addition, not a refactor.
- **A new team-resolution mechanism specific to ingestion** — rejected: `TeamDirectory` already
  solves exactly this problem (multi-source aliasing, safety-first unresolved handling); a
  parallel resolver would duplicate it for no benefit.
- **10-20 leagues hardcoded as a config list now** — rejected: no vendor means no real league-id
  vocabulary to hardcode yet; `League`/`Season` discovery is generic over whatever
  `list_leagues()` returns, and the specific league selection becomes a `configs/` entry once a
  vendor and its league IDs are known.

## Amendment (2026-10-01): one free-tier vendor connected

At the project owner's explicit request ("ücretsiz bişeyler ayarla" — set up something free; no
budget currently), `src/ingestion/football_data_org.py` implements `FixtureProvider` against
football-data.org's free tier (API v4) — free, email-only registration, no payment method
required, 10 calls/minute, 12 competitions (confirmed via their pricing/docs pages, 2026-10-01).
This does NOT change the "no vendor selected" decision above as a COMMERCIAL answer — their
commercial/redistribution terms were not found on the pages checked and remain unverified
(`docs/data_sources/licensing.md`); classification stays `RESEARCH_ONLY`. It exists so S12's
infrastructure has at least one real adapter to validate against once the owner has a key,
without waiting on a paid-vendor decision this agent still has no authority to make.

New: `configs/ingestion.yaml` (`IngestionConfig`, same `enabled`/`api_key_env` pattern as
`configs/provider.yaml`'s S8 `ProviderConfig`) + `sync.resolve_ingestion_provider` (same shape
as `src.llm.runner.resolve_provider`). `football-data-org`'s `enabled: false` by default; the
owner still needs to register and set `FOOTBALL_DATA_ORG_API_KEY` before any real call happens.
