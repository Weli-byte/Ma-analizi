# ADR 0017: Golden test coverage extended to Elo/Poisson/Dixon-Coles; XGBoost/LightGBM excluded

## Status
Accepted — 2026-09-28. S0-S7 hardening pass, Phase 4 (audit finding H-03).

## Context
`tests/test_golden_and_reproducibility.py` couples the FULL chain — raw fixture → normalized
data → feature snapshot → model predictions → metrics → content hashes — into one end-to-end
regression test, but until now `tests/fixtures/golden/root/configs/model.yaml` only listed the
four S3 baselines (`always_home`, `historical_prior`, `recent_form_naive`, `market_implied`).
Elo (S4), Poisson/Dixon-Coles (S5), and XGBoost/LightGBM (S6) had their OWN dedicated unit tests
(`tests/test_elo.py`, `tests/test_poisson_dc.py`, `tests/test_gbm.py`) proving determinism in
isolation, but were never exercised by the coupled golden chain itself.

## Problem
Extending golden coverage is not just "add the model to the list" — the golden fixture is a
SYNTHETIC, deliberately tiny project (3 seasons × 12 matches = 36 fixtures total, 12 rows in the
single train season) built for fast, hand-verifiable regression testing, not for realistic model
behavior. `GBMModel.fit` (`src/models/gbm.py`) enforces `MIN_TRAIN_ROWS = 20` — a real guard
against fitting a boosted tree ensemble (with internal chronological validation split, Optuna
tuning, early stopping) on far too little data to do any of that meaningfully. The golden
fixture's 12 training rows are below that threshold.

## Decision
- **Extend** `tests/fixtures/golden/root/configs/model.yaml` to
  `[always_home, historical_prior, recent_form_naive, market_implied, elo, poisson, dixon_coles]`
  — every model whose fitting procedure has no hard minimum-data requirement incompatible with
  12 rows.
- **Do NOT add `xgboost`/`lightgbm`** to the golden fixture. Do NOT lower or bypass
  `MIN_TRAIN_ROWS` to accommodate the fixture, and do NOT enlarge the golden fixture's synthetic
  dataset just to clear that threshold (that would be a much larger, disruptive change to a
  fixture many other tests already pin byte-for-byte, entirely to serve one coverage gap).
- **Regenerated** `tests/fixtures/golden/expected/golden.json` and `normalized_fixtures.csv` via
  `scripts/update_golden.py`. Verified BEFORE accepting the new file: all four pre-existing
  models' metrics (`always_home`, `historical_prior`, `recent_form_naive`, `market_implied`) are
  BYTE-IDENTICAL to the pre-extension golden file — the extension added new models' predictions
  to the chain, it changed nothing about how the existing four are computed. New models'
  predictions land in the SAME immutable prediction ledger (`n_predictions`: 48 → 84, i.e.
  12 rows × 7 models, not a change to existing per-model behavior).
- GBM's real-data-scale determinism (same seed → same predictions, byte-identical across two
  independent full runs including Optuna tuning) is instead proven by the Phase 3 real-data
  sanity layer (`scripts/ci_real_data_sanity.py`, `docs/ci_real_data_sanity.md`) on 1140 real
  matches — arguably a STRONGER guarantee than a 12-row synthetic golden hash would give, since
  it exercises GBM's actual internal chronological split and Optuna tuning path meaningfully.

## Alternatives considered
- **Lower `MIN_TRAIN_ROWS` for golden-fixture runs only** (e.g. a test-only override): rejected —
  this is exactly the "weaken a guard to make a test pass" pattern the S0-S7 hardening task
  explicitly forbids (its Rule 2/3: do not weaken tests, do not change expected values just to
  pass). `MIN_TRAIN_ROWS=20` exists because fitting a GBM on fewer rows produces a genuinely
  unreliable/meaningless model, in golden or in production.
- **Enlarge the golden fixture's synthetic dataset** (more seasons/teams) to clear the
  threshold: rejected for THIS decision — it would change every existing model's golden metrics
  (a much bigger, harder-to-review diff), is out of Phase 4's scope, and doesn't obviously buy
  enough additional coverage to justify disrupting a fixture that many other tests
  (`test_cli_entrypoints.py`, `test_final_lock.py`, `test_walk_forward.py`, ...) already depend
  on for specific row/fixture counts. Could be revisited as its own, separately-reviewed decision.
- **Skip GBM's golden coverage silently** (say nothing): rejected — this ADR exists specifically
  so the exclusion is documented and deliberate, not a silent gap.

## Leakage considerations
No change to any model's leakage behavior. The golden chain's own `EvaluationContext` /
final-test isolation is unaffected — Elo/Poisson/Dixon-Coles read only the same
train/validation seasons the four existing models already did.

## Evaluation consequences
New golden-pinned real numbers (12-row synthetic validation set, not representative of
real-data performance — see `docs/baselines.md`/`docs/poisson_dc.md` for real numbers):
`elo` Log Loss 0.9074, `poisson` 1.1007, `dixon_coles` 1.0743. These exist to catch
DETERMINISM regressions (same input+config+seed+environment must reproduce the same hash), not
to represent expected real-world model quality.

## Computational consequences
Negligible — Elo/Poisson/Dixon-Coles fit in well under a second on 12 rows; the golden chain's
total runtime is unaffected in any noticeable way.

## Known limitations
- XGBoost/LightGBM still have no golden-chain (data→features→predictions→metrics, single
  coupled hash) coverage — only their own unit tests plus the Phase 3 real-data sanity layer.
  If a future change alters how GBM predictions get serialized into the ledger specifically
  (not GBM's own fitting logic, which IS covered), that narrow gap could theoretically go
  unnoticed by golden but would very likely still be caught by `tests/test_gbm.py` or
  `tests/test_ci_real_data_sanity.py`.
- The synthetic golden fixture's Elo/Poisson/Dixon-Coles metrics (12 rows) should never be read
  as representative of real model quality — only as regression-detection pins.

## Amendment (same day): BLAS/OpenMP thread-count reproducibility bug

Discovered while landing this ADR: `golden.json` regenerated locally (Windows) did not match
what CI's `test` job (Linux) computed live for the NEW `elo`/`poisson`/`dixon_coles` predictions
— every metric VALUE still matched to 1e-8 (`test_golden_metrics_match_with_tolerance` passed
throughout), but the exact `predictions_sha256`/`metrics_sha256`/`report_json_sha256` did not.
Root-caused by running `.github/workflows/regenerate-golden.yml` (an isolated, single-purpose
job) twice on Linux — identical hash both times — versus the full `test` job (same commit, same
Linux runner type, same Python) computing a THIRD, different hash. `always_home`/
`historical_prior`/`recent_form_naive`/`market_implied` were never affected (closed-form, no
iterative floating-point reduction); `elo`'s gradient-ascent fit and `poisson`'s IPF both run
many iterations of `np.sum`/`exp`/`log`, whose thread-parallel reduction order is not guaranteed
associative — under concurrent CI load, thread scheduling can change the last bit of a float sum.

Fix: `OMP_NUM_THREADS`/`OPENBLAS_NUM_THREADS`/`MKL_NUM_THREADS`/`NUMEXPR_NUM_THREADS=1` pinned at
`.github/workflows/ci.yml`'s workflow level (every job) and in `regenerate-golden.yml`. Verified:
after pinning, `test (3.12)` AND `test (3.14)` both reproduce the canonical Linux-generated
`golden.json` exactly. This is now a documented, general reproducibility requirement (Rule 8 —
"every experiment must be reproducible") for any future model whose fit involves iterative
numpy reductions, not just Elo/Poisson.

Windows-vs-Linux parity was NOT established (and is not claimed): thread-pinning fixed
run-to-run non-determinism WITHIN Linux CI, but a separate, expected libm/BLAS implementation
difference means a Windows-generated golden file still won't byte-match a Linux one. This is why
`docs/golden_coverage.md` now says to ALWAYS regenerate via `regenerate-golden.yml` (Linux, the
platform CI verifies against), never locally on a developer's own machine.

## Second amendment (same day): thread-pinning was insufficient — root cause is CPU/SIMD dispatch

Re-verification after the thread-pinning fix showed it was NOT the full fix: the identical
commit's `test` job computed a THIRD different `predictions_sha256` on a later CI run, with zero
code changes in between. Controlled comparison: `regenerate-golden.yml` alone (isolated process)
— 3/3 runs identical; local Windows, standalone script or under pytest — 3/3 identical; the full
`test` job on GitHub-hosted Linux runners — 2 different hashes observed across otherwise-identical
commits/runs. This pattern (stable within any single controlled process or machine, unstable only
across separate ephemeral CI runner instances) points to CPU-dependent SIMD dispatch variance in
NumPy/OpenBLAS's `exp`/`log`/`sum` implementations — different runner VMs can have different CPU
microarchitectures, and NumPy's runtime CPU-feature dispatch can select a different code path
with different last-bit rounding — not thread scheduling (already pinned) and not the platform
(Linux-to-Linux).

**Actual fix**: stop claiming byte-exact reproducibility for predictions from ANY model with an
iterative floating-point fit, across heterogeneous CI hardware. `tests/golden_util.py` now
computes `closed_form_predictions_sha256`, scoped to only `always_home`/`historical_prior`/
`recent_form_naive`/`market_implied` (no iterative fit) — genuinely byte-reproducible everywhere
observed (Windows, Linux, any CI runner instance). `test_golden_summary_matches` asserts THIS
hash exactly; the full `predictions_sha256`/`metrics_sha256`/`report_json_sha256` (which include
`elo`/`poisson`/`dixon_coles`) remain in `golden.json` for informational/same-process value but
are no longer strictly asserted. `elo`/`poisson`/`dixon_coles` correctness and stability remain
fully verified by `test_golden_metrics_match_with_tolerance` (`abs=1e-8`), which passed
throughout this entire investigation and was never the source of any false confidence — the
metric VALUES were correct the whole time; only the exact-byte-hash claim for those three models
was too strong a guarantee to make across heterogeneous hardware.

The BLAS/OpenMP thread-pinning from the first amendment is KEPT (it is still correct hygiene —
unpinned multi-threading is a real, if here not the dominant, source of non-associativity — and
costs nothing), but is no longer claimed to be sufficient on its own.

## Third amendment (Phase 6): the H-09 pattern recurred on `report_sha256` and metric tolerance

Landing Phase 6 (elo's optimizer swap, ADR-0013's amendment) hit the SAME class of issue twice
more, confirming H-09 is a general property of iterative-fit models, not an elo-gradient-ascent-
specific quirk:

1. **`report_sha256` is not exact-hash-safe either.** It embeds every model's full `diagnostics`
   dict verbatim (via `json.dumps(r.diagnostics, sort_keys=True)` in `render_md`) — including
   elo's new `optimizer.n_iter`/`optimizer.objective`, which vary by a few ULPs across CI runner
   instances just like `predictions_sha256` already did. Dropped from
   `test_golden_summary_matches`'s strict checks alongside the other three (informational only).
2. **1e-8 metric tolerance was too tight.** `elo`'s `brier` differed by ~2.4e-7 between two
   Linux-generated golden runs (same commit, same workflow, different runner instances) — inside
   the SAME class of variance H-09 describes, just below `metrics_sha256`'s bit-exact threshold
   but above `pytest.approx(abs=1e-8)`. `test_golden_metrics_match_with_tolerance` now uses
   `abs=1e-6` — still far tighter than any real decision boundary (a probability shifting by
   1e-6 changes no ranking, no bet, no report figure at any sane precision), and it is the
   measured achievable precision for this class of algorithm, not an arbitrarily loosened check.

## Revisit conditions
Revisit if: `MIN_TRAIN_ROWS` changes, the golden fixture's dataset size changes for any other
reason (at which point re-evaluating GBM inclusion is a natural side effect to check), a future
model class is added whose fitting procedure also can't run on 12 rows (same exclusion pattern
would apply, documented the same way), or `report_sha256`/metric tolerance need to move again
(if so, prefer widening the closed-form-vs-iterative SCOPE rather than the tolerance NUMBER,
following the same reasoning as the second amendment above).
