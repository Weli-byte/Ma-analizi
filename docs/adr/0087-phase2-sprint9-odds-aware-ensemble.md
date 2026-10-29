# ADR 0087 — Phase 2, Sprint 9: odds-aware ensemble research (2026-10-29)

Plan: `Futbol_Tahmin_Optimizasyonu_Sprint_7_12_ULTRA_PRO_MAX.pdf`, sprint 9. Result: **CONDITIONAL** — the pipeline (vig removal, as-of bookmaker consensus, market-only benchmark, paired chronological comparison) is built and run on real prospective rows; the sample is far below the pre-registered gates, so the production market rule is **unchanged** and no trained stacker was fitted. Nothing was bought.

## Licence and access matrix (checked 2026-10-29)
| Item | Status |
|---|---|
| The Odds API, free plan (500 credits a month, 1X2 only is collected) | derived values, UI display and ML use permitted, no raw redistribution (terms read 2026-10-09, `docs/data_sources/licensing.md`; the 31 Aug 2026 terms quoted in the plan were not re-read here); both keys are at 0 of 500 since mid-October, collection resumes when the quota renews (ADR 0083 spends one call per window) |
| Historical odds endpoint | not on the free plan, not used; no back-filled quote exists. The only odds are the prospective archive (`odds-data` branch, exact provider timestamps, 250 fixtures) |
| Over/under 2.5 and BTTS quotes | **not collected** (only 1X2) → no odds-aware ensemble for those markets (`BLOCKED`; totals cost further credits) |
| Other bookmaker feeds | none; no licence reviewed |

## What was built (`src/oddsens/`)
- `devig.py`: normalization (production baseline), power and Shin vig removal for any number of outcomes; unit tests on invariants (sums to 1, ordering kept, margin removed, equals the implied probabilities when there is no margin, power/Shin take more from long shots).
- `consensus.py`: the market AS OF a cutoff from the stored exact quotes: only quotes whose provider timestamp and our fetch time are at or before the cutoff, a complete H/D/A triple per bookmaker from one moment (10 min), at most 24 h old, at least 3 bookmakers; each bookmaker de-vigged on its own, equal-weight mean (no learned weights), dispersion and staleness recorded. Closing odds are never a feature.
- `dataset.py`: one row per settled match with the forecast the user could have seen (last published artifact before kick-off, the live-record rule of ADR 0048), the settled result, the model and the stored headline probabilities and the market rebuilt at that forecast's cutoff.
- `evaluate.py` / `report.py`: chronological split — the first half **selects** (vig method; weight of the convex blend `w*market + (1-w)*model`), the second half **reports**; log loss, Brier, RPS, accuracy, ECE; paired mean log-loss differences with a percentile bootstrap over matches (fixed seed). Python: `python -m src.oddsens.report --markets-root <markets-data> --odds-root <odds-data> --out docs/oddsens/sprint09_market_vs_model.md`.

## Result on the real rows (`docs/oddsens/sprint09_market_vs_model.md`)
- 202 settled matches: 17 without a stored quote, 54 with fewer than three usable bookmakers at the forecast cutoff (excluded, a selection the report states), **131 used** (66 select, 65 report). Median 8 bookmakers; Pinnacle in every row; the rebuilt market agrees with the one stored in the published forecasts to within 0.016.
- The production 1X2 headline already weights the de-vigged market at 1.0 when exact odds exist (ADR 0041). The selection half chose w = 1.0 again: **no evidence that mixing the model into the market helps** (model only vs market: +0.0028 on the report half, interval -0.039 to +0.046; +0.020 on all rows, inside its interval).
- The selection half chose the **power** method. On the report half the consensus with power beats the consensus with normalization by -0.0097 log loss (interval -0.0149 to -0.0041) and beats the stored production headline (one bookmaker, normalization) by -0.0066 (-0.0110 to -0.0022); the all-rows figure is -0.0049. This points to a better market rule (all bookmakers, power) but rests on 65 report rows, one of three methods picked on 66 rows, and a quota-limited sample.

## Decision gates (pre-registered here)
- **Replacing the production market rule** (equal-weight consensus of the usable bookmakers with the selected vig method) needs at least **300 report-half rows**, a paired interval wholly below 0 against the production headline, and no worse Brier. Today 65 → not met, production unchanged. When met, it is a methodological change and needs its own ADR and a shadow period.
- **Trained stacking** (OOF logistic) only with at least **600 rows** with three or more bookmakers (131 today), trained on chronological out-of-fold outputs only.

## Not done, honestly
- No over/under or BTTS ensemble, no EV/CLV analysis (n too small, closing quotes stay evaluation-only), no stacking, no change to any served number. Open owner decisions: a paid odds plan with totals/BTTS and history, or waiting for the quota to renew.

## Rollback
Research package only, no served consumer. Remove `src/oddsens/`, `tests/test_oddsens.py` and `tests/fixtures/real_oddsens_excerpt.json` to undo.
