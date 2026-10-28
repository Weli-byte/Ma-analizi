# Phase 2 baseline (re-measured, ADR 0085)

Source: `track_record.json` generated 2026-10-27T09:52:09.322234+00:00 · 194 settled matches from 2026-10-09 to 2026-10-25 · prospective forecasts only (published before kick-off, ADR 0048).

## First 138 matches by date (the sample quoted in the plan, to 2026-10-20)

| market | model | trivial baseline | baseline rate |
|---|---|---|---|
| 1X2 | 94/138 = 68.1 % (59.9-75.3) | always home | 73/138 = 52.9 % (44.6-61.0) |
| over/under 2.5 | 84/138 = 60.9 % (52.5-68.6) | always over | 95/138 = 68.8 % (60.7-76.0) |
| BTTS | 76/138 = 55.1 % (46.7-63.1) | always yes | 87/138 = 63.0 % (54.7-70.6) |
| exact score | 10/138 = 7.2 % (4.0-12.8) | - | - |

## All 194 settled matches

| market | model | trivial baseline | baseline rate |
|---|---|---|---|
| 1X2 | 125/194 = 64.4 % (57.5-70.8) | always home | 100/194 = 51.5 % (44.6-58.5) |
| over/under 2.5 | 122/194 = 62.9 % (55.9-69.4) | always over | 126/194 = 64.9 % (58.0-71.3) |
| BTTS | 112/194 = 57.7 % (50.7-64.5) | always yes | 117/194 = 60.3 % (53.3-66.9) |
| exact score | 17/194 = 8.8 % (5.5-13.6) | - | - |

## Reading
- The first 138 matches reproduce the plan's figures exactly, so the earlier measurement is confirmed and its label rule is the same one used here.
- Over/under and BTTS: the model is **behind the trivial strategy** on this sample (the always-over and always-yes rates are higher). With these sample sizes the intervals overlap, so this is not proof of a worse model, but it is also no evidence of a better one. Accuracy is a secondary metric; the log loss figures in `track_record.json` are the primary ones.
- Exact score is the single most probable cell of the score grid (rarely above 12 % likely), so a low hit rate is expected and is not optimised.
- Leagues in the sample: UCL 31, EPL 30, SERIEA 30, BUNDESLIGA 27, LIGUE1 25, LALIGA 24, UEL 11, SUPERLIG 9, UECL 7. Per-league rates with fewer than 20 matches are noise.
- Cup matches are scored on the 90-minute result as stored by the settlement sources; awarded and abandoned matches are not in the sample.
