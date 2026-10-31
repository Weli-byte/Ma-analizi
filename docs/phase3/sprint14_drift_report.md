# Sprint 14 - data quality, input drift and performance drift (ADR 0092)

As of 2026-10-31T12:42:25.342989+00:00 · `python -m src.drift.report` · thresholds from validation history (`docs/phase3/s14_thresholds.json`, 14 window pairs, margin 1.25).

## 1. Data quality — ALARM

| check | value | limit | status |
|---|---|---|---|
| freshness live_record | 0.1 h | 8 h | OK |
| freshness forecast | 0.1 h | 8 h | OK |
| freshness lineups | 0.3 h | 96 h | OK |
| freshness odds | 281.8 h | 48 h | ALARM |
| freshness results:BUNDESLIGA | 6.7 h | 168 h | OK |
| freshness results:EPL | 137.6 h | 168 h | OK |
| freshness results:LALIGA | 6.7 h | 168 h | OK |
| freshness results:LIGUE1 | 6.7 h | 168 h | OK |
| freshness results:NED1 | 134.0 h | 168 h | OK |
| freshness results:POR1 | 6.7 h | 168 h | OK |
| freshness results:SERIEA | 30.7 h | 168 h | OK |
| freshness results:SUPERLIG | 0.2 h | 168 h | OK |
| freshness results:UCL | 229.2 h | 168 h | CUP_GAP |
| freshness results:UECL | 40.1 h | 168 h | OK |
| freshness results:UEL | 40.1 h | 168 h | OK |
| schema | forecasts with missing fields 0 of 323; results 0 of 1056 | 0 | OK |
| duplicate results (same pair within 3 days) | 0 | 0 | OK |
| forecast pairs within 3 days (provisional dates, ADR 0090) | 1 | report | INFO |
| result with a future kick-off / result and void | 0 / 0 | 0 | OK |

Alarms: freshness:odds.

## 2. Input drift (model outputs and league mix) — INSUFFICIENT_DATA

Reference: fixtures of the first 14 days of the record from 2026-10-09 (n 166); current: from 2026-10-23, the last 14 days, never overlapping the reference (n 71).

- p_home: INSUFFICIENT_DATA (n 166 / 71, minimum 100)
- p_over_2_5: INSUFFICIENT_DATA (n 166 / 71, minimum 100)
- p_btts: INSUFFICIENT_DATA (n 166 / 71, minimum 100)
- league_mix: INSUFFICIENT_DATA (n 166 / 71, minimum 100)
- odds_share: reference 0.8976, current 0.8732
- low_confidence_share: reference 0.0, current 0.0

Alarms: none.

## 3. Performance drift (settled matches only) — INSUFFICIENT_DATA

- 237 settled matches (150 needed: first 100 against the last 50); the reference / current windows span 2 / 2 ISO weeks, at least 4 each are needed: no performance verdict.

Input drift and performance drift are reported apart: input drift needs no result, performance drift waits for settled results (label latency) and never counts an open match.
