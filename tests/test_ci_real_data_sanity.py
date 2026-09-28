"""S0-S7 hardening Phase 3: the real-data sanity layer itself, exercised in the test suite too
(not just as a standalone CI script) — and a staleness guard for its committed requirements.lock
copy, which must never silently drift from the project's real lock file."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:  # `scripts/` isn't an installed package; CI runs bare
    sys.path.insert(0, str(REPO_ROOT))  # `pytest` (no CWD auto-insertion), unlike `python -m pytest`

from scripts.ci_real_data_sanity import FIXTURE_ROOT, run_sanity  # noqa: E402


def test_fixture_lock_matches_project_lock():
    """The real-data fixture carries its own requirements.lock (provenance needs one at its
    root); it must be kept in sync with the real one or provenance/dependency_lock_hash would
    silently diverge from what the rest of the project actually uses."""
    fixture_lock = (FIXTURE_ROOT / "requirements.lock").read_text(encoding="utf-8")
    project_lock = (REPO_ROOT / "requirements.lock").read_text(encoding="utf-8")
    assert fixture_lock == project_lock, (
        "tests/fixtures/real_smoke/root/requirements.lock is stale: "
        "copy the project's requirements.lock over it again"
    )


def test_real_data_sanity_passes_end_to_end():
    """Slower than the rest of the suite (~15s: two full pipeline->features->baselines-> "
    "walk-forward runs on 1140 real matches) but still fast enough for every PR."""
    run_sanity(FIXTURE_ROOT, "research")  # research: this repo checkout may be dirty locally;
    # CI's real-data-sanity job runs the same function in --mode strict against a clean checkout
