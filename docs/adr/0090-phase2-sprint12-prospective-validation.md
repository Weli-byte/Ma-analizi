# ADR 0090 — Phase 2, Sprint 12: prospective validation, settlement and monitoring (2026-10-29)

Plan: `Futbol_Tahmin_Optimizasyonu_Sprint_7_12_ULTRA_PRO_MAX.pdf`, sprint 12. Result: **CONDITIONAL**. Most of the sprint already existed and is reused, not rebuilt: immutable content-hashed forecasts written once (`src/markets/run.py`), the live record that scores only pre-kick-off forecasts (ADR 0048), the shadow layer with staged immutable records and a pre-registered gate (ADR 0076), the experimental-card gate (ADR 0073), alerts and Telegram (ADR 0077), the rollback drill (ADR 0080) and secret scanning (ADR 0078).

## Added
- `src/prospective/audit.py`: recomputes the content hash of every stored forecast, checks that the information cutoff equals the generation time and precedes kick-off, and lists every fixture with a pre-kick-off forecast that is not settled 36 h after kick-off (listed, never filled).
- `src/prospective/report.py`: prospective-only hit tables (all, last 100, last 30 matches; per league; trivial strategies beside them; primary log-loss metrics from the live record).
- **ESPN settlement of the domestic leagues** (`live_results.settle_from_espn`, run in every markets job): adds a finished result that no other source stored, never changes a stored one, never touches the fixture list; 21 days back. Domestic league matches have no extra time, so ESPN's final score is the 90-minute score. Source: ESPN public scoreboard, unofficial, research only, the same footing as the Europa/Conference League results of ADR 0070. These results also enter the open history the market model is fitted on (results only, availability = when first seen). Alias added: ESPN `Amed SFK` → `TUR_amed_sk` (owner to re-confirm).
- `docs/prospective/runbook.md` (objectives, alert handling, drills) and `docs/prospective/phase2_readiness.md` (sprints 7-12).

## Findings on the real store (2026-10-29)
- 7,658 stored forecasts: **0 hash mismatches**, 0 cutoff/generation-time differences, all published before kick-off.
- 226 fixtures with a forecast are past the 36 h grace: 197 settled, **29 unsettled** (18 Süper Lig, 7 La Liga, 2 Ligue 1, 1 UCL, 1 UEL). The Süper Lig results came from the odds provider's scores, whose free quota ran out in mid-October; ESPN has them (a local check found 24 finished Süper Lig, 30 La Liga and 27 Ligue 1 results in the last 21 days). Every published rate before this change covered only the settled matches; the report says so.
- Live record (202 settled matches, 2026-10-09 to 2026-10-28): 1X2 130/202 (64.4 %), over/under 2.5 127/202 (62.9 %), BTTS 118/202 (58.4 %), exact score 18/202 (8.9 %); always-home 50.5 %, always-over 64.9 %, always-BTTS 60.4 %.

## Not done, honestly
- No database constraint: the store is files; immutability is the refusal to overwrite plus the content hash, verified by the audit.
- No canary traffic (no approved league model; kill switch ON, canary fraction 0). No per-stage (t-24h/t-90m/t-30m) scoring of the published product forecasts: they are not staged (the shadow layer is). Rolling 300-match windows do not exist yet (202 matches). Settlement lag in hours is not measurable (no result publication time is stored).

## Rollback
Remove `settle_from_espn` from `live_results.collect` to stop the ESPN settlement (results already stored stay, they are correct 90-minute scores); the audit and report packages have no served consumer.

## Amendment 2026-10-29 — provisional fixture dates
After the ESPN settlement ran (18 Süper Lig results added), 11 fixtures were still unsettled; 7 of them were La Liga matches stored under 2026-10-24 22:00 (a provisional kick-off) that were played on 25–26 October (the line-up collector saw Barcelona–Real Madrid on 2026-10-25), so the exact-date join could never find their result. `track.result_index` now joins a forecast to the same ordered home/away pair played within 3 days when no result has the exact date (an ordered pair meets once in a league season; the exact date always wins). The pre-kick-off rule is unchanged and uses the REAL kick-off of the result. Applies to the live record and to the experimental-card gate.
