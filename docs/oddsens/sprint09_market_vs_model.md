# Sprint 9 - market versus model on prospective rows (ADR 0087)

As of 2026-10-29 (live record generated 2026-10-29T06:04Z, odds-data branch of the same day) · source: stored exact-timestamp 1X2 quotes of The Odds API (cloud collector) and the published forecasts of the live track record · `python -m src.oddsens.report`.

## Rows
- Settled matches in the live record: 202; no forecast before kick-off: 0; no stored odds: 17; fewer than 3 usable bookmakers at the forecast cutoff: 54; **used: 131**.
- A bookmaker counts only with a complete H/D/A triple from one moment (10 min), fetched and stamped by the provider at or before the forecast cutoff and at most 24 h old. The market is rebuilt as of that cutoff; closing odds are not used.
- Bookmakers per row: median 8, min 7, max 8; mean staleness 346 min; rows with a Pinnacle triple: 131; leagues: SERIEA 20, LALIGA 19, LIGUE1 18, EPL 18, BUNDESLIGA 17, UCL 14, SUPERLIG 9, UEL 9, UECL 7.
- Sanity: the rebuilt consensus differs from the market probability stored in the published forecast by at most 0.016 (mean of the per-row maxima 0.007) over 131 rows.

## Chronological split: 66 rows select, 65 rows report

- Vig removal chosen on the selection half: **power** (log loss normalization 0.8658, power 0.8609, shin 0.8623).
- Convex blend weight of the market chosen on the selection half: **w = 1.0** (0 = model only, 1 = market only).

### Report half (not used for any choice)

| candidate | log loss | Brier | RPS | accuracy % | ECE |
|---|---|---|---|---|---|
| base rate (selection half) | 1.0011 | 0.6031 | 0.2402 | 52.3 | 0.022 |
| model only | 0.8421 | 0.4849 | 0.1795 | 67.7 | 0.145 |
| market only (normalization) | 0.8393 | 0.4829 | 0.1781 | 72.3 | 0.196 |
| market only (power, selected) | 0.8297 | 0.4769 | 0.1757 | 72.3 | 0.184 |
| convex blend w=1.0 (selected) | 0.8297 | 0.4769 | 0.1757 | 72.3 | 0.184 |
| production headline (stored) | 0.8363 | 0.4808 | 0.1771 | 72.3 | 0.192 |

Paired log-loss difference against *market only (normalization)*, negative = better, 95 % bootstrap interval over matches:

| candidate | mean difference | 95 % interval | reading |
|---|---|---|---|
| base rate (selection half) | +0.1618 | +0.0624 to +0.2653 | worse, interval excludes 0 |
| model only | +0.0028 | -0.0385 to +0.0459 | inside the interval (no evidence either way) |
| market only (power, selected) | -0.0097 | -0.0149 to -0.0041 | better, interval excludes 0 |
| convex blend w=1.0 (selected) | -0.0097 | -0.0149 to -0.0041 | better, interval excludes 0 |
| production headline (stored) | -0.0030 | -0.0069 to +0.0011 | inside the interval (no evidence either way) |

### All rows (descriptive, selection and report mixed: no decision is taken from this table)

| candidate | log loss | Brier | RPS | accuracy % | ECE |
|---|---|---|---|---|---|
| base rate (selection half) | 1.0006 | 0.6002 | 0.2298 | 53.4 | 0.011 |
| model only | 0.8728 | 0.5071 | 0.1822 | 66.4 | 0.138 |
| market only (normalization) | 0.8527 | 0.4919 | 0.1753 | 69.5 | 0.159 |
| market only (power, selected) | 0.8454 | 0.4870 | 0.1733 | 69.5 | 0.150 |
| convex blend w=1.0 (selected) | 0.8454 | 0.4870 | 0.1733 | 69.5 | 0.150 |
| production headline (stored) | 0.8503 | 0.4897 | 0.1742 | 68.7 | 0.147 |

| candidate | mean difference vs market | 95 % interval | reading |
|---|---|---|---|
| base rate (selection half) | +0.1479 | +0.0772 to +0.2185 | worse, interval excludes 0 |
| model only | +0.0201 | -0.0112 to +0.0521 | inside the interval (no evidence either way) |
| market only (power, selected) | -0.0073 | -0.0119 to -0.0021 | better, interval excludes 0 |
| convex blend w=1.0 (selected) | -0.0073 | -0.0119 to -0.0021 | better, interval excludes 0 |
| production headline (stored) | -0.0024 | -0.0055 to +0.0011 | inside the interval (no evidence either way) |

## Reading
- The production 1X2 headline already uses the de-vigged market with weight 1.0 whenever exact odds exist (`market_blend_weight`, ADR 0041). What differs is the market itself: production takes the newest complete triple of ONE bookmaker (Pinnacle wins ties) and normalises it; the candidates here average all usable bookmakers and may use another vig removal.
- Selected market-only candidate against the production headline (report half): -0.0066, 95 % interval -0.0110 to -0.0022 (better, interval excludes 0); all rows, descriptive: -0.0049 (-0.0081 to -0.0012).
- Gate to replace the production market rule (pre-registered, ADR 0087): at least 300 report-half rows, an interval wholly below 0 against the production headline and no worse Brier. Report rows today: 65 → **NOT_MET**, production unchanged.
- A negative difference of the blend or the model against the market that stays inside its interval is no evidence; the production rule changes only after the pre-registered gate of ADR 0087 is met.
- Trained stacking (OOF logistic): **NOT_RUN** - 131 rows with >= 3 bookmakers < 600 (pre-registered gate).
- Over/under 2.5 and BTTS: no market quotes for them are collected (only 1X2), so no odds-aware ensemble is possible for those markets; collecting totals would cost further credits of a quota that is exhausted.
- Accuracy is secondary; log loss and Brier decide. ECE with this few rows is noise.

