# ADR 0072 - The hierarchical goal-market model is shown to users as "Deneysel model (beta)"

Date: 2026-10-23. Status: accepted (owner decision). Builds on ADR 0071.

## Context
Sprint 4c (ADR 0071) found that a hierarchical Poisson / Dixon-Coles model whose variance components are chosen by nested walk-forward is better than the production goal models on the development folds for over/under (-0.0122 log loss) and BTTS (-0.0072), and worse than the production Elo for the result (+0.0076). The Sprint 5 gate approved no league model and the frozen holdout is consumed, so no prospective evidence exists yet. On 2026-10-23 the owner decided that the new model's results are shown to users.

## Decision
* The model (`src/markets/experimental.py`, wrapper of `league_models.per_league.HierarchicalDC`) is fitted on the product's own history (the openfootball-based `StatMatch` rows available at the run time, last 8 seasons) in every `python -m src.markets.run`, and its output is stored in each new artifact as an `experimental` block: over/under 1.5 / 2.5 / 3.5, BTTS, expected goals, the six most likely scorelines, the selected variance components, the number of fitted matches and a short fit digest.
* It is shown as a separate card "Deneysel model (beta)" on the match page, next to the current model's numbers (never instead of them), with the evidence statement: better than the current model on over/under and BTTS in past development matches, not better on the result, not yet verified on live matches. The API returns the block as `experimental` (null for older artifacts).
* The 1X2 headline, the tips, the value suggestions and the track record keep using the current model. The experimental model is NOT used for the result because the evidence says it is worse there.
* Neutral-venue matches (cup finals) get no block (the model has no home-advantage switch). A failed or disabled fit gives no block; the run prints the reason. `MARKETS_EXPERIMENTAL=0` switches it off for new artifacts.
* The league-model kill switch and canary of ADR 0068 govern the Sprint 5/6 league candidates and are unchanged; this model is a separate product path with its own label and its own off switch.

## Limits (stated to users and here)
* No live verification: the shadow families `P_dc_sep` / `H_dc_hier` (ADR 0071) collect prospective evidence on four leagues; the owner decides what to do with it. If they come out worse than the current model, switch the card off.
* Development evidence is selection-contaminated by the usual amount (340 comparisons in ADR 0071, about 8 supported by chance); the pooled over/under and BTTS gains are far outside that.
* The fit uses the same history as the product (including recent football-data.org results already used by the product); the licence status of those sources is unchanged (RESEARCH_ONLY, `docs/data_sources/licensing.md`). A commercial launch is still gated on the owner's provider terms.
* The track record (`src.markets.track`) does not yet score the experimental block separately; its own prospective scorecard is `rollout scorecard` for the shadow families.

## Rollback
Set `MARKETS_EXPERIMENTAL=0` in the cloud workflow and locally (new artifacts carry no block; older artifacts keep theirs but are replaced by newer ones within hours); delete `experimental_card` and the `experimental` key from `service._public_intel` to remove the card and field.
