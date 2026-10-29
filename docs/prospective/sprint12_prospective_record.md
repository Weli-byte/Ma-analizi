# Sprint 12 - prospective record of the published forecasts (ADR 0090)

As of 2026-10-29T15:35:36.844505+00:00 · live record generated 2026-10-29T15:33:57.771035+00:00 · 228 settled matches from 2026-10-09 to 2026-10-28 · `python -m src.prospective.report`.
Only forecasts published BEFORE kick-off are scored; backtests are in the league-programme and Phase 2 sprint reports, never mixed in here.

## Audit
- Immutability: 7978 stored forecasts, content hash recomputed for every one: **0 mismatches**.
- Timing: information cutoff differs from the generation time in 0; published before kick-off 7978, after kick-off 0 (never scored).
- Settlement: of 226 fixtures with a pre-kick-off forecast that kicked off more than 36 h before the report, 223 are settled and **3 are not** (listed below; they are missing from every rate in this report, so the rates cover the settled matches only).

## All settled matches

| Lig | Maç | 1X2 | 2.5 Alt/Üst | KG | Skor |
|---|---|---|---|---|---|
| SERIEA | 38 | 26/38 (%68.4) | 22/38 (%57.9) | 25/38 (%65.8) | 3/38 (%7.9) |
| UCL | 31 | 21/31 (%67.7) | 22/31 (%71.0) | 20/31 (%64.5) | 1/31 (%3.2) |
| LALIGA | 30 | 17/30 (%56.7) | 15/30 (%50.0) | 17/30 (%56.7) | 4/30 (%13.3) |
| EPL | 30 | 15/30 (%50.0) | 21/30 (%70.0) | 16/30 (%53.3) | 4/30 (%13.3) |
| LIGUE1 | 27 | 16/27 (%59.3) | 17/27 (%63.0) | 17/27 (%63.0) | 5/27 (%18.5) |
| BUNDESLIGA | 27 | 16/27 (%59.3) | 19/27 (%70.4) | 16/27 (%59.3) | 2/27 (%7.4) |
| SUPERLIG | 27 | 21/27 (%77.8) | 13/27 (%48.1) | 10/27 (%37.0) | 1/27 (%3.7) |
| UEL | 11 | 8/11 (%72.7) | 7/11 (%63.6) | 4/11 (%36.4) | 0/11 (%0.0) |
| UECL | 7 | 6/7 (%85.7) | 4/7 (%57.1) | 3/7 (%42.9) | 1/7 (%14.3) |
| **Toplam** | **228** | **146/228 (%64.0) [58-70]** | **140/228 (%61.4) [55-67]** | **128/228 (%56.1) [50-62]** | **21/228 (%9.2) [6-14]** |

| basit strateji | 1X2 | 2.5 Alt/Üst | KG |
|---|---|---|---|
| hep ev sahibi / hep üst / hep KG var | 113/228 (%49.6) [43-56] | 145/228 (%63.6) [57-70] | 133/228 (%58.3) [52-65] |

Primary metrics on all 228 matches (lower is better): 1X2 log loss 0.8939 (model only 0.9050, league base rate 1.0389), Brier 0.5186, RPS 0.1844; over/under 2.5 log loss 0.6614; BTTS log loss 0.6887.

## Last 100 settled matches

| Lig | Maç | 1X2 | 2.5 Alt/Üst | KG | Skor |
|---|---|---|---|---|---|
| SERIEA | 24 | 17/24 (%70.8) | 13/24 (%54.2) | 16/24 (%66.7) | 2/24 (%8.3) |
| UCL | 16 | 10/16 (%62.5) | 12/16 (%75.0) | 10/16 (%62.5) | 1/16 (%6.2) |
| LIGUE1 | 13 | 6/13 (%46.2) | 7/13 (%53.8) | 8/13 (%61.5) | 3/13 (%23.1) |
| SUPERLIG | 13 | 11/13 (%84.6) | 6/13 (%46.2) | 6/13 (%46.2) | 1/13 (%7.7) |
| LALIGA | 12 | 6/12 (%50.0) | 7/12 (%58.3) | 8/12 (%66.7) | 1/12 (%8.3) |
| BUNDESLIGA | 11 | 6/11 (%54.5) | 9/11 (%81.8) | 8/11 (%72.7) | 2/11 (%18.2) |
| EPL | 11 | 5/11 (%45.5) | 7/11 (%63.6) | 6/11 (%54.5) | 1/11 (%9.1) |
| **Toplam** | **100** | **61/100 (%61.0) [51-70]** | **61/100 (%61.0) [51-70]** | **62/100 (%62.0) [52-71]** | **11/100 (%11.0) [6-19]** |

## Last 30 settled matches

| Lig | Maç | 1X2 | 2.5 Alt/Üst | KG | Skor |
|---|---|---|---|---|---|
| SERIEA | 13 | 9/13 (%69.2) | 10/13 (%76.9) | 10/13 (%76.9) | 1/13 (%7.7) |
| LIGUE1 | 7 | 3/7 (%42.9) | 3/7 (%42.9) | 4/7 (%57.1) | 3/7 (%42.9) |
| SUPERLIG | 5 | 5/5 (%100.0) | 3/5 (%60.0) | 3/5 (%60.0) | 0/5 (%0.0) |
| LALIGA | 3 | 2/3 (%66.7) | 2/3 (%66.7) | 1/3 (%33.3) | 1/3 (%33.3) |
| BUNDESLIGA | 2 | 1/2 (%50.0) | 2/2 (%100.0) | 1/2 (%50.0) | 0/2 (%0.0) |
| **Toplam** | **30** | **20/30 (%66.7) [49-81]** | **20/30 (%66.7) [49-81]** | **19/30 (%63.3) [46-78]** | **5/30 (%16.7) [7-34]** |

Leagues with fewer than 20 matches are noise; one league's high rate is not a result.

## Live gates (pre-registered)

- experimental card (ADR 0073): BETA - 65 of 150 matches; no clear difference demonstrated yet (65 matches)
- shadow families (ADR 0076): H_dc_hier: SHADOW, P_dc_sep: SHADOW

## Unsettled fixtures (forecast published, no result stored)

| fixture | league | kick-off | age (days) |
|---|---|---|---|
| ESP_rayo_vallecano__ESP_athletic_bilbao__2026-10-10 | LALIGA | 2026-10-10T12:00 | 19.1 |
| SVK_sk_slovan_bratislava__GER_vfb_stuttgart__2026-10-14 | UCL | 2026-10-14T19:00 | 14.9 |
| ENG_afc_bournemouth__AUT_sk_sturm_graz__2026-10-15 | UEL | 2026-10-15T19:00 | 13.9 |

