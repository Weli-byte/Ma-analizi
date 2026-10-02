# ADR 0028 — Provider capabilities, real injury data, lineups stay UNKNOWN

Status: accepted · 2026-10-02

## Decision
- **Capability interfaces** (`src/ingestion/interfaces.py`): `FixtureProvider` (S12) plus
  `LineupProvider`, `InjuryProvider`, `EventProvider`, `OddsProvider`, `StatisticsProvider`,
  `XGProvider`. Every provider carries a `ProviderMeta`: per-capability `SUPPORTED` /
  `NOT_SUPPORTED` / `NOT_CONFIGURED`, coverage, timestamp semantics, rate limit, licence and
  `license_status`, provenance endpoint, `verified_on` (the date a REAL response confirmed it).
  `src/ingestion/capabilities.py::capability_report()` is derived from those metas.
- **Verified facts (2026-10-02, real calls):** football-data.org free tier returns fixtures only:
  no lineups (also none for finished matches), no injuries, `odds` not populated. The Fantasy
  Premier League public endpoint (`bootstrap-static`) returns, for every PL player, `status`
  (a/d/i/s/u), `news`, `news_added` and `chance_of_playing_next_round`.
- **Injuries (EPL only)** come from FPL (`src/ingestion/fpl.py`), `RESEARCH_ONLY` (unofficial,
  undocumented endpoint, terms unverified). Each record is a `PlayerAvailability`: player, team
  (resolved through `TeamDirectory`, source `fpl`), status, availability %, detail, source,
  `observed_at` (our fetch time), `effective_at` (= `news_added`), confidence (always `None`: FPL
  exposes a chance-of-playing %, not a confidence), provenance (endpoint, FPL id, raw status, raw
  response hash). Raw responses are archived under `artifacts/ingestion/fpl/`.
- **Cutoff rule:** only entries with `effective_at <= information_cutoff` are used
  (`snapshot/availability.py`); later-dated or undated entries are only counted
  (`excluded_post_cutoff`). `audit_snapshot` re-checks this before every LLM call. The response is
  the CURRENT state, so a status that changed after a past cutoff cannot be reconstructed; absence
  from the list means "no flag", never "will play".
- **Status vocabulary** in `availability.injuries`: `OBSERVED` / `UNKNOWN` / `FAILED`. Non-EPL
  fixtures: `UNKNOWN (no_provider)`. A failed fetch is `FAILED`, not silently UNKNOWN.
  **Lineups: always `UNKNOWN (no_provider)`** (no free source exists).
- **Use:** injuries enter the stage snapshot (and its content hash) and the LLM prompt's
  `permitted_current_information.injuries`. No statistical/ML model uses them: there is no
  timestamped history to train on, so adding a feature would be unvalidated (a feature needs a
  registry entry, history and its own ADR).
- Team aliases for `football-data-org` (36) and `fpl` (8) were approved on 2026-10-02 as
  `fuzzy_approved` (top suggestion checked against the canonical team); owner to re-confirm.

## Next free option for lineups (needs the owner)
API-Football's free plan (key from dashboard.api-football.com, `API_FOOTBALL_KEY`) lists lineups
and injuries; no adapter is written until a key exists, because capability claims are only made
after a real response.
