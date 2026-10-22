# ADR 0069 - League-model programme: urgent data top-ups (research overlay, Süper Lig 2026-27) and the UEFA stage experiment (Sprint 4b)

Date: 2026-10-22. Status: accepted. Builds on ADR 0063 to 0068. Nothing committed by Sprints 1 to 5 changes: no pre-registration, licence manifest, protocol or holdout file is touched.

## Context
The shadow layer (ADR 0068) fits its models on licence-clean history that ends on 2026-09-20 (the upstream `openfootball/football.json` has not been updated since then), the Süper Lig 2026-27 has no
football.json file (HTTP 404), the Europa and Conference League 2025-26 league phases are not published by any free public-domain source, and the UEFA stage (group / league phase / knockout) was not a feature.

## Decisions
1. **Research overlay** (`rollout/shadow.py`, `shadow.research_overlay: true`). The schedule call that the shadow run already makes to football-data.org also returns finished matches. Those that the licence-clean history lacks
   (deduplicated by league, clubs and date +- 1 day, and only once they are older than the 3-hour result lag) are added to the history used to fit the SHADOW models. It is research use of a source whose free tier is documented as
   non-commercial; the records already carry the `football_data_org` licence id, so the eligibility gate (which needs `PERMITTED`) can never serve such a model to users. Every record carries
   `coverage_flags.history_sources` (licence-clean rows / operational extra rows / overlay rows) and a refit with a changed history gets a new model version (immutable records, no overwrite). First effect (2026-10-22 20:30 UTC): +78 results
   for the four shadow leagues, history now ends on 2026-10-21.
2. **Operational public-domain cache** (`src/ingestion/openfootball_txt.py`). Reads the openfootball Football.TXT league files (both layouts present in the repositories) into the football.json shape and stores them under
   `artifacts/league_models/operational_cache/`, never in the frozen research snapshot (`artifacts/markets/_cache`), so Sprints 1 to 5 stay reproducible. The parser reproduces the football.json of England, Spain, Italy and Germany 2026-27
   exactly (380 / 380 / 380 / 306 matches, same scores); it fetched the Süper Lig 2026-27 (306 matches, 36 played, last played 2026-09-07; the source itself is stale for that league). The Süper Lig 2025-26 txt has the same 99 played
   matches as the cached json: the source is incomplete there, nothing to add.
3. **Aliases**: 44 football-data.org club aliases (exact strings already approved under `openfootball`) added through the approved CLI path with provenance `fuzzy_approved`, approver `claude-assistant 2026-10-22 ... owner to re-confirm`.
4. **Not solvable today, stated plainly**: Europa and Conference League 2025-26 (no free public-domain file; football-data.org lists EL and the Süper Lig as restricted for the free key: HTTP 403), results of the licence-clean source after
   2026-09-20 (nobody publishes them under a clear licence), lineups / injuries / shots (paid plans). Those cells stay on the global model.
5. **UEFA stage experiment** (`stage_features.py`, `stage_experiment.py`, report `docs/league_models/s4b_stage_experiment.json`). Stage text comes from the Sprint 1 identity rows (UEFA competitions only; a domestic round label
   is not a stage) and is mapped to seven columns (stage known, league phase / group, playoff, knockout, final, two-legged, matchday); a domestic match has `stage_known = 0` and NaN elsewhere. On the five development folds, a tuned LightGBM
   with the Sprint 4 features is compared with the same model plus the stage columns on the 1,750 UEFA evaluation rows, three targets, pooled and per competition (12 paired block-bootstrap comparisons, Sprint 3 rule).
   **Result: 0 of 12 SUPPORTED; every interval includes 0; the pooled deltas are -0.0007 (1X2), +0.0002 (BTTS) and +0.0002 (over/under) log loss.** The stage features are NOT adopted. The frozen feature contract `lm-s4-features-v1` stays as it is;
   the experiment is repeatable and the module can be reused if a prospective check is ever wanted.

## Consequences and limits
- The shadow models are fresher (to 2026-10-21) but partly trained on a CONDITIONAL source: a research setting only; the written answer from football-data.org (see `docs/data_sources/league_models_data_and_licence_actions.md`) decides whether that can ever
  support a user-visible model.
- The overlay changes a shadow model version whenever new results arrive (several records of one fixture can exist for different versions; the scorecard uses the latest per fixture, stage and family).
- Clubs missing from the alias table (several newly promoted Turkish clubs) are skipped and counted, never invented.
- The stage experiment compares tuned models on development folds only; it cannot be confirmed on the consumed holdout.

## Rollback
Set `shadow.research_overlay: false` (and delete `artifacts/league_models/operational_cache/`) to return to the licence-clean history; delete the aliases by their `2026-10-22` approver string; delete `stage_*.py` and
`openfootball_txt.py` with their tests. Nothing in production imports these modules.
