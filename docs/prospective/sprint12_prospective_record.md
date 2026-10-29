# Sprint 12 - prospective record of the published forecasts (ADR 0090)

As of 2026-10-29T20:46:12.307745+00:00 · live record generated 2026-10-29T20:43:59.375031+00:00 · 231 settled matches from 2026-10-09 to 2026-10-29 · `python -m src.prospective.report`.
Only forecasts published BEFORE kick-off are scored; backtests are in the league-programme and Phase 2 sprint reports, never mixed in here.

## Audit
- Immutability: 8058 stored forecasts, content hash recomputed for every one: **0 mismatches**.
- Timing: information cutoff differs from the generation time in 0; published before kick-off 8058, after kick-off 0 (never scored).
- Settlement: of 226 fixtures with a pre-kick-off forecast that kicked off more than 36 h before the report, 225 are settled and **1 are not** (listed below; they are missing from every rate in this report, so the rates cover the settled matches only).

## All settled matches

| Lig | Maç | 1X2 | 2.5 Alt/Üst | KG | Skor |
|---|---|---|---|---|---|
| SERIEA | 39 | 27/39 (%69.2) | 23/39 (%59.0) | 26/39 (%66.7) | 3/39 (%7.7) |
| UCL | 32 | 22/32 (%68.8) | 23/32 (%71.9) | 20/32 (%62.5) | 1/32 (%3.1) |
| LALIGA | 30 | 17/30 (%56.7) | 15/30 (%50.0) | 17/30 (%56.7) | 4/30 (%13.3) |
| EPL | 30 | 15/30 (%50.0) | 21/30 (%70.0) | 16/30 (%53.3) | 4/30 (%13.3) |
| LIGUE1 | 27 | 16/27 (%59.3) | 17/27 (%63.0) | 17/27 (%63.0) | 5/27 (%18.5) |
| BUNDESLIGA | 27 | 16/27 (%59.3) | 19/27 (%70.4) | 16/27 (%59.3) | 2/27 (%7.4) |
| SUPERLIG | 27 | 21/27 (%77.8) | 13/27 (%48.1) | 10/27 (%37.0) | 1/27 (%3.7) |
| UEL | 12 | 9/12 (%75.0) | 8/12 (%66.7) | 5/12 (%41.7) | 0/12 (%0.0) |
| UECL | 7 | 6/7 (%85.7) | 4/7 (%57.1) | 3/7 (%42.9) | 1/7 (%14.3) |
| **Toplam** | **231** | **149/231 (%64.5) [58-70]** | **143/231 (%61.9) [55-68]** | **130/231 (%56.3) [50-63]** | **21/231 (%9.1) [6-13]** |

| basit strateji | 1X2 | 2.5 Alt/Üst | KG |
|---|---|---|---|
| hep ev sahibi / hep üst / hep KG var | 115/231 (%49.8) [43-56] | 148/231 (%64.1) [58-70] | 134/231 (%58.0) [52-64] |

Primary metrics on all 231 matches (lower is better): 1X2 log loss 0.8879 (model only 0.9003, league base rate 1.0376), Brier 0.5143, RPS 0.1830; over/under 2.5 log loss 0.6599; BTTS log loss 0.6898.

## Last 100 settled matches

| Lig | Maç | 1X2 | 2.5 Alt/Üst | KG | Skor |
|---|---|---|---|---|---|
| SERIEA | 25 | 18/25 (%72.0) | 14/25 (%56.0) | 17/25 (%68.0) | 2/25 (%8.0) |
| UCL | 16 | 10/16 (%62.5) | 12/16 (%75.0) | 10/16 (%62.5) | 1/16 (%6.2) |
| SUPERLIG | 13 | 11/13 (%84.6) | 6/13 (%46.2) | 6/13 (%46.2) | 1/13 (%7.7) |
| LIGUE1 | 12 | 6/12 (%50.0) | 7/12 (%58.3) | 8/12 (%66.7) | 3/12 (%25.0) |
| LALIGA | 12 | 6/12 (%50.0) | 7/12 (%58.3) | 8/12 (%66.7) | 1/12 (%8.3) |
| BUNDESLIGA | 11 | 6/11 (%54.5) | 9/11 (%81.8) | 8/11 (%72.7) | 2/11 (%18.2) |
| EPL | 11 | 5/11 (%45.5) | 7/11 (%63.6) | 6/11 (%54.5) | 1/11 (%9.1) |
| **Toplam** | **100** | **62/100 (%62.0) [52-71]** | **62/100 (%62.0) [52-71]** | **63/100 (%63.0) [53-72]** | **11/100 (%11.0) [6-19]** |

## Last 30 settled matches

| Lig | Maç | 1X2 | 2.5 Alt/Üst | KG | Skor |
|---|---|---|---|---|---|
| SERIEA | 14 | 10/14 (%71.4) | 11/14 (%78.6) | 11/14 (%78.6) | 1/14 (%7.1) |
| LIGUE1 | 7 | 3/7 (%42.9) | 3/7 (%42.9) | 4/7 (%57.1) | 3/7 (%42.9) |
| SUPERLIG | 5 | 5/5 (%100.0) | 3/5 (%60.0) | 3/5 (%60.0) | 0/5 (%0.0) |
| LALIGA | 2 | 2/2 (%100.0) | 2/2 (%100.0) | 1/2 (%50.0) | 1/2 (%50.0) |
| BUNDESLIGA | 2 | 1/2 (%50.0) | 2/2 (%100.0) | 1/2 (%50.0) | 0/2 (%0.0) |
| **Toplam** | **30** | **21/30 (%70.0) [52-83]** | **21/30 (%70.0) [52-83]** | **20/30 (%66.7) [49-81]** | **5/30 (%16.7) [7-34]** |

Leagues with fewer than 20 matches are noise; one league's high rate is not a result.

## Forecast at each stage (published product forecast as it stood at the cutoff)

| aşama | maç | 1X2 | 2.5 Alt/Üst | KG | 1X2 log loss |
|---|---|---|---|---|---|
| t-24h | 192 | 125/192 (%65.1) | 120/192 (%62.5) | 110/192 (%57.3) | 0.8652 |
| t-90m | 223 | 145/223 (%65.0) | 139/223 (%62.3) | 126/223 (%56.5) | 0.8873 |
| t-30m | 223 | 145/223 (%65.0) | 139/223 (%62.3) | 126/223 (%56.5) | 0.8867 |

A stage counts only matches that had a published forecast by that cutoff, so the rows are not the same matches; 8 fixtures stored under a provisional date are left out. The live record above uses the LAST forecast before kick-off.

Settlement lag (kick-off to the first time a collector saw the result, an upper bound): median 5.7 h, 90 % within 93.1 h, max 478.0 h over 221 results.

## Live gates (pre-registered)

- experimental card (ADR 0073): BETA - 66 of 150 matches; no clear difference demonstrated yet (66 matches)
- shadow families (ADR 0076): H_dc_hier: SHADOW, P_dc_sep: SHADOW

## Unsettled fixtures (forecast published, no result stored)

| fixture | league | kick-off | age (days) | reason |
|---|---|---|---|---|
| ESP_rayo_vallecano__ESP_athletic_bilbao__2026-10-10 | LALIGA | 2026-10-10T12:00 | 19.4 | ABANDONED (espn) |

