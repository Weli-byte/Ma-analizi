# Golden chain model coverage

`tests/fixtures/golden/`: raw fixture → normalized data → feature snapshot → model predictions →
metrics → content hashes, coupled into one end-to-end regression test
(`tests/test_golden_and_reproducibility.py`). Model list:
`tests/fixtures/golden/root/configs/model.yaml`.

## Covered

`always_home`, `historical_prior`, `recent_form_naive`, `market_implied` (S3) · `elo` (S4) ·
`poisson`, `dixon_coles` (S5). Every one of these has no hard minimum-training-data requirement
incompatible with the golden fixture's 12-row training season, so they run meaningfully even on
this deliberately tiny synthetic dataset.

## Not covered here: `xgboost`, `lightgbm` (S6)

`GBMModel.MIN_TRAIN_ROWS = 20` (`src/models/gbm.py`) is a real guard, not an arbitrary limit —
fitting a boosted-tree ensemble with an internal chronological validation split, Optuna tuning,
and early stopping on fewer rows than that produces a genuinely unreliable model. The golden
fixture's train season has 12 rows. See ADR-0017 for the full reasoning: neither lowering the
guard nor enlarging the golden fixture to clear it was judged worth the disruption for this gap.

GBM determinism (same seed + config + environment → byte-identical predictions) is instead
verified at REAL-DATA scale (1140 matches, `tests/fixtures/real_smoke/`) by
`scripts/ci_real_data_sanity.py` / `tests/test_ci_real_data_sanity.py` (Phase 3 of the S0-S7
hardening pass) — running the full chain twice and diffing every hash, including GBM's. This
exercises GBM's actual internal split/tuning path meaningfully, which a 12-row synthetic fixture
could not.

## Updating the golden artifact

**Always regenerate via the `regenerate-golden` GitHub Actions workflow** (`workflow_dispatch`,
`.github/workflows/regenerate-golden.yml`, targetable at any branch), download the
`golden-artifacts-<run_id>` artifact, and copy it into `tests/fixtures/golden/expected/`. Do
**not** run `scripts/update_golden.py` locally and commit the result directly — see ADR-0017's
amendment: `elo`/`poisson`/`dixon_coles` involve iterative floating-point reductions
(`np.sum`/`exp`/`log` in a loop) whose last bit can differ between platforms (Windows vs Linux
libm/BLAS) and even between runs on the SAME platform if BLAS/OpenMP thread counts aren't
pinned. `regenerate-golden.yml` and `ci.yml` both pin
`OMP_NUM_THREADS=OPENBLAS_NUM_THREADS=MKL_NUM_THREADS=NUMEXPR_NUM_THREADS=1`; a locally-generated
file (especially on Windows) will NOT reproduce what CI computes, even though the underlying
metric VALUES match to within floating-point tolerance.

This applies ONLY for an intentional change (new model added, a fixed bug that legitimately
changes normalized data/features/predictions/metrics). Before accepting the regenerated file:
diff it against the previous one and confirm every UNCHANGED model's metrics are byte-identical;
only the NEW/intentionally-changed entries should differ. Write or update an ADR explaining why.
Never regenerate to make a failing test pass without that investigation — a golden-hash failure
is a regression signal until proven otherwise.
