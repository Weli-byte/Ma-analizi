# ADR 0089 — Phase 2, Sprint 11: calibration, uncertainty and model selection (2026-10-29)

Plan: `Futbol_Tahmin_Optimizasyonu_Sprint_7_12_ULTRA_PRO_MAX.pdf`, sprint 11. Result: **CONDITIONAL** — candidates, protocol, manifest and label-reliability check are built and run on real data; no calibrator passed the test season, so the champion (the uncalibrated market model) is kept and **nothing served changes**. Nothing was bought.

## Existing infrastructure (audited, not rewritten)
S9 temperature scaling (`src/evaluation/calibration.py`, ADR 0020) and the league programme's calibrators and approval gate (Sprint 5, ADR 0067: frozen holdout consumed, never re-read). Neither covered the product's market model on 1X2, over/under and BTTS together, nor the labels users see. This sprint adds `src/calibsel/` beside them.

## Data and split
- Out-of-fold predictions of the production market model (model only, no odds) for the six domestic forecast leagues, refitted every 30 days on matches finished before the block: **7,650 matches**, 43 fits, seasons 2020-21 to 2023-24, history `dv-of-56d45e54d516`, OOF digest `4a022b29…`. `oof.build_oof` refuses the final-test seasons (2024-25, 2025-26) and does not download them.
- Windows by season: **fit** 2020-21 + 2021-22 (4,072), **select** 2022-23 (1,826), **test** 2023-24 (1,752). The 2023-24 season already served to REPORT the market blend in ADR 0041, so it is untouched for calibration decisions, not pristine in general.
- Development note, stated for honesty: the test season was scored once while the league-level candidates were not yet in the candidate list; adding them did not change any choice, and the final run reproduced the same test numbers.

## Candidates and gate (fixed in code before the first run)
1X2: identity, temperature, vector scaling, Dirichlet (matrix on log-probabilities, L2 toward the identity), league-level temperature. Binary: identity, temperature, Platt, isotonic (needs 400 rows), league-level Platt. League-level candidates only for leagues with 800 fit + select rows (BUNDESLIGA, EPL, LALIGA, LIGUE1, SERIEA; the Süper Lig falls back to the global calibrator). Gate on the select window: at least 0.0010 lower log loss than the identity, no worse Brier, ECE at most 0.005 worse. The chosen calibrator is refitted on fit + select and scored once on test against the identity with a paired block bootstrap over ISO weeks, alpha = 0.05 / candidates tried. `SHADOW_CANDIDATE` only if the test interval is wholly below 0 with no worse Brier and ECE; otherwise `KEEP_CHAMPION`.

## Results (`docs/calibsel/sprint11_calibration_and_selection.md`, manifest `sprint11_evaluation_manifest.json`)
| target | chosen on 2022-23 | test 2023-24, chosen minus identity | status |
|---|---|---|---|
| 1X2 | temperature (T 1.15) | +0.0017 (−0.0015 to +0.0049) | KEEP_CHAMPION |
| over/under 2.5 | Platt | +0.0027 (−0.0009 to +0.0060) | KEEP_CHAMPION |
| BTTS | Platt | −0.0020 (−0.0058 to +0.0016) | KEEP_CHAMPION |

Every target passed the gate on 2022-23 and none was confirmed on 2023-24: the gain picked on one season did not carry over (winner's curse / season-to-season drift). Diagnostics of the champion on the test season: 1X2 home-win slope 1.01 (well calibrated; the 1X2 confidence tiers track their stated probabilities, e.g. 77.1 % stated vs 77.5 % hit in the top tier); over/under intercept +0.12 and BTTS intercept +0.17 at slope ≈ 1, i.e. the goal markets are shifted low — the same direction the live record showed in Sprint 10.

## User-facing labels (prospective, live record, 202 matches)
`strong lean` (p ≥ 0.65): 303 tips, stated 76.6 %, hit 78.5 % (73.6–82.8) — consistent; `lean`: 241 tips, stated 59.3 %, hit 61.8 % — consistent; `toss-up`: 264 tips, stated 48.7 %, hit 56.4 % (50.4–62.3) — **understates** (mostly the 1X2 pick: stated 44.1 %, hit 55.2 %). Small cells (for example `strong lean` BTTS, 17 tips, 47.1 % hit) are noise. The labels may stay; no label is upgraded to "high confidence" on this short, early-season record.

## Not done, honestly
- No calibrator served, no change to the market blend, no abstention policy in the product (the tiers are a report, not a feature), no per-season or cluster-by-competition bootstrap beyond weekly blocks. The live-record intervals cover a few weeks only.

## Rollback
Research package without a served consumer. Remove `src/calibsel/`, `tests/test_calibsel.py`, `tests/fixtures/real_calibsel_oof_excerpt.jsonl` and `docs/calibsel/` to undo.
