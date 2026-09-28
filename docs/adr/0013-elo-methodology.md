# ADR 0013: Elo rating methodology (S4)

## Status
Accepted — 2026-09-28 (written retroactively during S0-S7 hardening; see
`reports/remediation/S0_S7_HARDENING_AUDIT.md` finding H-01). Documents S4 as shipped; Phases
6-9 of the hardening pass extend it (optimizer, tuning, decay, MOV infra) — those become
amendments here, not a separate ADR, unless they change the core rating formula itself.

## Context
S3 shipped four non-adaptive baselines (`always_home`, `historical_prior`, `recent_form_naive`,
`market_implied`). None of them build a persistent notion of "team strength" that updates over
time. S4's plan requirement (`docs/reference/master_sprint_plan.raw.txt`, "S4 -- ELO RATING")
was leakage-safe Elo with configurable K-factor, home advantage, idempotent updates, rating
history, replay determinism, and a 1X2 probability mapping — evaluated with the same Log Loss /
Brier / RPS / Accuracy protocol as every other model.

## Problem
Two independent design problems had to be solved:
1. **Rating update ordering.** A naive implementation could easily leak a match's own outcome
   into its own prediction (update-then-predict instead of predict-then-update), or double-count
   a fixture's rating update if `fit`/`predict_proba` were called more than once over overlapping
   rows.
2. **Rating-difference → 1X2 mapping.** Raw Elo (chess) only produces a single win/loss
   expectation; football has three outcomes, and the standard chess formula
   `1/(1+10^(-diff/400))` has no notion of a draw.

## Decision
- **Sequential replay, strict pre-match/post-match separation**
  (`EloModel._pre_match_diff` / `_apply_result`, `src/models/elo.py`): every prediction reads
  `self.ratings` BEFORE the fixture's own update is applied; the update runs immediately after,
  using the fixture's real (already-known, post-hoc) outcome. `_apply_result` is idempotent per
  `fixture_id` (tracked in `self._processed`), so a fixture can never be double-counted even if
  `fit`/`predict_proba` overlap.
- **3-outcome ordinal logistic ("proportional odds") mapping**
  (`EloModel._fit_outcome_mapping`/`_map_probs`): rating difference (+ home advantage) is mapped
  through two ordered thresholds (`theta1 < theta2`, parameterized via a softplus gap for the
  constraint) fit ONLY on the training replay's own pre-match diffs and outcomes — never on
  evaluation data. This is a standard, well-understood extension of Elo to three ordered
  outcomes (away < draw < home), distinct from ad hoc heuristic draw-probability formulas.
- **Fitting method (as shipped in S4):** a hand-rolled batch gradient-ascent loop (`lr=0.05`,
  `800` fixed iterations, `beta` clipped to `[-6, 6]` to prevent saturation blow-up after an
  earlier numerically unstable attempt — see `docs/baselines.md`/git history for the pre-scaling
  version that diverged to `beta≈6`). No dependency on `scipy` existed in the project at S4's
  time (added later, transitively, by S6's `shap`).

## Alternatives considered
- **Raw win-equity Elo with a fixed empirical draw-rate split** (used by some simpler football
  Elo models): rejected — throws away the rating-difference signal for the draw probability
  entirely (draw rate would be constant regardless of how close the match is), which is known to
  be a worse calibration than an ordinal-logit fit.
- **A full multinomial (unordered) softmax over 3 classes** with rating difference as the sole
  feature: rejected — discards the known ORDER of outcomes (away < draw < home is not arbitrary;
  a draw is "between" a home and away win in rating-difference space), which the ordinal model
  encodes structurally and a generic multinomial model would have to learn implicitly with more
  parameters for no benefit here (single scalar predictor).
- **`scipy.optimize.minimize` from the start:** not available as a direct dependency at S4's
  time; deferred to the hardening pass (Phase 6 / audit finding M-01) now that `scipy` is already
  in the dependency tree transitively.

## Why chosen
Ordinal logistic regression on rating difference is the standard, textbook way to extend a
single-scalar-strength rating system to an ordered multi-outcome target, requires only 3 free
parameters (`beta`, `theta1`, `theta2`), and is fit exclusively on the training replay — matching
the project's "fit only on training period" rule while giving Elo a real chance at a
well-calibrated draw probability instead of a constant one.

## Leakage considerations
- Prediction for fixture `i` reads `self.ratings` as it stood immediately before `i`'s own
  update (verified by `tests/test_elo.py::test_prediction_uses_only_pre_match_rating`).
- The ordinal-logit mapping is fit ONLY on `train` rows passed to `fit()`; `predict_proba` never
  refits the mapping, only replays ratings forward using rows it is given (which, in the
  `evaluate()` runner, are the validation/test rows — their OWN outcomes are used only to
  ADVANCE the rating state for subsequent predictions, never to inform the CURRENT prediction).
- Idempotent updates (`tests/test_elo.py::test_idempotent_update_never_applies_twice`) prevent a
  fixture from being counted twice, which would otherwise let a match's outcome influence its own
  rating twice over if `fit`/`predict_proba` were called with overlapping row sets.

## Evaluation consequences
Reported via the standard runner (Log Loss, Brier, RPS, ECE, Accuracy) alongside every other
model — see `docs/baselines.md`. Real validation-period numbers (train 2019-20..2021-22,
validation 2022-23..2023-24): Log Loss 0.9718, close to `market_implied`'s 0.9476 and ahead of
`historical_prior` (1.0615). These numbers use UNTUNED `k_factor=20`, `home_advantage=60`
(config defaults) — see audit finding M-02; tuning is Phase 7 of the hardening pass, not part of
this ADR's decision.

## Computational consequences
O(n_teams) rating-dict lookups per fixture, O(iterations × n_train_rows) for the gradient-ascent
fit (currently 800 × training-set-size, ~2280 rows on real data — sub-second in practice).
Negligible compared to GBM/Optuna cost.

## Known limitations
- Fitting uses a hand-rolled optimizer with no convergence tolerance/status reporting (audit
  finding M-01) — Phase 6 replaces this with `scipy.optimize.minimize` and stores convergence
  metadata, WITHOUT changing the underlying ordinal-logit formulation this ADR documents.
- `k_factor`/`home_advantage` are fixed, untuned hyperparameters (M-02) — Phase 7 adds
  walk-forward-driven tuning.
- No time decay (M-03) — Phase 8 adds it as an optional, separately-evaluated variant.
- `use_margin_of_victory` is real code but permanently inert (no goal-margin feature source
  exists) — this is a deliberate, documented no-op (L-01), not a limitation to "fix" by
  fabricating data.

## Revisit conditions
Revisit this ADR (not just the model card) if: the core rating-update formula changes (e.g.
switching away from the standard Elo expected-score formula), the outcome mapping changes from
ordinal-logit to a different family, or a goal-margin data source becomes available and MOV is
actually activated (at which point its evaluation impact must be reported, not assumed).
