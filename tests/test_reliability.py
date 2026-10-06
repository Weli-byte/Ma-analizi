"""S9: reliability curve + confidence histogram."""

import numpy as np
import pytest

from src.evaluation.metrics import ece
from src.evaluation.reliability import confidence_histogram, reliability_curve


def test_reliability_curve_on_perfect_predictions_has_zero_gap():
    p = np.array([[1.0, 0, 0]] * 10)
    y = np.array([0] * 10)
    rows = reliability_curve(p, y)
    assert len(rows) == 1
    assert rows[0]["confidence"] == pytest.approx(1.0)
    assert rows[0]["accuracy"] == pytest.approx(1.0)
    assert rows[0]["gap"] == pytest.approx(0.0)
    assert rows[0]["count"] == 10


def test_reliability_curve_flags_overconfidence_with_a_positive_gap():
    p = np.array([[0.95, 0.025, 0.025]] * 20)
    y = np.array([0] * 10 + [1] * 10)  # confident but wrong half the time
    rows = reliability_curve(p, y)
    assert len(rows) == 1
    assert rows[0]["gap"] > 0  # confidence > accuracy


def test_reliability_curve_omits_empty_bins():
    p = np.array([[1.0, 0, 0]] * 5)  # every row lands in the top bin only
    y = np.array([0] * 5)
    rows = reliability_curve(p, y, bins=10)
    assert all(r["count"] > 0 for r in rows)
    assert sum(r["count"] for r in rows) == 5


def test_reliability_curve_bin_summary_matches_ece():
    """The curve's rows are the exact decomposition ece() sums over -- cross-checked here so the
    two can never silently drift apart."""
    rng = np.random.default_rng(0)
    p = rng.dirichlet([1, 1, 1], size=300)
    y = rng.integers(0, 3, 300)
    rows = reliability_curve(p, y, bins=10)
    n = len(y)
    reconstructed = sum((r["count"] / n) * abs(r["confidence"] - r["accuracy"]) for r in rows)
    assert reconstructed == pytest.approx(ece(p, y, bins=10), abs=1e-6)


def test_confidence_histogram_counts_sum_to_n_and_ignores_correctness():
    p = np.array([[0.9, 0.05, 0.05], [0.1, 0.1, 0.8]])
    hist = confidence_histogram(p, bins=5)
    assert sum(h["count"] for h in hist) == 2


def test_confidence_histogram_omits_empty_bins():
    p = np.array([[0.4, 0.3, 0.3]] * 3)  # all near-minimum confidence
    hist = confidence_histogram(p, bins=10)
    assert all(h["count"] > 0 for h in hist)
