# Poisson / Dixon-Coles goal model (S5)

`src/models/poisson_dc.py`: `PoissonModel` (`model_id="poisson"`) and `DixonColesModel`
(`model_id="dixon_coles"`, subclasses `PoissonModel`).

## Model

Team `i` has log-scale attack strength `alpha_i` and defense strength `beta_i`. For a match
with home team `h`, away team `a`:

```
lambda_home = exp(alpha_h + beta_a + home_adv)
lambda_away = exp(alpha_a + beta_h)
```

Goals are modelled as Poisson(lambda_home) / Poisson(lambda_away). The scoreline probability
matrix is the outer product of the two Poisson pmfs truncated at `max_goals` (config,
default 10; residual tail mass is folded into the last row/column so the matrix still sums to
1), and 1X2 = strictly-lower-triangular / diagonal / strictly-upper-triangular sums.

`DixonColesModel` multiplies the four low-score cells {0-0, 1-0, 0-1, 1-1} by the Dixon & Coles
(1997) `tau(x, y; rho)` correction for the observed negative correlation between home and away
goals in low-scoring games, then renormalizes.

## Fitting (train period only)

`alpha`/`beta`/`home_adv` are estimated by iterative proportional fitting (IPF): each sweep
rescales one team's attack (then defense, then the shared home-advantage) in log-space by the
log-ratio of actual to model-expected goals, holding the other parameters fixed — the classical
algorithm for Poisson log-linear models, equivalent to Poisson MLE. `ipf_sweeps` sweeps (config,
default 40). `rho` is fit afterwards by a 1-D grid search (`rho_grid`, default
`(-0.2, 0.2, 0.005)`) maximizing the Dixon-Coles-corrected training log-likelihood with
`alpha`/`beta`/`home_adv` already fixed.

`fit(train)` reads only the rows passed to it — never validation or final-test fixtures
(same discipline as every other model in this repo; ADR 0004/0012). A row without
`home_goals`/`away_goals` is excluded and counted in `diagnostics["rows_missing_goals"]`,
never silently treated as goalless.

## Identifiability

`alpha_i -> alpha_i + c`, `beta_i -> beta_i - c` leaves every `lambda` unchanged for any team,
so attack/defense are identified only up to that shift. Resolved by recentering `alpha` to
mean zero after every IPF sweep — `diagnostics["mean_attack"]` should read `0.0`.

## Assumptions & limitations

- Poisson goal counts, home/away independent (uncorrected in `PoissonModel`; corrected for
  the four lowest scorelines only in `DixonColesModel`).
- No time decay — every training match has equal weight regardless of recency.
- A team absent from training falls back to league-average strength (`alpha=beta=0`);
  counted in `diagnostics["unseen_team_rows"]`, never silently imputed as "average" without
  a record of it happening.
- `max_goals` truncation: scorelines above it are folded into the boundary row/column rather
  than dropped, so probabilities still sum to 1, but the model does not distinguish e.g. a
  6-0 from a 7-0 once both exceed `max_goals`.

## Training window (current config, `configs/evaluation.yaml`)

train 2019-20..2021-22 → evaluated on validation 2022-23..2023-24 (final test 2024-25..2025-26
is locked; never read by this fit).

## Metrics (validation, `configs/model.yaml` defaults)

Reported via the standard runner (Log Loss, Brier, RPS, ECE, Accuracy) alongside every other
model in `artifacts/runs/<...>/report.md`; see `docs/baselines.md` for the shared benchmark
protocol. No exact-scoreline metric is part of the formal metric set (only H/D/A is scored
against `EvaluationConfig.metrics`), but the full scoreline matrix is available via
`PoissonModel.scoreline_matrix(lambda_home, lambda_away)` for downstream (e.g. correct-score)
use.
