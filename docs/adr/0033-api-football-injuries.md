# ADR 0033 — API-Football free plan: current injuries for La Liga and the Premier League

Status: accepted · 2026-10-08

## Verified on a real account (2026-10-08, key in `API_FOOTBALL_KEY`, host reachable from the owner's network)
- Plan Free, 100 requests/day, 10/minute.
- Fixtures and lineups: only seasons 2022-2024 ("Free plans do not have access to this season, try from
  2022 to 2024"). So NO current-season lineups come from this plan.
- `/injuries?date=YYYY-MM-DD`: only dates inside a rolling window [yesterday, tomorrow], WITHOUT a season
  filter; one call returns every league of that date (208 entries on 2026-10-09), each tied to its fixture.
  Entries have `type` (Missing Fixture / Questionable) and `reason`, but no report time.
  League ids: EPL 39 (id 235 is the Russian Premier League), La Liga 140.

## Decision
- `src/ingestion/api_football.py` (`RESEARCH_ONLY`) is a CURRENT-injuries source for fixtures kicking off
  within about a day. It is the first injury source for La Liga (FPL covers only the EPL). Statuses:
  Questionable -> DOUBTFUL, suspension -> SUSPENDED, injury/illness -> INJURED, anything else (loan,
  national duty) -> UNAVAILABLE. A plan/error payload raises (never "no injuries"). One request per
  kickoff date per snapshot run; the key never reaches logs or exception text.
- **Cutoff rule generalised:** an injury entry is usable when `(effective_at or observed_at) <=
  information_cutoff`; the snapshot rows carry `as_of`, `effective_at` (nullable), `source`. Undated
  entries therefore mean "the state as of our fetch", which is exactly what they are.
- `merge_blocks` combines FPL and API-Football for the EPL (each player keeps its `source`); a source that
  failed is listed in `failed_sources`, never dropped. The merged block feeds the stage snapshot and the LLM
  prompt as before; no statistical model uses injuries.
- The first stage run that needs a date outside the window (a match more than a day away) gets `FAILED`
  from this source, by design: the t-24h stage of tomorrow's matches is inside it.

## Not possible on the free plan
Current lineups (ESPN stays the lineup source) and historical injuries (the injuries endpoint only serves
today +/- 1 day); lineups for 2022-2024 exist and could build a historical availability dataset slowly
(100 requests/day), a separate research decision.
