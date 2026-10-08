# ADR 0041 — Match intelligence: scores, goals, corners, cards (owner request, 2026-10-09)

Status: accepted

## Decision
`src/markets/` adds, per upcoming fixture: 1X2, double chance, draw-no-bet, handicap, correct-score
distribution (top scores), goals (expected, over/under 0.5-4.5, BTTS, team totals, clean sheets) and, from the
corners / yellow cards / shots-on-target columns that football-data.co.uk provides, expected totals and
over/under lines. `tips` ranks the most probable side of each market with an honest lean label.
Models: multiplicative attack x defence rates by league (IPF, exponential time decay, shrinkage); goals =
Poisson + Dixon-Coles low-score term; other counts = negative binomial (moment dispersion).
Artifacts: immutable content-hashed `artifacts/markets/<HOME>__<AWAY>__<DATE>/intel-<stamp>.json`
(`information_cutoff` = run time), written by `python -m src.markets.run` (also called by the snapshot tick),
served by `GET /v1/fixtures/{id}/intelligence`, `GET /v1/tips` and the dashboard.

## Evidence (python -m src.markets.evaluate, validation 2022-24, 1520 matches, 30-day refits, final test NOT loaded)
- Clear skill over naive baselines: correct score (-0.119 nats), 1X2 (log loss 0.974 vs 1.062), over/under 2.5
  (0.6758 vs 0.6895), corners (all lines), shots on target 8.5-10.5.
- No clear difference: BTTS, yellow cards (all lines), shots on target 7.5. Reported as such in the output.
- Headline 1X2: the de-vigged closing market beat the model on 2023-24 (log loss 0.9253 vs 0.9505), the blend
  weight chosen on 2022-23 was 1.00. Therefore when a complete EXACT odds set exists the headline 1X2 is the
  market (`market_blend_weight: 1.0`), and the correct-score list is rescaled to be consistent with it. Over/under
  and count markets always come from the model.
- No odds exist for corners/cards/scores in the exact feed, so value picks stay 1X2-only; the other markets are
  probabilities and tips, not value claims.

## Rules kept
Hyper-parameters were set on train/validation only; production inference uses all matches finished before the
run time as history (same as feature building); unknown/promoted teams get neutral strength and are flagged.
Tips are probabilities with a lean label, never guarantees.
