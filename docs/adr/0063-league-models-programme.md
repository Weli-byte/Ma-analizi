# ADR 0063: League-based model programme (6 sprints) and Sprint 1

Date: 2026-10-20. Status: accepted; Sprint 1 built, owner decisions recorded, Sprints 2 and 3 built (ADR 0064, 0065), Sprints 4 to 6 not started.

## Decision
Adopt the plan in `docs/reference/Lig_Bazli_Modeller_6_Sprint_ULTRA_PRO_MAX_GUNCEL.pdf` (summary: `docs/league_models/plan.md`):
add a league-aware prediction layer for nine competitions (Premier League, Serie A, La Liga, Bundesliga, Ligue 1, Süper Lig,
Champions League, Europa League, Conference League) and three targets (1X2, over/under 2.5, BTTS), **without changing the global
production model**. A separate model per league is not mandatory; the plan's production default is "global + league adapter"
(candidates B and D), a local model (C) only with independent out-of-sample superiority, otherwise the global model stays.
Sprints run one at a time with gates (S1 data/licence/time, S2 frozen global reference, S3 league statistical models and UEFA bridge,
S4 ML adapters, S5 calibration/holdout/approval gate, S6 shadow, canary, rollback). Nothing reaches production without Sprint 5/6 evidence.

## Sprint 1 decisions (code: `src/league_models/`, config: `configs/league_models/`, outputs: `docs/league_models/`)
1. **The league universe uses only PERMITTED sources**, today the two public-domain openfootball repositories (domestic leagues and
   UEFA cups). A licence manifest (`source_licenses.yaml`) records every plan field per source with explicit `ALLOWED/DENIED/UNKNOWN`;
   an unknown right blocks production training (`training_gate`, fail closed, tested).
2. **football-data.co.uk is `BLOCKED` for league models** (plan red line, consistent with ADR 0044). The local research CSVs keep serving
   the existing global research benchmark (S4 to S11, RESEARCH_ONLY) and are not opened by the league programme.
3. **Consequence:** the licensed universe has scores and dates only (no shots, corners, cards, xG, lineups, odds), so league candidates are
   score-based. xG stays unavailable and is never faked.
4. **Canonical ids:** `canonical_league_id` (`ENG1` ... `UEFA_UECL`), season id, fixture id `{home}__{away}__{date}` (the repository's existing
   key). Unresolved clubs are excluded and reported, never guessed. No database migration (there is no fixtures database).
5. **As-of contract:** two separate clocks (event time, availability time), stage matrix on the S13 stages, every historical row
   `BACKTEST_LIMITED` because availability is inferred (kick-off + 3 h, ADR 0006). Closing odds and unannounced lineups are forbidden
   before kick-off.
6. **Training status of a league season** (starting engineering rules, not scientific limits): blocked without PERMITTED licence, without a
   file, with more than 5% of fixtures lacking a score (finished season: all fixtures; running season: up to its latest published score) or under 95%
   identity coverage; otherwise trainable (a running season is partial).
7. **Formats are declared, not verified** (`ASSISTANT_DECLARED_UNVERIFIED`): UEFA format versions and domestic league sizes.

## Sprint 1 result (details: `docs/league_models/sprint01_data_audit.md`)
72 competition-seasons audited (2019-20 to 2026-27): 53 trainable complete, 5 trainable partial (running season), 2 blocked incomplete
(Ligue 1 2019-20, Süper Lig 2025-26), 10 blocked no data (source 404 confirmed live), 2 not yet existing. 18,074 identity rows, 16,239
finished, 0 collisions, 0 unresolved clubs; temporal audit 64,956 (fixture, stage) checks, 0 violations. Trainable matches: five big
leagues about 2,100 to 2,700 each; Süper Lig 1,064 (three seasons); UEFA 576 to 997 each.

## Owner decisions (2026-10-20, replaces the open questions; all accepted as proposed)
1. **Features:** score-only universe (no shots, corners, cards, xG). No paid statistics source for the league programme.
2. **Holdout:** 2025-26 for every league that has it; Süper Lig 2024-25. Never loaded for training, tuning or selection.
3. **Source bytes:** the 95 cached source files (about 7 MB) are archived in the private data repository (Ma-analizi2) so exact
   reproduction is possible. Done 2026-10-20: branch `league-source-snapshot` of the private data repository (95 files, sha256 `MANIFEST.sha256`, bytes verified from a fresh clone; `scripts/archive_league_sources.py`).
4. **Süper Lig:** three usable seasons accepted; tier 500 to 1,500 (global + league effect, local model only with out-of-sample superiority).
5. **Kick-off times:** for date-only fixtures only the t-24h stage is treated as time-safe.
6. **Running-season lag:** keep the permitted-only rule (openfootball); the CONDITIONAL live-results store does not fill gaps.
7. **Formats:** UEFA format versions and Süper Lig sizes stay `ASSISTANT_DECLARED_UNVERIFIED` until a source is provided.

## Status of the six sprints
| Sprint | Status |
|---|---|
| 1 Data, licences, time safety | built and verified locally; CI: see the pull request |
| 2 Global reference | built and run on real data (ADR 0064) |
| 3 League statistical models + UEFA bridge | built and run on real data (ADR 0065) |
| 4 ML adapters | built and run on real data (ADR 0066) |
| 5 Calibration, holdout, approval gate | built; the frozen holdout was read once; no model approved (ADR 0067) |
| 6 Shadow, canary, rollback | not started |
