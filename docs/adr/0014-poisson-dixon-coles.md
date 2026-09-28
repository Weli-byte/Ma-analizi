# ADR 0014: Poisson / Dixon-Coles goal model methodology (S5)

## Status
Accepted — 2026-09-28 (written retroactively during S0-S7 hardening; see audit finding H-01).
Phases 10-14 of the hardening pass (IPF convergence criteria, joint-MLE DC variant, rho search,
time decay, tail-mass measurement) extend or add variants to this ADR's scope; a NEW variant
(e.g. `DC_v2_joint_mle`) that changes the estimator gets its own ADR entry appended here or a
follow-up ADR, not a silent replacement of the model this ADR documents.

## Context
S4 (Elo) models team strength as a single scalar. Football's plan (`master_sprint_plan.raw.txt`,
"S5 -- POISSON + DIXON-COLES") calls for a goal-scoring-process model: attack/defense strengths,
home advantage, a scoreline probability matrix, and the Dixon & Coles (1997) low-score
correlation correction — giving both a full scoreline distribution (useful later for
correct-score / goal-difference metrics) and a 1X2 probability via aggregation.

## Problem
Three estimation problems:
1. **Identifiability.** `attack_i -> attack_i + c`, `defense_i -> defense_i - c` for any team
   leaves every `lambda_home`/`lambda_away` unchanged — attack/defense are only identified up to
   a shift.
2. **Independence assumption.** A base Poisson model assumes home and away goals are
   conditionally independent given the two lambdas — known to be slightly wrong in practice,
   most visible in low-scoring games (Dixon & Coles' own motivating finding).
3. **No `scipy`/general nonlinear solver available at S5's time** (same situation as S4):
   attack/defense/home-advantage needed a dependency-free, deterministic, leakage-safe MLE
   procedure.

## Decision
- **Iterative Proportional Fitting (IPF)** for `alpha` (attack), `beta` (defense), `home_adv`
  (`PoissonModel._ipf_update`, `src/models/poisson_dc.py`): each sweep rescales one team's
  attack (then defense, then the shared home-advantage) in log-space by the log-ratio of actual
  to model-expected goals, holding other parameters fixed. This is the classical algorithm for
  fitting Poisson log-linear models and is exactly equivalent to Poisson MLE with a canonical
  link — not a heuristic approximation.
- **Identifiability**: `alpha` is recentered to mean zero after every sweep
  (`mean_a = np.mean(...); self.attack = {t: v - mean_a ...}`), resolving the shift invariance.
  Verified: `diagnostics["mean_attack"]` reads `0.0` on real data
  (`tests/test_poisson_dc.py::test_attack_is_recentered_for_identifiability`).
- **Fixed sweep count (as shipped in S5):** `ipf_sweeps` (config default 40), no per-iteration
  convergence check. Chosen as a simple, deterministic, "obviously converges in practice for
  football-scale data" default rather than an adaptively-terminating loop, given no numerical
  library was available to lean on for tolerance/status reporting conventions.
- **Dixon-Coles low-score correction** (`DixonColesModel._tau`): multiplies the four scorelines
  {0-0, 1-0, 0-1, 1-1} by `tau(x, y; rho)` (Dixon & Coles' original formulation), renormalizes.
  `rho` is fit AFTER `alpha`/`beta`/`home_adv` are fixed, via a 1-D grid search
  (`_fit_extra`/`_dc_loglik`, step `0.005` over `[-0.2, 0.2]`) maximizing the DC-corrected
  training log-likelihood — a SEQUENTIAL fit, not a joint MLE of all parameters at once.

## Alternatives considered
- **Full joint MLE of attack/defense/home_adv/rho simultaneously** via a general nonlinear
  optimizer: the theoretically preferred approach (this is what "DC_v2_joint_mle", audit finding
  M-05, will implement as a SEPARATE model in the hardening pass), rejected for S5's initial
  implementation because no general optimizer dependency existed yet and the sequential
  attack/defense-then-rho approach is a well-documented, reasonable approximation used in
  practice (rho is typically small and its interaction with attack/defense estimates is weak).
- **Continuous rho optimization** instead of a grid: rejected for S5 — a grid is simpler to
  reason about deterministically and rho's range is small and bounded; revisited in Phase 13.
- **No Dixon-Coles correction at all** (pure independent Poisson only): rejected — the whole
  point of including `DixonColesModel` is to correct the KNOWN low-score correlation; `PoissonModel`
  is kept as the uncorrected baseline for comparison, not replaced.

## Why chosen
IPF is the textbook-correct, deterministic, numerically stable way to fit this exact class of
log-linear model without a general-purpose optimizer dependency, and it was already sufficient to
produce a real `home_adv≈0.19` and `rho≈-0.06` on real data — both in the expected sign/magnitude
range from the football analytics literature, which is the correctness signal that mattered most
for S5's scope.

## Leakage considerations
- `fit(train)` filters to rows carrying `home_goals`/`away_goals` (added to `EvalRow` specifically
  for this model, see `src/evaluation/dataset.py`) and reads ONLY the `train` rows passed to it;
  future fixtures are never read (`tests/test_poisson_dc.py::test_fit_requires_goal_counts_and_never_fabricates_them`
  and the model's `fit`/`predict_proba` separation, mirroring the `BaselineModel` contract every
  other model follows).
- An unseen team (present in eval rows, absent from `train`) falls back to league-average
  strength (`alpha=beta=0`) and is COUNTED in `diagnostics["unseen_team_rows"]`, never silently
  imputed without a trace.

## Evaluation consequences
Real validation-period numbers: `poisson` Log Loss 0.9998, `dixon_coles` 1.0012 — both close to
`historical_prior` (1.0615) and behind `elo`/`market_implied`. `rho=-0.06` matches the known
sign/magnitude of the Dixon-Coles low-score effect from the original paper and subsequent
football-analytics literature — a real correctness signal, not just a plausible-looking number.

## Computational consequences
O(n_teams × n_train_rows × ipf_sweeps) per fit (40 sweeps × ~2280 rows × ~48 teams — sub-second
on real data). `rho` grid search: 81 candidate values × O(n_train_rows) each — also sub-second.

## Known limitations
- No per-iteration convergence check; `ipf_sweeps=40` is an untested-for-sufficiency fixed budget
  (audit finding M-04) — Phase 10 adds `max_iterations`/`convergence_tolerance` with reported
  `converged`/`iterations_used`/`final_delta`, WITHOUT changing the IPF algorithm itself.
- `rho` fit sequentially after attack/defense/home_adv, on a coarse grid (M-05) — Phase 12 builds
  a joint-MLE variant as `DC_v2_joint_mle`, compared under walk-forward; `dixon_coles` (this ADR's
  DC_v1) is NOT deleted or silently replaced regardless of that comparison's outcome.
- No time decay (M-06) — same class of gap as Elo's M-03, deferred to Phase 14 as an optional,
  separately-evaluated variant.
- `max_goals=10` truncation folds rare high-scoring tails into the boundary cell; tail mass is
  not currently measured (L-02) — Phase 15 adds `captured_mass`/`tail_mass` diagnostics.

## Revisit conditions
Revisit if: the core goal-generating-process assumption changes (e.g. moving off Poisson
entirely, such as to a negative-binomial or zero-inflated model), IPF is replaced by a different
estimator for the base attack/defense/home_adv parameters, or `DC_v2_joint_mle` is promoted to
the DEFAULT `dixon_coles` model (that promotion itself requires documented walk-forward evidence
per Phase 12, and would be recorded as an amendment here or a dedicated follow-up ADR).
