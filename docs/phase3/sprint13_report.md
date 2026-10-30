# Phase 3, Sprint 13 — production reality audit and prediction lineage (ADR 0091)

Plan: `Futbol_Tahmin_Platformu_Faz3_Sprint_13_18_ULTRA_PRO_MAX.pdf` (30 Oct 2026). Release decision: **CONDITIONAL_GO** (see the end).

## 1. Discovery and gaps
- What runs (inventory, `docs/phase3/s13_inventory.md`): the public site is deployed by the `markets-cloud` workflow (newest successful run on 2026-10-30 at commit `a37395d4`); data lives on three branches of the private data repository (`markets-data`, `shadow-data`, `odds-data`; `odds-data` has not moved since 2026-10-19 because both free odds keys are below their reserve). Versions: market model `markets-1.0.0`, API `1.0.0`. The user store (Turso) has two tables and one additive column; there is no other migration.
- What the published forecasts already had: immutable files written once, a content hash, the information cutoff, data / feature / model / config versions, the odds source. **What they lacked**: the producing code commit, the stage, the calibration state and an explicit statement of the inputs that are not features. That is the gap closed here.
- The plan's 138-match reference: the "first 138 by date" is no longer the same set, because later settlements (ADR 0090) added matches on those dates (today the first 138 give 93/138 for 1X2, not 94).

## 2. Plan and 3. Changes
| Area | Change |
|---|---|
| `src/lineage/record.py` | lineage block (schema `lineage-v1`): code commit (GITHUB_SHA / git), pipeline, stage (early / t-24h / t-90m / t-30m / post_kickoff) and minutes to kick-off, history data version and size, odds source (bookmaker, observed time, quality — no prices), calibration, lineups / injuries status |
| `src/markets/run.py` | `write_artifact` adds the block BEFORE the content hash, so it is covered by the hash; existing path still refused (no overwrite) |
| `src/lineage/query.py`, `src/api/service.py`, `src/api/app.py` | `GET /v1/fixtures/{id}/lineage` (API key): every stored version, hash re-verified, `legacy_unknown` for versions written before the block; the intelligence response carries `lineage` |
| `src/web/pages.py`, `src/web/i18n.py` | "Tahmin künyesi" line in the match page's data tab (stage, commit, history size, odds time without the bookmaker name, no calibration); older forecasts say they predate the record |
| `src/lineage/smoke.py` | one real cheap request per provider, redacted (`docs/phase3/s13_provider_smoke.json`) |
| `src/lineage/inventory.py` | the inventory above |
| `src/lineage/baseline.py` | the 138-match set frozen from the 2026-10-27 record (`docs/phase3/s13_baseline_manifest.json`, source SHA-256 `ab7e572d…`) and verified against any later record |

## 4. Migration and configuration
Additive only: a new optional field in new forecast files. Old files are not touched and not back-filled (the plan's rule); readers treat the field as optional. Rollback: remove the `body["lineage"]` line in `write_artifact`; files already written stay valid (their hash covers whatever they contain).

## 5. Commands and results
- `python -m src.lineage.smoke --out docs/phase3/s13_provider_smoke.json` (2026-10-30): football-data.org 200 (1.3 s); The Odds API key 2: 200, 24 credits left, 476 used (below the 25-credit reserve, so collection stays paused); key 1 unreachable from this network (connection reset); API-Football: account suspended (the provider's message); ESPN 200; openfootball 200; Highlightly not called from this network (its host is unreachable here; the cloud collector is its smoke). The output was checked to contain no key value.
- `python -m src.lineage.baseline verify …`: **VERIFIED_BASELINE = true** — all 138 fixtures found in the record of 2026-10-29 with identical picks, results and hits: 1X2 94, over/under 84, BTTS 76, exact score 10, always-over 95.
- Tests: `tests/test_lineage.py` (8, offline; the forecast WITH a lineage block is written by the real writer on TEST_ONLY matches; the legacy ones are real artifacts), plus the existing API / markets / web / prospective suites. The real-provider smoke is separate (the command above), never counted as a unit test.

## 6. Data and sample sizes
8,058 stored forecasts (all legacy before this sprint, 0 hash mismatches, all published before kick-off, ADR 0090); 231 settled matches in the live record; 138 in the frozen baseline.

## 7. Licence, cost and quota
No paid call, no new dependency. The smoke uses calls that spend no credit (`/v4/sports` of The Odds API reports credits without spending them). Licences unchanged: football-data.org CONDITIONAL, ESPN unofficial / research only, The Odds API derived use allowed with no raw redistribution (the page and the lineage show no prices).

## 8. Open blockers
API-Football suspended (BLOCKED_EXTERNAL_ACCESS); Highlightly unreachable from the owner's network (cloud only); The Odds API quota below reserve; no deployment id without the Vercel token (the deploy job's record stands in). Lineage coverage of NEW forecasts is verified after the next cloud run (it needs this code deployed).

## 9. Audit queries (no personal data)
For the user store, counts only, never names or e-mails:
```sql
SELECT provider, plan, COUNT(*) AS users FROM users GROUP BY provider, plan;
SELECT COUNT(*) AS favourites, COUNT(DISTINCT uid) AS users_with_favourites FROM favorites;
```
For the forecast store (files): `python -m src.prospective.report` (hashes, timing, settlement) and `GET /v1/fixtures/{id}/lineage`.

## 10. Acceptance and decision
| Criterion | State |
|---|---|
| lineage fields filled for 100 % of NEW forecasts, old ones not back-filled | built and tested; production coverage confirmed after the next cloud run |
| the 138-match baseline reproducible or BLOCKED | reproducible: frozen set, VERIFIED_BASELINE true |
| duplicate / race, cutoff violation and secret-leak tests pass | yes (same path refused; post-kick-off artifacts never scored, ADR 0090; smoke redaction test) |
| real provider smoke where key and terms allow | yes for 4 providers, blocked ones named |

**CONDITIONAL_GO**: the lineage is safe to ship (additive, hash-covered, tested); it is conditional on the external blockers above and on the post-deploy coverage check.
