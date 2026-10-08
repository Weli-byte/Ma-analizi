# Match intelligence (ADR 0041)

```
python -m src.markets.evaluate     # out-of-sample report: artifacts/markets/evaluation/evaluation.md
python -m src.markets.run          # artifacts for the next fixtures (PL + PD); --force to refresh
GET /v1/fixtures/{id}/intelligence   GET /v1/tips?min_probability=0.6
```
Per fixture: 1X2, double chance, draw-no-bet, handicap, top correct scores, goals O/U 0.5-4.5, BTTS, team
totals, clean sheets, corners / yellow cards / shots on target (expected + O/U lines), ranked tips, data-quality
flags. Read ADR 0041 for what is and is not better than a naive baseline.
