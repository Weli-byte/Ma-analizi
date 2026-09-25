# S3 baseline benchmark — dv1 / fv1

- fit on: 2019-20 .. 2021-22
- evaluated on: 2022-23 .. 2022-23 (validation; final test untouched)
- eval fixtures: 760 | common set: 760

| model | n | Log Loss | Brier | RPS | Accuracy |
|---|---|---|---|---|---|
| always_home v1.0.0 | 760 | 17.9056 | 1.0368 | 0.4026 | 0.482 |
| historical_prior v1.0.0 | 760 | 1.0576 | 0.6384 | 0.2296 | 0.482 |
| recent_form_naive v1.0.0 | 760 | 1.0681 | 0.6446 | 0.2330 | 0.471 |
| market_implied v1.0.0 | 760 | 0.9699 | 0.5767 | 0.1996 | 0.549 |

## By league

| model | league | n | Log Loss | Brier | RPS | Accuracy |
|---|---|---|---|---|---|---|
| always_home | EPL | 380 | 17.8147 | 1.0316 | 0.4013 | 0.484 |
| always_home | LALIGA | 380 | 17.9965 | 1.0421 | 0.4039 | 0.479 |
| historical_prior | EPL | 380 | 1.0575 | 0.6388 | 0.2311 | 0.484 |
| historical_prior | LALIGA | 380 | 1.0578 | 0.6380 | 0.2280 | 0.479 |
| recent_form_naive | EPL | 380 | 1.0547 | 0.6362 | 0.2295 | 0.487 |
| recent_form_naive | LALIGA | 380 | 1.0814 | 0.6530 | 0.2365 | 0.455 |
| market_implied | EPL | 380 | 0.9620 | 0.5712 | 0.1975 | 0.555 |
| market_implied | LALIGA | 380 | 0.9778 | 0.5821 | 0.2018 | 0.542 |

## By season

| model | season | n | Log Loss | Brier | RPS | Accuracy |
|---|---|---|---|---|---|---|
| always_home | 2022-23 | 760 | 17.9056 | 1.0368 | 0.4026 | 0.482 |
| historical_prior | 2022-23 | 760 | 1.0576 | 0.6384 | 0.2296 | 0.482 |
| recent_form_naive | 2022-23 | 760 | 1.0681 | 0.6446 | 0.2330 | 0.471 |
| market_implied | 2022-23 | 760 | 0.9699 | 0.5767 | 0.1996 | 0.549 |

## Diagnostics
- always_home: unpredictable_rows=0; {}
- historical_prior: unpredictable_rows=0; {"global_prior": [0.4281, 0.2579, 0.314], "league_prior": {"EPL": [0.4202, 0.2307, 0.3491], "LALIGA": [0.436, 0.2851, 0.2789]}, "training_rows": 2280}
- recent_form_naive: unpredictable_rows=0; {"draw_rate": 0.2579, "prior_fallback_rows": 15}
- market_implied: unpredictable_rows=0; {"no_odds_rows": 0, "source_usage": {"closing:Avg": 760}}

## Notes
- always_home is degenerate (p=[1,0,0]); its log loss is dominated by the 1e-15 clip.
- market_implied uses closing odds (Avg, else B365): a reference bar for later models, NOT a pre-cutoff signal. It is not tuned and not a competitor to be 'beaten' honestly.
- recent_form_naive: fixed rule (share proportional to 1 + points last 5, draw = training draw rate).
- Metrics are on the common fixture set (fixtures every model can predict); no single winner is declared. CIs arrive in S9.
