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

## Amendment (S0-S7 hardening, Phases 6-9): optimizer, tuning, decay, MOV infra

- **Optimizer (M-01, closed)**: `_fit_outcome_mapping` now uses `scipy.optimize.minimize`
  (BFGS, `gtol=1e-9`, `maxiter=500`) minimizing the exact same ordinal-logit negative
  log-likelihood the hand-rolled gradient ascent targeted — no change to the underlying
  formulation, only to how it's optimized. `EloModel.optimizer_diagnostics`
  (`OptimizerDiagnostics`) always records `success`, `status`, `message`, `n_iter`, `objective`,
  `tolerance`, `initial_params`, `final_params` — surfaced in `diagnostics["optimizer"]` on every
  fit, so an unconverged result is never silently treated as success.
  **Old vs new, real data** (train 2019-20..2021-22, validation 2022-23..2023-24): Log Loss
  0.9718 (hand-rolled) → 0.9695 (scipy BFGS) — a small improvement (−0.0023), not a regression;
  Accuracy 0.557 → 0.546 (a secondary metric; per CLAUDE.md, Log Loss/Brier/RPS are primary,
  Accuracy is not optimized for). Reported here per the hardening rule "do not hide a changed
  result" — the change was expected to be neutral-to-slightly-positive (both target the same
  likelihood) and it was.
- **Hyperparameter tuning (M-02, closed)**: `EloConfig.tuning` (`EloTuningConfig`) +
  `src/models/elo_tuning.py` (`python -m src.models.elo_tuning`). Optuna search over
  `k_factor`/`home_advantage`/optional `decay_half_life_days`, objective = mean Log Loss across
  `src.evaluation.split.walk_forward_folds` (train+validation seasons only, read through a
  VALIDATION-mode `EvaluationContext` — final-test seasons structurally unreachable, same
  mechanism as ADR-0016). Baseline and tuned Elo are evaluated on IDENTICAL folds
  (`EloTuningReport`); the report lists every fold's log loss, never only the mean. Disabled by
  default (`tuning.enabled: false` in `configs/model.yaml`) — an explicit opt-in, not a
  default behavior change to the shipped `elo` model.
- **Time decay (M-03, closed)**: optional `decay_half_life_days`. A team's rating decays toward
  `initial_rating` between its matches: `_decayed_rating` applies a half-life curve
  (`0.5 ** (days_since_last_match / half_life)`) at READ time (pre-match diff and the update
  itself), never mutating the stored raw rating — so decay is purely a function of
  `(current row's own kickoff time, that team's last-seen time)`, both always causally known,
  leakage-safe by construction. `None` (default) = no decay, byte-identical to the pre-decay
  model (verified: `test_no_decay_by_default`). Off by default; comparing decayed vs
  non-decayed Elo under walk-forward is exactly what `elo_tuning.py`'s
  `decay_half_life_days_range` enables, not assumed to be better without that evidence.
- **MOV infrastructure (L-01, still inert, now more explicit)**: renamed the read path from a
  single ad hoc `result_goal_margin` to two explicitly-named fields,
  `features["goal_difference"]` and `features["margin_of_victory_available"]`, matching the
  hardening task's literal naming. Still permanently inert (`_mov_unavailable` increments,
  never silently defaults) because no such feature source exists yet — this amendment only
  clarifies the infrastructure's shape for when one does; it does not activate anything.

## Revisit conditions
Revisit this ADR (not just the model card) if: the core rating-update formula changes (e.g.
switching away from the standard Elo expected-score formula), the outcome mapping changes from
ordinal-logit to a different family, a goal-margin data source becomes available and MOV is
actually activated (at which point its evaluation impact must be reported, not assumed), or
`tuning.enabled` is ever flipped to `true` as the shipped default (that promotion needs its own
documented walk-forward evidence, same bar `dixon_coles`'s joint-MLE variant would need).
