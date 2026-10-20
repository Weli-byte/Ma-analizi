# Sprint 1: real data, licences and time safety: audit report

Status: **Sprint 1 built and verified on the real cached source files (2026-10-20).** No model was trained, nothing was added
to production, the global model is untouched. Plan: [`plan.md`](plan.md) (PDF
`docs/reference/Lig_Bazli_Modeller_6_Sprint_ULTRA_PRO_MAX_GUNCEL.pdf`), decisions: ADR 0063. Everything below comes from
`python -m src.league_models.audit --as-of 2026-10-20T00:00:00Z --write`; the machine-readable outputs sit next to this file
and the real command outputs in [`evidence/`](evidence/).

## Headline findings (read these first)
1. **The licence-clean universe supplies scores and dates only.** The two PERMITTED sources (openfootball domestic and cups,
   public domain) carry no shots, corners, cards, xG, lineups, injuries or odds. Every coverage figure for those is **0.0 by
   construction**, not "missing data". Plan candidates that need shot/xG features cannot be built from licensed data; the
   realistic Sprint 3 to 5 candidates are score-based: Elo, Poisson / Dixon-Coles, and GBMs on form/strength features.
2. **Volume is enough for five leagues, thin elsewhere.** Premier League, Serie A, La Liga, Bundesliga and Ligue 1 each have
   about 2,100 to 2,700 completed matches in 7 to 8 usable seasons (plan tier "over 1,500": local GBM/DC candidates may be
   compared with nested walk-forward). Süper Lig has 1,064 (three usable seasons; tier 500 to 1,500). The three UEFA tournaments
   have 576 to 997 matches each (tier 500 to 1,500; the plan says a cross-league bridge is the default there).
3. **Every row is `BACKTEST_LIMITED`.** The sources publish no result timestamp, so historical availability is inferred
   (kick-off + 3 h). As-of correctness can be made structurally leak-free but cannot be proven historically.
4. **Süper Lig has real source holes:** the source answers 404 for 2021-22, 2022-23, 2023-24 and 2026-27 (checked live, evidence
   `evidence/source_smoke_check.txt`), and 2025-26 has scores for only 99 of 306 fixtures. Those seasons are blocked, not patched.
5. **The running season lags by about a month:** the latest published score of every 2026-27 league file is 2026-09-20 although
   matches were played up to 2026-10-19. Treated as publication lag (partial season, trainable up to the published date), not as
   a data hole.
6. **Kick-off times are often unknown.** 1205 of 18074 fixtures publish only a date (placeholder
   time): 940 of them are 2026-27 fixtures (mostly not played yet), the rest sit in Süper Lig 2025-26 and Ligue 1 2019-20; `t-90m`/`t-30m` cutoffs for those are unreliable (flag `KICKOFF_TIME_UNRELIABLE`).
7. **Time audit and identity audit are clean:** 64,956 (fixture, stage) checks over 16,239 finished matches,
   0 violations; 18,074 identity rows, 0 collisions, 0 unresolved club names.

## Acceptance criteria of the Sprint 1 prompt
| Criterion | Result | Evidence |
|---|---|---|
| Nine competitions listed, every season's coverage with real counts | DONE | `league_coverage.csv/json` (72 competition-seasons) |
| Commercial ML and display rights in separate statuses; uncertain rights do not reach production training | DONE | `data_source_license_manifest.json`, test `test_training_gate_is_fail_closed` |
| Every training row has as_of, available_at and a hash | DONE as a contract plus an audit; the rows themselves are built in Sprint 2/3 | `asof.py` (`TrainingRowLineage`), `temporal_audit.json` |
| Leakage / duplicate / timezone tests run; CI not regressed | DONE locally (19 new tests); CI result: see the pull request | `evidence/pytest_s1.txt` |
| Trainable seasons separated from blocked seasons | DONE | `training_universe.json`, table below |
| Immutable data snapshot manifest and baseline-eval fixture list handed to Sprint 2 | DONE (60 source files, sha256 pinned) | `snapshot_manifest.json`, `canonical_identity_summary.json` |

## 1. Source and licence inventory (task 1)
Every right is explicit; `UNKNOWN` is never permission; football-data.co.uk is `BLOCKED` for league models (plan red line; the
local research CSVs are listed in the snapshot manifest as excluded and were **not opened**). The facts are copied from the
repository's own evidence files (this sprint did not research new terms). **Not legal advice**; every source keeps
`legal_review_required: true`.

| Source | Plan status | Display | ML | Derived forecasts | Retention | Redistribution | Production training | Evidence |
|---|---|---|---|---|---|---|---|---|
| `api_football` | RESEARCH_ONLY | DENIED | UNKNOWN | UNKNOWN | UNKNOWN | DENIED | REFUSED | `docs/data_sources/licensing.md` |
| `espn_scoreboard` | RESEARCH_ONLY | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | REFUSED | `docs/adr/0030-odds-value-paper.md` |
| `football_data_co_uk` | BLOCKED | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | REFUSED | `docs/data_sources/licensing.md` |
| `football_data_org` | CONDITIONAL | UNKNOWN | UNKNOWN | UNKNOWN | DENIED | UNKNOWN | REFUSED | `docs/data_sources/licensing.md` |
| `fpl_fantasy_premier_league` | RESEARCH_ONLY | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | REFUSED | `docs/adr/0028-availability-providers.md` |
| `highlightly` | CONDITIONAL | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | REFUSED | `docs/data_sources/provider_evaluation.md` |
| `openfootball_cups` | PERMITTED | ALLOWED | ALLOWED | ALLOWED | ALLOWED | ALLOWED | ALLOWED | `docs/adr/0055-european-cups.md` |
| `openfootball_domestic` | PERMITTED | ALLOWED | ALLOWED | ALLOWED | ALLOWED | ALLOWED | ALLOWED | `docs/adr/0038-openfootball.md` |
| `the_odds_api` | CONDITIONAL | ALLOWED | ALLOWED | ALLOWED | ALLOWED | DENIED | REFUSED | `docs/data_sources/licensing.md` |

Notes: openfootball's "public domain" dedication is by the repository maintainers (third-party rights in fixture lists were not
reviewed). football-data.org is conditional (commercial/ML rights UNKNOWN; art. 9.1 forbids referencing the data after
cancelling). The Odds API permits ML and derived display but forbids raw redistribution (ADR 0043) and has no historical archive
here (exact odds exist only for fixtures observed since 2026-10). Highlightly's free Basic plan is under terms that were not read.

## 2. Coverage matrix and training universe (tasks 2 and 6)
`T n` = trainable season with n completed matches, `P n` = running season, partial, `X` = blocked: source incomplete, `-` =
blocked: no data, `n/a` = the competition did not exist yet.

| Competition | 2019-20 | 2020-21 | 2021-22 | 2022-23 | 2023-24 | 2024-25 | 2025-26 | 2026-27 | Trainable matches |
|---|---|---|---|---|---|---|---|---|---|
| Premier League (`ENG1`) | T 380 | T 380 | T 380 | T 380 | T 380 | T 380 | T 380 | P 50 | **2710** |
| Serie A (`ITA1`) | T 380 | T 380 | T 380 | T 380 | T 380 | T 370 | T 380 | P 50 | **2700** |
| La Liga (`ESP1`) | T 380 | T 380 | T 380 | T 380 | T 380 | T 370 | T 380 | P 69 | **2719** |
| Bundesliga (`GER1`) | T 306 | T 306 | T 306 | T 306 | T 306 | T 306 | T 306 | P 36 | **2178** |
| Ligue 1 (`FRA1`) | X | T 380 | T 380 | T 380 | T 306 | T 306 | T 305 | P 45 | **2102** |
| Süper Lig (`TUR1`) | T 306 | T 420 | - | - | - | T 338 | X | - | **1064** |
| Champions League (`UEFA_UCL`) | T 119 | T 125 | T 125 | T 125 | T 125 | T 189 | T 189 | - | **997** |
| Europa League (`UEFA_UEL`) | - | T 205 | T 139 | T 141 | T 141 | T 189 | - | - | **815** |
| Conference League (`UEFA_UECL`) | n/a | n/a | T 141 | T 141 | T 141 | T 153 | - | - | **576** |

| Competition | Trainable seasons | Completed matches | Plan volume tier (page 2) |
|---|---|---|---|
| `ENG1` | 8 | 2710 | over 1500 |
| `ITA1` | 8 | 2700 | over 1500 |
| `ESP1` | 8 | 2719 | over 1500 |
| `GER1` | 8 | 2178 | over 1500 |
| `FRA1` | 7 | 2102 | over 1500 |
| `TUR1` | 3 | 1064 | 500 to 1500 |
| `UEFA_UCL` | 7 | 997 | 500 to 1500 |
| `UEFA_UEL` | 5 | 815 | 500 to 1500 |
| `UEFA_UECL` | 4 | 576 | 500 to 1500 |

Rules that produced the statuses (engineering rules in `configs/league_models/competitions.yaml`, starting values, not scientific
limits): a season is `BLOCKED_LICENSE` unless its source is PERMITTED for production training; `BLOCKED_NO_DATA` without a file;
`BLOCKED_INCOMPLETE_SOURCE` when more than 5% of the fixtures lack a score (a finished season is judged on all its fixtures, a
running season on the fixtures up to its latest published score) or identity coverage is under 95%; otherwise trainable.

What the blocked and flagged seasons mean:
- **Ligue 1 2019-20 (X):** 279 of 380 fixtures have scores; the season was curtailed in March 2020. The data cannot tell a
  cancelled match from a source gap, so it is blocked (conservative).
- **Süper Lig 2025-26 (X):** the season is over but the source has 99 of 306 scores (latest 2025-11-03): a source gap.
- **Serie A and La Liga 2024-25 (T 370):** 10 fixtures each have no score; 2.6% is under the 5% limit, so the season stays
  trainable with the flag `UNSCORED_TAIL_OF_FINISHED_SEASON_10`.
- **UEFA seasons:** the source repository publishes Champions League 2019-20 to 2025-26, Europa League 2020-21 to 2024-25,
  Conference League 2021-22 to 2024-25 (checked live 2026-10-20: the other files answer 404). Champions League 2019-20 has 119
  matches because the pandemic season used single-leg knockout games.
- Four rows carry `GLOBAL_BENCHMARK_FINAL_TEST_SEASON`: `configs/evaluation.yaml` locks 2024-25 and 2025-26 for the global
  research benchmark (Premier League and La Liga). The league programme's own holdout is a Sprint 2/5 decision (open question).
- Competition formats are **declared, not verified** (`ASSISTANT_DECLARED_UNVERIFIED`): UEFA format versions and the domestic
  league sizes in `competitions.yaml`. Observed fixture counts equal the declared sizes wherever sizes were declared (380 or 306);
  the unknown Süper Lig sizes the files show (306, 420, 342) are consistent with 18, 21 and 19 teams.

## 3. Canonical identities (task 3)
`canonical_fixture_id = {home_team_id}__{away_team_id}__{YYYY-MM-DD}` (the key this repository already uses), plus
`canonical_league_id` (`ENG1`, `ITA1`, `ESP1`, `GER1`, `FRA1`, `TUR1`, `UEFA_UCL`, `UEFA_UEL`, `UEFA_UECL`), `season_id`,
`provider_id`, a derived `provider_fixture_id` (these sources publish no match id), `kickoff_utc`, `kickoff_time_known`,
`status`, `final_score`, `stage` and the source file. Team ids come from the existing `TeamDirectory`; an unresolved or ambiguous
name is reported and the fixture excluded, never guessed or auto-registered; same-named clubs of different countries stay separate
(tested). Result on the real files: **18,074 rows** (ENG1 3040, ESP1 3040, FRA1 2744, GER1 2448, ITA1 3040, TUR1 1374, UEFA_UCL 997, UEFA_UECL 576, UEFA_UEL 815), **0 collisions** (no duplicate id, no id in two competitions, no
self match, no club with two fixtures on one day, no duplicate provider id), **0 unresolved
club mentions**. The mapping sha256 is `b0c1d15426dbfe0532723c76ff0e7a49273f04152773e44427bb43fea55b7660`; the rows are written to
`artifacts/league_models/identity_mapping.jsonl` (gitignored, rebuilt deterministically) by an idempotent upsert with a dry-run: a
second run added 0, changed 0, removed 0. **Migration: not applicable.** There is no fixtures database (the only database is the
Turso user store), so there is nothing to migrate; this is an additive mapping layer.

## 4. As-of contract and null policy (tasks 4 and 5)
See [`asof_contract.md`](asof_contract.md): two clocks checked separately, the stage matrix for t-24h/t-90m/t-30m/kickoff,
`BACKTEST_LIMITED`, training-row lineage. Null policy: a field the source cannot supply is 0.0 coverage with its reason in the
manifest (`supplies`); a field it could supply but was not measured is `None` (UNKNOWN); xG is never produced or renamed from
shots; "lineups unavailable" and "lineups confirmed false" are separate states; bookmaker odds keep `observed_at` and market apart
and closing odds are forbidden before kick-off. Sprint 1 defines these rules and enforces them in the contract and tests; the
feature builders that use them are Sprint 2 to 4 work.

## 5. Data quality (task 6)
From `quality_report.json`: 18,074 rows, 16,239 finished; score outside 0 to 15:
0; finished fixtures with a future kick-off: 0; fixtures
with an unknown kick-off time: 1205. Time zones: kick-off times are local clock times without a zone,
converted to UTC by the league zone (INFERRED); a daylight-saving gap or fold falls back to the date only.

Tests (`tests/test_league_models_s1.py`, 19): fail-closed licence gate (blocked, unknown, missing field, unknown source),
observation validation, both time checks, stage matrix, history excludes future results and the fixture itself, impossible
availability flagged, lineage refuses a leak, identity resolution/exclusion/no merge, collision audit and idempotent upsert,
season decisions (complete, running with lag, hole, curtailed tail, missing file, not yet existing, blocked licence), blocked CSVs
never opened, plus real-data invariants and consistency of the committed outputs. Tests labelled `TEST_ONLY` use minimal synthetic
data for logic and are **not** verification of any real data.

## 6. What was verified, and what was not
Verified on real data: the cached source files (95 files) were read and audited; a live read-only check (no key, TLS verified)
confirmed the 404s and that three cached files still equal the source's current bytes (`evidence/source_smoke_check.txt`); a
second audit run reproduced all 8 output files byte for byte (`evidence/idempotence_check.txt`).

**Not verified:** the licence statuses beyond the repository's own evidence (no lawyer, no provider contacted); competition
formats; that openfootball scores are correct (spot checks exist from earlier sprints, no full cross-source reconciliation: the
plan's "conflict resolution between two APIs" cannot be done with one licensed source); observed result availability times (none
exist); CI (see the pull request).

## 7. Handover to Sprint 2
- `snapshot_manifest.json`: 60 source files with sha256 and size, `data_snapshot_sha256`
  `56982683f64489e6b885eb4add22596fadec2ec2f77c7039147837842cf8a61d`; identity mapping hash above; licence manifest hash `3f1eb66c28fd0873130ae66e67a6d98ec4c0e93a7b99b5cd800acc465c312167`.
- The source files are fetched from public GitHub repositories whose current-season files change: the manifest pins exact bytes,
  but the bytes themselves live only in the local cache. Archive them (for example in the private data repository) before
  Sprint 2 if exact reproduction is required.
- Baseline-eval fixtures: the `FINISHED` rows of the trainable seasons in the mapping; the split (train / inner validation /
  calibration / final holdout) is Sprint 2's job.
- Blockers for Sprint 2: the open questions in ADR 0063 (holdout definition, football-data.co.uk role, source archive, Highlightly
  for the Süper Lig gap, format verification).
