import math

import numpy as np
import pytest

from src.evaluation.metrics import (
    EPS,
    accuracy,
    bootstrap_ci,
    brier,
    compute_metrics,
    ece,
    log_loss,
    rps,
    validate,
)

UNIFORM = np.full((1, 3), 1 / 3)


def test_uniform_home_win_known_values():
    y = [0]
    assert log_loss(UNIFORM, y) == pytest.approx(math.log(3))
    assert brier(UNIFORM, y) == pytest.approx(2 / 3)  # (2/3)^2 + 2*(1/3)^2
    assert rps(UNIFORM, y) == pytest.approx(5 / 18)  # ((2/3)^2 + (1/3)^2) / 2


def test_perfect_prediction():
    p = np.array([[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]])
    y = [0, 1, 2]
    assert brier(p, y) == 0 and rps(p, y) == 0 and accuracy(p, y) == 1
    assert log_loss(p, y) == pytest.approx(0, abs=1e-12) and ece(p, y) == 0


def test_worst_prediction_edge_cases():
    p = np.array([[0.0, 0.0, 1.0]])
    assert log_loss(p, [0]) == pytest.approx(-math.log(EPS))  # p=0 clipped, finite
    assert brier(p, [0]) == pytest.approx(2.0) and rps(p, [0]) == pytest.approx(1.0)
    assert accuracy(p, [0]) == 0.0


def test_rps_respects_ordering_but_brier_does_not():
    assert rps([[0, 1.0, 0]], [2]) < rps([[1.0, 0, 0]], [2])
    assert brier([[0, 1.0, 0]], [2]) == brier([[1.0, 0, 0]], [2])


def test_metrics_are_means_over_samples():
    p = np.array([[0.5, 0.3, 0.2], [0.2, 0.3, 0.5]])
    y = [0, 2]
    assert log_loss(p, y) == pytest.approx(-math.log(0.5))
    assert brier(p, y) == pytest.approx(0.25 + 0.09 + 0.04)


# ------------------------------------------------------------------ tie rule
def test_accuracy_tie_rule_gives_fractional_credit_never_silent_home():
    # uniform: 3-way tie -> credit 1/3 whatever the truth is (a draw is NOT turned into a home win)
    for truth in (0, 1, 2):
        assert accuracy(UNIFORM, [truth]) == pytest.approx(1 / 3)
    two_way = np.array([[0.4, 0.4, 0.2]])
    assert accuracy(two_way, [0]) == pytest.approx(0.5) and accuracy(two_way, [1]) == pytest.approx(0.5)
    assert accuracy(two_way, [2]) == 0.0
    # no tie: plain top-1
    assert accuracy(np.array([[0.2, 0.5, 0.3]]), [1]) == 1.0
    # a draw prediction on a drawn match is credited even when home probability is lower
    assert accuracy(np.array([[0.3, 0.4, 0.3]]), [1]) == 1.0


# ---------------------------------------------------------------------------- ECE
def test_ece_known_values():
    conf = np.tile([0.9, 0.05, 0.05], (10, 1))
    y = [0] * 5 + [1] * 5  # right half of the time while claiming 90%
    assert ece(conf, y, bins=10) == pytest.approx(0.4)
    calibrated = np.tile([0.6, 0.2, 0.2], (10, 1))
    y2 = [0] * 6 + [1] * 2 + [2] * 2  # accuracy 0.6 == confidence 0.6
    assert ece(calibrated, y2, bins=10) == pytest.approx(0.0)


def test_ece_uses_the_configured_number_of_bins():
    rng = np.random.default_rng(0)
    p = rng.dirichlet([2, 2, 2], size=200)
    y = rng.integers(0, 3, 200)
    assert ece(p, y, bins=2) != ece(p, y, bins=20)
    assert 0 <= ece(p, y, bins=5) <= 1


# ---------------------------------------------------------------------- bootstrap
def test_bootstrap_is_deterministic_and_brackets_the_estimate():
    rng = np.random.default_rng(1)
    p = rng.dirichlet([3, 2, 2], size=300)
    y = rng.integers(0, 3, 300)
    names = ["log_loss", "brier", "rps", "accuracy", "ece"]
    a = bootstrap_ci(names, p, y, samples=200, seed=7)
    b = bootstrap_ci(names, p, y, samples=200, seed=7)
    assert a == b  # same seed, same intervals
    assert bootstrap_ci(names, p, y, samples=200, seed=8) != a
    point = compute_metrics(names, p, y, 10)
    for n in names:
        ci = a[n]
        assert ci["lower"] <= ci["mean"] <= ci["upper"] and ci["bootstrap_samples"] == 200
        assert ci["random_seed"] == 7 and ci["n"] == 300 and ci["metric"] == n
        if n != "ece":  # ECE is biased upward by resampling; the others bracket the point estimate
            assert ci["lower"] <= point[n] <= ci["upper"]


def test_more_data_narrows_the_interval():
    rng = np.random.default_rng(2)
    small_p, big_p = rng.dirichlet([2, 2, 2], size=40), rng.dirichlet([2, 2, 2], size=1600)
    small_y, big_y = rng.integers(0, 3, 40), rng.integers(0, 3, 1600)
    w = lambda r: r["log_loss"]["upper"] - r["log_loss"]["lower"]  # noqa: E731
    assert w(bootstrap_ci(["log_loss"], small_p, small_y, 200, 0)) > w(
        bootstrap_ci(["log_loss"], big_p, big_y, 200, 0)
    )


# ---------------------------------------------------------------------- validation
@pytest.mark.parametrize(
    "bad",
    [[[0.5, 0.5, 0.5]], [[1.2, -0.1, -0.1]], [[np.nan, 0.5, 0.5]], [[0.5, 0.5]]],
)
def test_invalid_probabilities_rejected(bad):
    with pytest.raises(ValueError):
        validate(np.array(bad))
    with pytest.raises(ValueError):
        log_loss(np.array(bad), [0])


def test_invalid_outcomes_empty_and_unknown_metric():
    with pytest.raises(ValueError):
        brier(UNIFORM, [3])
    with pytest.raises(ValueError):
        brier(UNIFORM, [0, 1])
    with pytest.raises(ValueError):
        log_loss(np.empty((0, 3)), [])
    with pytest.raises(KeyError):
        compute_metrics(["nope"], UNIFORM, [0])
