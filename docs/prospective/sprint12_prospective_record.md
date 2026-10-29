# Sprint 12 - prospective record of the published forecasts (ADR 0090)

As of 2026-10-29T15:09:27.845799+00:00 · live record generated 2026-10-29T15:07:24.816881+00:00 · 220 settled matches from 2026-10-09 to 2026-10-28 · `python -m src.prospective.report`.
Only forecasts published BEFORE kick-off are scored; backtests are in the league-programme and Phase 2 sprint reports, never mixed in here.

## Audit
- Immutability: 7818 stored forecasts, content hash recomputed for every one: **0 mismatches**.
- Timing: information cutoff differs from the generation time in 0; published before kick-off 7818, after kick-off 0 (never scored).
- Settlement: of 226 fixtures with a pre-kick-off forecast that kicked off more than 36 h before the report, 215 are settled and **11 are not** (listed below; they are missing from every rate in this report, so the rates cover the settled matches only).

## All settled matches

| Lig | Maç | 1X2 | 2.5 Alt/Üst | KG | Skor |
|---|---|---|---|---|---|
| SERIEA | 38 | 26/38 (%68.4) | 22/38 (%57.9) | 25/38 (%65.8) | 3/38 (%7.9) |
| UCL | 31 | 21/31 (%67.7) | 22/31 (%71.0) | 20/31 (%64.5) | 1/31 (%3.2) |
| EPL | 30 | 15/30 (%50.0) | 21/30 (%70.0) | 16/30 (%53.3) | 4/30 (%13.3) |
| BUNDESLIGA | 27 | 16/27 (%59.3) | 19/27 (%70.4) | 16/27 (%59.3) | 2/27 (%7.4) |
| SUPERLIG | 27 | 21/27 (%77.8) | 13/27 (%48.1) | 10/27 (%37.0) | 1/27 (%3.7) |
| LIGUE1 | 25 | 15/25 (%60.0) | 17/25 (%68.0) | 16/25 (%64.0) | 4/25 (%16.0) |
| LALIGA | 24 | 14/24 (%58.3) | 11/24 (%45.8) | 14/24 (%58.3) | 3/24 (%12.5) |
| UEL | 11 | 8/11 (%72.7) | 7/11 (%63.6) | 4/11 (%36.4) | 0/11 (%0.0) |
| UECL | 7 | 6/7 (%85.7) | 4/7 (%57.1) | 3/7 (%42.9) | 1/7 (%14.3) |
| **Toplam** | **220** | **142/220 (%64.5) [58-71]** | **136/220 (%61.8) [55-68]** | **124/220 (%56.4) [50-63]** | **19/220 (%8.6) [6-13]** |

| basit strateji | 1X2 | 2.5 Alt/Üst | KG |
|---|---|---|---|
| hep ev sahibi / hep üst / hep KG var | 109/220 (%49.5) [43-56] | 142/220 (%64.5) [58-71] | 130/220 (%59.1) [52-65] |

Primary metrics on all 220 matches (lower is better): 1X2 log loss 0.8912 (model only 0.9016, league base rate 1.0372), Brier 0.5160, RPS 0.1849; over/under 2.5 log loss 0.6584; BTTS log loss 0.6898.

## Last 100 settled matches

| Lig | Maç | 1X2 | 2.5 Alt/Üst | KG | Skor |
|---|---|---|---|---|---|
| SERIEA | 24 | 17/24 (%70.8) | 13/24 (%54.2) | 16/24 (%66.7) | 2/24 (%8.3) |
| UCL | 16 | 10/16 (%62.5) | 12/16 (%75.0) | 10/16 (%62.5) | 1/16 (%6.2) |
| EPL | 14 | 7/14 (%50.0) | 9/14 (%64.3) | 8/14 (%57.1) | 3/14 (%21.4) |
| SUPERLIG | 13 | 11/13 (%84.6) | 6/13 (%46.2) | 6/13 (%46.2) | 1/13 (%7.7) |
| LIGUE1 | 12 | 6/12 (%50.0) | 8/12 (%66.7) | 7/12 (%58.3) | 2/12 (%16.7) |
| BUNDESLIGA | 11 | 6/11 (%54.5) | 9/11 (%81.8) | 8/11 (%72.7) | 2/11 (%18.2) |
| LALIGA | 10 | 7/10 (%70.0) | 4/10 (%40.0) | 5/10 (%50.0) | 0/10 (%0.0) |
| **Toplam** | **100** | **64/100 (%64.0) [54-73]** | **61/100 (%61.0) [51-70]** | **60/100 (%60.0) [50-69]** | **11/100 (%11.0) [6-19]** |

## Last 30 settled matches

| Lig | Maç | 1X2 | 2.5 Alt/Üst | KG | Skor |
|---|---|---|---|---|---|
| SERIEA | 13 | 9/13 (%69.2) | 10/13 (%76.9) | 10/13 (%76.9) | 1/13 (%7.7) |
| EPL | 5 | 2/5 (%40.0) | 3/5 (%60.0) | 2/5 (%40.0) | 0/5 (%0.0) |
| LIGUE1 | 5 | 2/5 (%40.0) | 3/5 (%60.0) | 3/5 (%60.0) | 2/5 (%40.0) |
| SUPERLIG | 5 | 5/5 (%100.0) | 3/5 (%60.0) | 3/5 (%60.0) | 0/5 (%0.0) |
| BUNDESLIGA | 2 | 1/2 (%50.0) | 2/2 (%100.0) | 1/2 (%50.0) | 0/2 (%0.0) |
| **Toplam** | **30** | **19/30 (%63.3) [46-78]** | **21/30 (%70.0) [52-83]** | **19/30 (%63.3) [46-78]** | **3/30 (%10.0) [3-26]** |

Leagues with fewer than 20 matches are noise; one league's high rate is not a result.

## Live gates (pre-registered)

- experimental card (ADR 0073): BETA - 57 of 150 matches; no clear difference demonstrated yet (57 matches)
- shadow families (ADR 0076): H_dc_hier: SHADOW, P_dc_sep: SHADOW

## Unsettled fixtures (forecast published, no result stored)

| fixture | league | kick-off | age (days) |
|---|---|---|---|
| ESP_rayo_vallecano__ESP_athletic_bilbao__2026-10-10 | LALIGA | 2026-10-10T12:00 | 19.1 |
| SVK_sk_slovan_bratislava__GER_vfb_stuttgart__2026-10-14 | UCL | 2026-10-14T19:00 | 14.8 |
| ENG_afc_bournemouth__AUT_sk_sturm_graz__2026-10-15 | UEL | 2026-10-15T19:00 | 13.8 |
| FRA_toulouse__FRA_troyes__2026-10-24 | LIGUE1 | 2026-10-24T15:15 | 5.0 |
| ESP_athletic_bilbao__ESP_getafe__2026-10-24 | LALIGA | 2026-10-24T22:00 | 4.7 |
| ESP_barcelona__ESP_real_madrid__2026-10-24 | LALIGA | 2026-10-24T22:00 | 4.7 |
| ESP_celta_vigo__ESP_real_betis__2026-10-24 | LALIGA | 2026-10-24T22:00 | 4.7 |
| ESP_deportivo_alaves__ESP_malaga__2026-10-24 | LALIGA | 2026-10-24T22:00 | 4.7 |
| ESP_real_sociedad__ESP_levante__2026-10-24 | LALIGA | 2026-10-24T22:00 | 4.7 |
| ESP_sevilla__ESP_osasuna__2026-10-24 | LALIGA | 2026-10-24T22:00 | 4.7 |
| FRA_paris_saint_germain__FRA_lyon__2026-10-25 | LIGUE1 | 2026-10-25T19:45 | 3.8 |

