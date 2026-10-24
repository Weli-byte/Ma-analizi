# ADR 0075 - Sprint 4d: wider variance grid and time-varying team strength (no gain)

Date: 2026-10-24. Status: accepted. Builds on ADR 0071. Development folds only; the frozen holdout is consumed and was not read.

## Question
ADR 0071 left two open points: 76 of 90 selected variance values sat on the edge of their grid, and team strength was constant over time except for the decay weights. Does a much wider grid, or a random-walk team strength across seasons, improve the hierarchical goal model?

## What was built
* `dynamic_dc.DynamicPoissonDC`: Poisson / Dixon-Coles with a Gaussian random walk of team attack and defence across seasons (MAP of a state-space model, analytic gradient checked against finite differences in the tests, prediction with the team's last fitted season, unseen clubs from the country level).
* `nested.make_model` / `dc_grid` accept the walk dimension (`l2_walk`); `HierarchicalDC` takes a config section.
* `s4d_experiment.py` fits, per outer fold, H_base (the Sprint 4c model), H_ext (grid up to a team penalty of 1,000,000 = teams pooled completely, country penalty up to 128) and H_dyn (random walk, walk penalty 4 to 256), every choice by the nested walk-forward of ADR 0071, and compares with the paired block bootstrap.

## Result (10,712 development matches, 5 folds)
Pooled log loss, candidate minus H_base: 1X2 H_ext +0.0001, H_dyn +0.0009; over/under +0.0002 and +0.0004; BTTS +0.0003 and +0.0004. All six intervals contain 0 (INCONCLUSIVE). Of the per-competition comparisons only a handful are SUPPORTED or WORSE, in both directions, as expected by chance (`docs/league_models/s4d_extension.json`).
* The dynamic model chooses the stiffest walk of its grid (walk penalty 256, the largest value) for all three targets: the data prefer constant strength within the available seasons.
* With the wider grid the totals and BTTS models still pick the strongest pooling of team and country effects offered: for goal totals and BTTS team and country effects add nothing measurable; the optimum is a flat plateau, not a missed corner.

## Decision
Neither extension is adopted. The model shown on the "Deneysel model (beta)" card (ADR 0072) and gated by ADR 0073 stays the Sprint 4c hierarchical model, unchanged, so that the live gate scores one fixed model. `configs/league_models/s4c.yaml` gained two sections (its fingerprint changed; the sections used by the committed Sprint 4c outputs did not).

## Limits
Team strength per season is one random-walk step per season, not a within-season Kalman filter; with 5 outer folds and 3 inner folds there is little data to separate a moving from a constant strength. The comparison is on development folds; no prospective evidence exists for either candidate.
