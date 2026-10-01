"""S11 (ADR 0022): ensemble orchestration -- unit tests for the helpers, plus a real end-to-end
run against the real_smoke fixture (1140 real matches, already committed and used elsewhere),
since the tiny golden fixture (12 common rows) can never clear MIN_SPLIT_ROWS*3 and would only
ever exercise the "skipped" path.
"""

import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from src.evaluation.run_ensemble import (
    EnsembleError,
    _common_fixtures,
    _grouped_metrics,
    _probs_matrix,
    _split_three,
    render_ensemble_md,
    run_ensemble,
    run_ensemble_variants,
)
from src.schemas import PredictionRecord

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from scripts.ci_real_data_sanity import FIXTURE_ROOT  # noqa: E402

T0 = datetime(2024, 3, 1, tzinfo=UTC)


def pred(fixture_id, model_id, p=(0.5, 0.3, 0.2)):
    return PredictionRecord(
        fixture_id=fixture_id, model_id=model_id, model_version="1.0.0", feature_version="fv2",
        data_version="dv-aaaaaaaaaaaa", kickoff_utc=T0, information_cutoff=T0, generated_at=T0,
        p_home=p[0], p_draw=p[1], p_away=p[2],
    )  # fmt: skip


# -------------------------------------------------------------------------- _common_fixtures
def test_common_fixtures_requires_every_model():
    preds = [pred("f1", "a"), pred("f1", "b"), pred("f2", "a")]  # f2 missing from b
    assert _common_fixtures(preds, ["a", "b"]) == ["f1"]


def test_common_fixtures_raises_when_nothing_is_shared():
    preds = [pred("f1", "a"), pred("f2", "b")]
    with pytest.raises(EnsembleError):
        _common_fixtures(preds, ["a", "b"])


# ----------------------------------------------------------------------------- _probs_matrix
def test_probs_matrix_preserves_requested_order():
    preds = [pred("f1", "a", (0.1, 0.2, 0.7)), pred("f2", "a", (0.9, 0.05, 0.05))]
    m = _probs_matrix(preds, "a", ["f2", "f1"])
    np.testing.assert_allclose(m[0], [0.9, 0.05, 0.05])
    np.testing.assert_allclose(m[1], [0.1, 0.2, 0.7])


# ------------------------------------------------------------------------------ _split_three
@pytest.mark.parametrize("n", [3, 9, 10, 100, 101])
def test_split_three_covers_every_row_exactly_once(n):
    a, b, c = _split_three(n)
    covered = set(range(*a.indices(n))) | set(range(*b.indices(n))) | set(range(*c.indices(n)))
    assert covered == set(range(n))
    assert a.stop == b.start and b.stop == c.start  # contiguous, no gaps or overlaps


# -------------------------------------------------------------------------- _grouped_metrics
def test_grouped_metrics_groups_by_label_and_counts_n():
    rng = np.random.default_rng(0)
    probs = rng.dirichlet([2, 2, 2], size=10)
    y = rng.integers(0, 3, 10)
    labels = ["EPL"] * 6 + ["LALIGA"] * 4
    out = _grouped_metrics(probs, y, labels, ["log_loss", "accuracy"], bins=10)
    assert set(out) == {"EPL", "LALIGA"}
    assert out["EPL"]["n"] == 6 and out["LALIGA"]["n"] == 4


# ------------------------------------------------------------------------ run_ensemble_variants
def test_run_ensemble_variants_skips_gracefully_below_min_rows():
    rng = np.random.default_rng(0)
    probs_by_model = {"a": rng.dirichlet([2, 2, 2], size=10), "b": rng.dirichlet([2, 2, 2], size=10)}
    y = rng.integers(0, 3, 10)
    variants = run_ensemble_variants(probs_by_model, y, ["log_loss"], bins=10)
    assert len(variants) == 4
    assert all(v.skipped for v in variants)
    assert all(v.reason for v in variants)


def test_run_ensemble_variants_runs_all_four_above_min_rows():
    rng = np.random.default_rng(1)
    n = 90
    probs_by_model = {
        "a": rng.dirichlet([2, 2, 2], size=n),
        "b": rng.dirichlet([2, 2, 2], size=n),
    }
    y = rng.integers(0, 3, n)
    variants = run_ensemble_variants(probs_by_model, y, ["log_loss", "accuracy"], bins=10)
    names = {v.name for v in variants}
    assert names == {"simple_mean", "validation_weighted", "logistic_stacking", "lightgbm_stacking"}
    for v in variants:
        assert not v.skipped
        assert set(v.raw_metrics) == {"log_loss", "accuracy"}
        assert set(v.calibrated_metrics) == {"log_loss", "accuracy"}
        assert v.base_model_ids == ["a", "b"]


def test_run_ensemble_variants_reports_by_league_and_season_when_given():
    rng = np.random.default_rng(2)
    n = 90
    probs_by_model = {"a": rng.dirichlet([2, 2, 2], size=n), "b": rng.dirichlet([2, 2, 2], size=n)}
    y = rng.integers(0, 3, n)
    leagues = np.array((["EPL"] * (n // 2)) + (["LALIGA"] * (n - n // 2)))
    seasons = np.array((["2022-23"] * (n // 2)) + (["2023-24"] * (n - n // 2)))
    variants = run_ensemble_variants(probs_by_model, y, ["log_loss"], 10, leagues, seasons)
    for v in variants:
        assert v.by_league is not None and v.by_season is not None


# -------------------------------------------------------------------------- render_ensemble_md
def test_render_ensemble_md_has_no_single_winner_and_shows_skip_reason():
    rng = np.random.default_rng(3)
    probs_by_model = {"a": rng.dirichlet([2, 2, 2], size=5), "b": rng.dirichlet([2, 2, 2], size=5)}
    y = rng.integers(0, 3, 5)
    variants = run_ensemble_variants(probs_by_model, y, ["log_loss"], 10)
    md = render_ensemble_md(variants, ["log_loss"])
    assert "No single winner" in md
    assert "SKIPPED" in md


# ------------------------------------------------------------------------------- end-to-end
def test_run_ensemble_end_to_end_on_real_data():
    """Real data (1140 matches) clears MIN_SPLIT_ROWS*3 and exercises the actual fit path for
    all four variants, including a real sklearn/lightgbm fit -- not just the skip branch."""
    from src.evaluation.walk_forward import run_walk_forward

    run_walk_forward(FIXTURE_ROOT, "research")  # ensures predictions.jsonl exists for this run
    out_dir = run_ensemble(FIXTURE_ROOT, "research")
    import json

    report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    assert report["n_common_oof_fixtures"] > 45
    variants = {v["name"]: v for v in report["variants"]}
    assert set(variants) == {"simple_mean", "validation_weighted", "logistic_stacking", "lightgbm_stacking"}
    for v in variants.values():
        assert not v["skipped"]
        assert v["raw_metrics"]["log_loss"] > 0
        assert v["calibrated_metrics"]["log_loss"] > 0
    md = (out_dir / "report.md").read_text(encoding="utf-8")
    assert "No single winner" in md
