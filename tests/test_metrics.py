import math

import numpy as np
import pytest

from src.evaluation.metrics import EPS, accuracy, all_metrics, brier, log_loss, rps, validate

UNIFORM = np.full((1, 3), 1 / 3)


def test_uniform_home_win_known_values():
    y = [0]
    assert log_loss(UNIFORM, y) == pytest.approx(math.log(3))
    assert brier(UNIFORM, y) == pytest.approx(2 / 3)  # (2/3)^2 + 2*(1/3)^2
    assert rps(UNIFORM, y) == pytest.approx(5 / 18)  # ((2/3)^2 + (1/3)^2) / 2
    assert accuracy(UNIFORM, y) == 1.0  # tie -> first class (home)


def test_perfect_prediction():
    p = np.array([[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]])
    m = all_metrics(p, [0, 1, 2])
    assert m["brier"] == 0 and m["rps"] == 0 and m["accuracy"] == 1
    assert m["log_loss"] == pytest.approx(0, abs=1e-12)


def test_worst_prediction_edge_cases():
    p = np.array([[0.0, 0.0, 1.0]])
    assert log_loss(p, [0]) == pytest.approx(-math.log(EPS))  # p=0 clipped, finite
    assert brier(p, [0]) == pytest.approx(2.0)
    assert rps(p, [0]) == pytest.approx(1.0)
    assert accuracy(p, [0]) == 0.0


def test_rps_respects_ordering():
    # truth = away. Predicting draw is "closer" than predicting home.
    assert rps([[0, 1.0, 0]], [2]) < rps([[1.0, 0, 0]], [2])
    # Brier does not care about order
    assert brier([[0, 1.0, 0]], [2]) == brier([[1.0, 0, 0]], [2])


def test_metrics_are_means_over_samples():
    p = np.array([[0.5, 0.3, 0.2], [0.2, 0.3, 0.5]])
    y = [0, 2]
    assert log_loss(p, y) == pytest.approx(-math.log(0.5))
    assert brier(p, y) == pytest.approx(0.25 + 0.09 + 0.04)


@pytest.mark.parametrize(
    "bad",
    [
        [[0.5, 0.5, 0.5]],  # sum != 1
        [[1.2, -0.1, -0.1]],  # out of range
        [[np.nan, 0.5, 0.5]],  # NaN
        [[0.5, 0.5]],  # wrong shape
    ],
)
def test_invalid_probabilities_rejected(bad):
    with pytest.raises(ValueError):
        validate(np.array(bad))
    with pytest.raises(ValueError):
        log_loss(np.array(bad), [0])


def test_invalid_outcomes_and_empty():
    with pytest.raises(ValueError):
        brier(UNIFORM, [3])
    with pytest.raises(ValueError):
        brier(UNIFORM, [0, 1])  # length mismatch
    with pytest.raises(ValueError):
        log_loss(np.empty((0, 3)), [])
