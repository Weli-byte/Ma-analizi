# Sprint 16 - uncertainty and selective prediction (ADR 0095)

As of 2026-11-01 (live record of the markets-data branch, 2026-10-31) · `python -m src.uncertainty.study`.

Thresholds from the out-of-fold predictions of 2020-21 to 2022-23 (5898 matches), fixed before the test season and the live record were scored.

## Untouched season 2023-24 (out-of-fold, model only, 1752 matches)

| target coverage | top-probability threshold | answered | realised coverage % | accuracy % (95 %) | log loss |
|---|---|---|---|---|---|
| 100 % | 0.000 | 1752 | 100.0 | 53.0 (50.7-55.4) | 0.9764 |
| 80 % | 0.413 | 1428 | 81.5 | 56.2 (53.6-58.8) | 0.9494 |
| 60 % | 0.473 | 1067 | 60.9 | 60.5 (57.6-63.4) | 0.9079 |
| 40 % | 0.550 | 697 | 39.8 | 66.4 (62.8-69.8) | 0.8379 |

## Live prospective record (the model's own 1X2, 237 settled matches)

| target coverage | top-probability threshold | answered | realised coverage % | accuracy % (95 %) | log loss |
|---|---|---|---|---|---|
| 100 % | 0.000 | 237 | 100.0 | 62.0 (55.7-68.0) | 0.9050 |
| 80 % | 0.413 | 196 | 82.7 | 66.8 (60.0-73.0) | 0.8668 |
| 60 % | 0.473 | 144 | 60.8 | 70.8 (62.9-77.6) | 0.8104 |
| 40 % | 0.550 | 85 | 35.9 | 80.0 (70.3-87.1) | 0.6940 |

The accuracy at a lower coverage is the accuracy of the ANSWERED matches only; the 100 % row is every match. An abstained match stays in the coverage denominator.

## Confidence buckets shown to users (live evidence)

| bucket | matches | mean stated % | hit rate % (95 %) | evidence |
|---|---|---|---|---|
| among the 40 % most confident | 85 | 66.2 | 80.0 (70.3-87.1) | MEASURED |
| 40-60 % | 59 | 51.2 | 57.6 (44.9-69.4) | MEASURED |
| 60-80 % | 52 | 44.0 | 55.8 (42.3-68.4) | MEASURED |
| least confident 20 % (abstain) | 41 | 38.4 | 39.0 (25.7-54.3) | INSUFFICIENT_EVIDENCE |

Top-40 % bucket by league (live): SERIEA 16, LIGUE1 13, UCL 13, BUNDESLIGA 12, LALIGA 11, EPL 6, SUPERLIG 6, UEL 5, UECL 3. A league with a handful of matches says nothing on its own.

## Data flags (live)

- with exact odds: 209 matches, accuracy 62.7 %
- model only (no exact odds): 28 matches, accuracy 57.1 %
- low history: no match

## Exact score (live): the score is a low-frequency event; the single most likely cell is rarely right

- top1: 21/237 (8.9 %)
- top3: 73/237 (30.8 %)
- top5: 104/237 (43.9 %)
