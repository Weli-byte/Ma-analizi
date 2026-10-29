# Sprint 12 - prospective record of the published forecasts (ADR 0090)

As of 2026-10-29T14:00:00+00:00 · live record generated 2026-10-29T06:04:56.375205+00:00 · 202 settled matches from 2026-10-09 to 2026-10-28 · `python -m src.prospective.report`.
Only forecasts published BEFORE kick-off are scored; backtests are in the league-programme and Phase 2 sprint reports, never mixed in here.

## Audit
- Immutability: 7658 stored forecasts, content hash recomputed for every one: **0 mismatches**.
- Timing: information cutoff differs from the generation time in 0; published before kick-off 7658, after kick-off 0 (never scored).
- Settlement: of 226 fixtures with a pre-kick-off forecast that kicked off more than 36 h before the report, 197 are settled and **29 are not** (listed below; they are missing from every rate in this report, so the rates cover the settled matches only).

## All settled matches

| Lig | Maç | 1X2 | 2.5 Alt/Üst | KG | Skor |
|---|---|---|---|---|---|
| SERIEA | 38 | 26/38 (%68.4) | 22/38 (%57.9) | 25/38 (%65.8) | 3/38 (%7.9) |
| UCL | 31 | 21/31 (%67.7) | 22/31 (%71.0) | 20/31 (%64.5) | 1/31 (%3.2) |
| EPL | 30 | 15/30 (%50.0) | 21/30 (%70.0) | 16/30 (%53.3) | 4/30 (%13.3) |
| BUNDESLIGA | 27 | 16/27 (%59.3) | 19/27 (%70.4) | 16/27 (%59.3) | 2/27 (%7.4) |
| LIGUE1 | 25 | 15/25 (%60.0) | 17/25 (%68.0) | 16/25 (%64.0) | 4/25 (%16.0) |
| LALIGA | 24 | 14/24 (%58.3) | 11/24 (%45.8) | 14/24 (%58.3) | 3/24 (%12.5) |
| UEL | 11 | 8/11 (%72.7) | 7/11 (%63.6) | 4/11 (%36.4) | 0/11 (%0.0) |
| SUPERLIG | 9 | 9/9 (%100.0) | 4/9 (%44.4) | 4/9 (%44.4) | 0/9 (%0.0) |
| UECL | 7 | 6/7 (%85.7) | 4/7 (%57.1) | 3/7 (%42.9) | 1/7 (%14.3) |
| **Toplam** | **202** | **130/202 (%64.4) [58-71]** | **127/202 (%62.9) [56-69]** | **118/202 (%58.4) [52-65]** | **18/202 (%8.9) [6-14]** |

| basit strateji | 1X2 | 2.5 Alt/Üst | KG |
|---|---|---|---|
| hep ev sahibi / hep üst / hep KG var | 102/202 (%50.5) [44-57] | 131/202 (%64.9) [58-71] | 122/202 (%60.4) [54-67] |

Primary metrics on all 202 matches (lower is better): 1X2 log loss 0.8934 (model only 0.8983, league base rate 1.0346), Brier 0.5174, RPS 0.1853; over/under 2.5 log loss 0.6533; BTTS log loss 0.6844.

## Last 100 settled matches

| Lig | Maç | 1X2 | 2.5 Alt/Üst | KG | Skor |
|---|---|---|---|---|---|
| SERIEA | 27 | 19/27 (%70.4) | 15/27 (%55.6) | 19/27 (%70.4) | 2/27 (%7.4) |
| UCL | 16 | 10/16 (%62.5) | 12/16 (%75.0) | 10/16 (%62.5) | 1/16 (%6.2) |
| EPL | 15 | 8/15 (%53.3) | 10/15 (%66.7) | 8/15 (%53.3) | 3/15 (%20.0) |
| BUNDESLIGA | 13 | 6/13 (%46.2) | 9/13 (%69.2) | 8/13 (%61.5) | 2/13 (%15.4) |
| LIGUE1 | 12 | 6/12 (%50.0) | 8/12 (%66.7) | 7/12 (%58.3) | 2/12 (%16.7) |
| LALIGA | 10 | 7/10 (%70.0) | 4/10 (%40.0) | 5/10 (%50.0) | 0/10 (%0.0) |
| SUPERLIG | 7 | 7/7 (%100.0) | 3/7 (%42.9) | 3/7 (%42.9) | 0/7 (%0.0) |
| **Toplam** | **100** | **63/100 (%63.0) [53-72]** | **61/100 (%61.0) [51-70]** | **60/100 (%60.0) [50-69]** | **10/100 (%10.0) [6-17]** |

## Last 30 settled matches

| Lig | Maç | 1X2 | 2.5 Alt/Üst | KG | Skor |
|---|---|---|---|---|---|
| SERIEA | 17 | 12/17 (%70.6) | 11/17 (%64.7) | 11/17 (%64.7) | 2/17 (%11.8) |
| EPL | 5 | 2/5 (%40.0) | 3/5 (%60.0) | 2/5 (%40.0) | 0/5 (%0.0) |
| LIGUE1 | 5 | 2/5 (%40.0) | 3/5 (%60.0) | 3/5 (%60.0) | 2/5 (%40.0) |
| BUNDESLIGA | 3 | 2/3 (%66.7) | 3/3 (%100.0) | 1/3 (%33.3) | 0/3 (%0.0) |
| **Toplam** | **30** | **18/30 (%60.0) [42-75]** | **20/30 (%66.7) [49-81]** | **17/30 (%56.7) [39-73]** | **4/30 (%13.3) [5-30]** |

Leagues with fewer than 20 matches are noise; one league's high rate is not a result.

## Live gates (pre-registered)

- experimental card (ADR 0073): BETA - 48 of 150 matches; no clear difference demonstrated yet (48 matches)
- shadow families (ADR 0076): H_dc_hier: SHADOW, P_dc_sep: SHADOW

## Unsettled fixtures (forecast published, no result stored)

| fixture | league | kick-off | age (days) |
|---|---|---|---|
| TUR_galatasaray__TUR_kasmpasa__2026-10-09 | SUPERLIG | 2026-10-09T17:00 | 19.9 |
| TUR_genclerbirligi__TUR_amed_sk__2026-10-10 | SUPERLIG | 2026-10-10T10:30 | 19.1 |
| ESP_rayo_vallecano__ESP_athletic_bilbao__2026-10-10 | LALIGA | 2026-10-10T12:00 | 19.1 |
| TUR_alanyaspor__TUR_erzurumspor__2026-10-10 | SUPERLIG | 2026-10-10T13:00 | 19.0 |
| TUR_samsunspor__TUR_trabzonspor__2026-10-10 | SUPERLIG | 2026-10-10T13:00 | 19.0 |
| TUR_caykur_rizespor__TUR_fenerbahce__2026-10-10 | SUPERLIG | 2026-10-10T16:00 | 18.9 |
| TUR_konyaspor__TUR_istanbul_basaksehir__2026-10-11 | SUPERLIG | 2026-10-11T10:30 | 18.1 |
| TUR_gaziantep_fk__TUR_corum_fk__2026-10-11 | SUPERLIG | 2026-10-11T13:00 | 18.0 |
| TUR_besiktas__TUR_kocaelispor__2026-10-11 | SUPERLIG | 2026-10-11T16:00 | 17.9 |
| SVK_sk_slovan_bratislava__GER_vfb_stuttgart__2026-10-14 | UCL | 2026-10-14T19:00 | 14.8 |
| ENG_afc_bournemouth__AUT_sk_sturm_graz__2026-10-15 | UEL | 2026-10-15T19:00 | 13.8 |
| TUR_trabzonspor__TUR_besiktas__2026-10-19 | SUPERLIG | 2026-10-19T17:00 | 9.9 |
| TUR_alanyaspor__TUR_kocaelispor__2026-10-23 | SUPERLIG | 2026-10-23T17:00 | 5.9 |
| TUR_eyupspor__TUR_kasmpasa__2026-10-24 | SUPERLIG | 2026-10-24T10:30 | 5.1 |
| TUR_gaziantep_fk__TUR_erzurumspor__2026-10-24 | SUPERLIG | 2026-10-24T13:00 | 5.0 |
| FRA_toulouse__FRA_troyes__2026-10-24 | LIGUE1 | 2026-10-24T15:15 | 4.9 |
| TUR_samsunspor__TUR_amed_sk__2026-10-24 | SUPERLIG | 2026-10-24T16:00 | 4.9 |
| ESP_athletic_bilbao__ESP_getafe__2026-10-24 | LALIGA | 2026-10-24T22:00 | 4.7 |
| ESP_barcelona__ESP_real_madrid__2026-10-24 | LALIGA | 2026-10-24T22:00 | 4.7 |
| ESP_celta_vigo__ESP_real_betis__2026-10-24 | LALIGA | 2026-10-24T22:00 | 4.7 |
| ESP_deportivo_alaves__ESP_malaga__2026-10-24 | LALIGA | 2026-10-24T22:00 | 4.7 |
| ESP_real_sociedad__ESP_levante__2026-10-24 | LALIGA | 2026-10-24T22:00 | 4.7 |
| ESP_sevilla__ESP_osasuna__2026-10-24 | LALIGA | 2026-10-24T22:00 | 4.7 |
| TUR_konyaspor__TUR_genclerbirligi__2026-10-25 | SUPERLIG | 2026-10-25T13:00 | 4.0 |
| TUR_goztepe__TUR_corum_fk__2026-10-25 | SUPERLIG | 2026-10-25T16:00 | 3.9 |
| FRA_paris_saint_germain__FRA_lyon__2026-10-25 | LIGUE1 | 2026-10-25T19:45 | 3.8 |
| TUR_besiktas__TUR_istanbul_basaksehir__2026-10-26 | SUPERLIG | 2026-10-26T16:00 | 2.9 |
| TUR_caykur_rizespor__TUR_trabzonspor__2026-10-26 | SUPERLIG | 2026-10-26T16:00 | 2.9 |
| TUR_galatasaray__TUR_fenerbahce__2026-10-26 | SUPERLIG | 2026-10-26T18:30 | 2.8 |

