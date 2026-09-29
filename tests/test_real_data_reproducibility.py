"""S0-S7 hardening, Phase 5 — dedicated real-data reproducibility test (audit finding H-04).

Golden reproducibility (`test_golden_and_reproducibility.py`) only exercises a 36-fixture
SYNTHETIC project. This is the real-data equivalent: run pipeline -> features -> baselines ->
walk-forward TWICE from a clean state on `tests/fixtures/real_smoke/root` (1140 real EPL
matches) and assert every layer's hash is byte-identical between the two runs — normalized data,
features, EVERY model's predictions (baselines, Elo, Poisson, Dixon-Coles, XGBoost, LightGBM),
and walk-forward's own ledger.

This reuses `scripts/ci_real_data_sanity.run_sanity` (Phase 3) rather than re-implementing the
same two-runs-and-diff logic — that function already performs exactly this comparison as part of
its own checks. This file exists as a separately-named, separately-discoverable test specifically
for REPRODUCIBILITY (distinct from Phase 3's SANITY/correctness framing), per audit finding H-04.

Deliberately a SAME-PROCESS, single test-function comparison (not two separate CI jobs comparing
a stored value): audit finding H-09 showed that CPU-dependent SIMD dispatch can vary Elo/
Poisson's iterative floating-point fit ACROSS separate ephemeral CI runner instances, but never
within one process/one runner. A same-process dual run sidesteps that entirely by construction —
it is not a workaround for H-09, it is the same design `test_golden_and_reproducibility.py`
already uses for its own reproducibility tests.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:  # `scripts/` isn't an installed package; CI runs bare
    sys.path.insert(0, str(REPO_ROOT))  # `pytest` (no CWD auto-insertion), unlike `python -m pytest`

from scripts.ci_real_data_sanity import FIXTURE_ROOT, run_sanity  # noqa: E402


def test_real_data_reproducibility_end_to_end():
    """Two full pipeline->features->baselines->walk-forward runs from a clean state, same
    process: `run_sanity` raises SanityError itself if any hash differs (predictions, report,
    metrics — for both run_baselines and walk_forward, across every configured model)."""
    run_sanity(FIXTURE_ROOT, "research")  # research: repo checkout may be dirty locally; CI's
    # real-data-sanity job already covers --mode strict on a clean checkout for this same check
