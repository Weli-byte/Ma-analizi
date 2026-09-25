# FINAL S0–S3 REMEDIATION REPORT

Date: 2026-09-26 · Branch `remediation/s0-s3` · Audited code commit `8580908` · Sources: `docs/remediation/s0_s3_audit.md`
(23 findings F1–F23 + process items), ADR 0002–0012, reports 01–10 in this folder.

## Fixed issues
F1 git/CI · F2 data refresh · F4 content-derived data version · F5 silent fallback · F6 final-test lock + CI intervals ·
F8 rest-day cap · F9 NaN reasons · F11 dead config · F12 deep immutability/prediction identity · F13 timezones/DST ·
F15 team identity system · F16 20-check quality engine · F17 atomic pipeline · F18 ECE/bootstrap/tie rule · F19 golden,
regression, CLI tests, coverage · F20 dependency lock · F21 code hygiene · F22 ADRs · F23 TLS/download security.
Mitigated (documented limits): F3 provenance/checksums (archive origin, pinned only) · F7 odds semantics (no timestamps
exist) · F10 xG (experimental, no source) · F14 result availability (inferred).

## Unresolved issues (need owner decisions or later sprints)
Licensing of the data source (RESEARCH_ONLY) · upgrade of checksums to `verified_official` · review of 7 assistant-approved
team aliases · timestamped odds and observed result times (S12) · xG source · second-source adapter interface (S12) ·
Python 3.13/macOS untested · no static type checking.

## Changed files
~135 files: new `src/{provenance,runmode,cli_utils}.py`; `src/data/{download,raw_validation,manifest,checksums,versioning,
dataset,timezones,team_resolution}.py`; rewritten `src/data/{pipeline,clean,quality,teams,leagues}.py`; rewritten
`src/features/*` (+`availability`, `artifact`); rewritten `src/evaluation/*` (+`context`, `split`, `final`); schemas
(`frozen`, `lifecycle`, lifecycle fixtures, content-hash predictions, provenance experiments); configs (`leagues`,
`sources`, `features`, aliases); CI workflow; hashed lock; ADRs 0002–0012; docs; golden fixture; 20+ test modules.
Removed: `scripts/download_football_data.py` (TLS bypass), `requirements.txt`, stale S1–S3 reports.

## Verification results
| item | result |
|---|---|
| Tests (local, Windows, Py 3.14) | **319 passed / 0 failed** |
| Coverage | overall 97.7%; all critical groups ≥ 94.9% (gate 90%) |
| Lint | ruff clean |
| CI (GitHub Actions, run 36122384759, commit `8580908`) | **PASS** — Py 3.12, Py 3.14, lock-up-to-date |
| Data quality (real data, strict) | PASS — 21 checks, 0 errors, 0 warnings, 5350 fixtures |
| Leakage audit (real data, 300 samples) | PASS — 0 violations; 9 broken implementations + 1 cutoff-ignoring case caught in tests |
| Reproducibility (real data, artifacts deleted and rebuilt) | PASS — all six hashes identical |
| Final-test protection | PASS — guard tests for all ordinary modes; baseline run spy-verified; final path runs once |
| Data version | `dv-6f3af90c6bdb` |
| Feature version | `fv2` |
| Split | `split-8ed65cab17a3` (train 2280 / validation 1520 / final 1520 rows) |
| Local clean-venv install | NOT completed (killed by low-memory guard); CI fresh runners performed the hash-verified install |

## Remaining scientific limitations
See `10_remaining_risks.md`: inferred result availability, untimestamped odds, no xG, two leagues, two-season validation,
research-only data licence. Baselines show no established superiority between `recent_form_naive` and `historical_prior`.

## Definition of Done (docs/remediation prompt §38)
All boxes verified: git history · CI executed and green · explicit Python versions · reproducible dependencies · raw
checksums · correct provenance labelling · content-derived data version · lineage with data_version · stale artifacts fail ·
no silent feature fallback · final test protected · chronological split enforced · config consumed · deeply immutable
predictions · content-sensitive ids · enforced lifecycle · UTC timestamps · DST tests · result-availability semantics
documented · scalable alias system · strict quality checks · atomic pipeline · rest days corrected · explicit NaN policy ·
no false xG · explicit odds semantics · baselines interpreted with intervals · golden dataset · regression tests · coverage ·
CLI tests · production asserts removed · ADRs · TLS never disabled · data refreshed · partial season separated · licensing
metadata · experiment provenance · reproducibility test · leakage audit · this report.

## FINAL STATUS
FINAL STATUS: PASS (with the open owner decisions above)
TESTS: 319 passed / 0 failed
CI: PASS (commit 8580908; this report is a docs-only follow-up)
LEAKAGE: PASS
REPRODUCIBILITY: PASS
DATA VERSION: dv-6f3af90c6bdb
FEATURE VERSION: fv2
GIT SHA (audited code): 85809087b8a99bbd8cc4a786250ac7e42b025845
FINAL TEST PROTECTION: PASS
DATA QUALITY: PASS

READY_FOR_SPRINT_4 = TRUE
Non-blocking conditions to close during Sprint 4–S12: licence decision, verified checksums, alias review.
`main` has NOT been updated; merge the branch after reviewing this report.
