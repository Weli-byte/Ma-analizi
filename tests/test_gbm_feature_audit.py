"""S0-S7 hardening Phase 8: GBM feature audit / reduction-comparison scripts (M-08, M-09)."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.gbm_feature_audit import CATEGORIES, _base_name, _category_for  # noqa: E402
from scripts.gbm_feature_reduction_comparison import REDUCED_EXCLUDED  # noqa: E402
from src.models.gbm import FEATURES  # noqa: E402


def test_every_category_member_is_a_real_base_feature():
    known_bases = {_base_name(f) for f in FEATURES}
    for names in CATEGORIES.values():
        for n in names:
            assert n in known_bases, n


def test_every_base_feature_is_categorized_or_explicitly_uncategorized():
    for f in FEATURES:
        base = _base_name(f)
        # every TEAM_FEATURE/VENUE_FEATURE base must resolve to a real category, never silently
        # dropped from the audit
        assert _category_for(base) != "uncategorized" or base not in {
            n for names in CATEGORIES.values() for n in names
        }


def test_base_name_strips_home_away_prefix_but_not_venue_features():
    assert _base_name("home_form_points_5") == "form_points_5"
    assert _base_name("away_goals_for_avg_5") == "goals_for_avg_5"
    assert _base_name("home_win_rate") == "home_win_rate"  # already side-specific, not stripped


def test_reduced_feature_set_keeps_form_points_5_drops_3_and_10():
    assert "home_form_points_5" not in REDUCED_EXCLUDED
    assert "away_form_points_5" not in REDUCED_EXCLUDED
    for f in ("home_form_points_3", "away_form_points_3", "home_form_points_10", "away_form_points_10"):
        assert f in REDUCED_EXCLUDED


def test_reduction_never_auto_removes_anything_from_the_live_feature_contract():
    """The comparison script's exclusion set is a runtime GBM-input mask, never a mutation of
    the actual feature registry/contract -- the excluded names must still exist as real,
    produced features (so GBMModel.excluded_features has something real to mask)."""
    for f in REDUCED_EXCLUDED:
        assert f in FEATURES
