# Commercial data source migration plan (S12 preparation)

**Status:** planning document, written ahead of S12 so that sprint did not start from zero. S12
has since built the ingestion adapter infrastructure (`src/ingestion/`, ADR 0023) AND connected
one real, free-tier source (`football-data.org`, no cost — the project owner has no budget
currently) for research/testing. See `docs/data_sources/licensing.md` for why the current
sources (football-data.co.uk + Internet Archive fallback, AND football-data.org's free tier) are
all still `RESEARCH_ONLY` — none has had explicit commercial/redistribution terms confirmed yet
— and must not back any startup/production architecture (S19) until one does.

## Why now, not later

- `CLAUDE.md`'s Non-negotiable rules require odds `timestamp_quality=exact` for any edge/EV/CLV
  work (S15) and forbid faking or imputing xG (currently NOT available, experimental, never
  produced). A commercial source is the only path to both of those for anything beyond research.
- Migrating data sources mid-project is a leakage-control risk (different column semantics,
  different `result_available_at_utc` inference, different team-name spellings) — planning the
  contract now, before S12 writes ingestion code, reduces the chance of a rushed, under-tested
  switch.

## Candidate evaluation criteria (fill in during S12, not now)

For each candidate (API-Football, Sportmonks, football-data.org paid tiers, Opta/StatsBomb, or
others found during S12 review):

1. **License terms** — explicit commercial use, redistribution, and AI/model-training rights in
   writing (not inferred). Record verbatim terms + URL/contract reference in
   `docs/data_sources/licensing.md`, same table format as the current entries.
2. **Timestamp quality** — does the odds feed carry a genuine capture timestamp per quote
   (`timestamp_quality=exact`), or only a batch/snapshot timestamp? Anything less than exact
   cannot drive edge/EV/CLV per the non-negotiable rule; it may still be usable as
   `REFERENCE_MARKET_BASELINE` (closing odds) if that alone is truthfully labelled.
3. **Result availability semantics** — does the provider expose an actual `result_available_at`
   (or equivalent "when this became knowable") field, or does it need to keep being INFERRED as
   today? A provider with a real timestamp removes an entire class of leakage-control complexity
   from `src/features/availability.py`.
4. **xG availability and definition** — if xG is offered, get the exact model/provider
   methodology documented before treating it as a feature; `CLAUDE.md` currently forbids
   producing or imputing xG at all, so any future use is a distinct, ADR-gated decision, not an
   automatic consequence of the source having it.
5. **Team/competition ID stability** — does the provider use stable IDs, or names that would
   route through `src/data/team_resolution.py`'s existing manual-review safety net? Either is
   workable; note which, since it changes onboarding effort.
6. **Rate limits / cost model** — request budget vs. this project's ingestion cadence (live
   forecasting is S14 scope; research-phase polling is much lower volume).
7. **Historical backfill depth** — how many seasons of history the commercial feed provides,
   compared to what `football-data.co.uk` already covers; a gap here affects walk-forward window
   sizing (`src/evaluation/walk_forward.py`).

## Migration mechanics (once a source is chosen)

1. Write the licensing table entry in `docs/data_sources/licensing.md` FIRST (classification =
   the new source, not `RESEARCH_ONLY`, once terms are confirmed in writing) — do not write
   ingestion code against an unreviewed source.
2. New source gets its own `src/data/download.py` adapter and its own `data_version`
   (`dv-<hash>`) lineage; it does NOT silently replace the existing research dataset's artifacts.
   Both can coexist — research-track results stay reproducible against the original source.
3. `raw_validation.py`/`quality.py` checks are re-run against the new source's actual column
   semantics; do not assume they transfer unchanged (different providers encode missing values,
   scorelines, and match status differently).
4. Any schema field whose meaning changes (e.g. a real `result_available_at` replacing an
   inferred one) needs an ADR, per `CLAUDE.md`'s "methodological changes require an ADR" rule.
5. Golden artifacts are NOT regenerated against the new source as part of migration — golden
   fixtures stay pinned to the original research dataset for regression protection; a
   commercial-source golden set, if wanted, is a new, separate fixture set.

## Non-goals of this document (as originally written; superseded where noted)

- ~~Does not select a provider~~ — superseded: S12 connected football-data.org's FREE tier for
  research/testing (no cost, owner has no budget currently). This is NOT the resolved commercial
  answer this plan still asks for — football-data.org's commercial/redistribution terms remain
  unverified (`docs/data_sources/licensing.md`), same unresolved status as football-data.co.uk.
- ~~Does not change any code or config now~~ — superseded: `src/ingestion/football_data_org.py` +
  `configs/ingestion.yaml` exist, disabled by default until the owner adds a real API key.
- Does not resolve the xG decision — xG stays NOT_PRODUCED until a dedicated ADR says otherwise.
