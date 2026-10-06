"""S9: temperature scaling."""

import numpy as np
import pytest

from src.evaluation.calibration import apply_temperature, fit_temperature
from src.evaluation.metrics import log_loss, validate


def test_apply_temperature_one_is_identity():
    p = np.array([[0.7, 0.2, 0.1], [0.3, 0.3, 0.4]])
    np.testing.assert_allclose(apply_temperature(p, 1.0), p, atol=1e-10)


def test_apply_temperature_above_one_softens_confident_predictions():
    p = np.array([[0.9, 0.05, 0.05]])
    softened = apply_temperature(p, 2.0)
    assert softened[0, 0] < 0.9  # less confident on the top class
    assert softened[0, 0] > softened[0, 1]  # still ranks the same class first


def test_apply_temperature_below_one_sharpens():
    p = np.array([[0.6, 0.3, 0.1]])
    sharpened = apply_temperature(p, 0.5)
    assert sharpened[0, 0] > 0.6


def test_apply_temperature_output_is_always_a_valid_distribution():
    rng = np.random.default_rng(0)
    for t in (0.1, 1.0, 5.0, 20.0):
        raw = rng.dirichlet([1, 1, 1], size=20)
        cal = apply_temperature(raw, t)
        validate(cal)  # raises if not a valid (n, 3) probability array


def test_apply_temperature_rejects_non_positive_t():
    with pytest.raises(ValueError):
        apply_temperature(np.array([[0.5, 0.3, 0.2]]), 0.0)
    with pytest.raises(ValueError):
        apply_temperature(np.array([[0.5, 0.3, 0.2]]), -1.0)


def test_fit_temperature_on_perfectly_calibrated_input_stays_near_one():
    rng = np.random.default_rng(1)
    p = rng.dirichlet([2, 2, 2], size=500)
    # sample outcomes FROM the model's own probabilities: already well-calibrated by construction
    y = np.array([rng.choice(3, p=row) for row in p])
    t = fit_temperature(p, y)
    assert 0.5 < t < 2.0  # not exactly 1.0 (finite-sample noise), but not wildly off either


def test_fit_temperature_on_overconfident_wrong_predictions_pushes_t_above_one():
    p = np.array([[0.95, 0.025, 0.025]] * 100)
    y = np.array([0] * 50 + [2] * 50)  # confidently wrong half the time
    t = fit_temperature(p, y)
    assert t > 1.0  # the fit must soften, not sharpen, an overconfident-and-wrong model


def test_fit_temperature_improves_or_matches_log_loss_on_the_fit_set():
    rng = np.random.default_rng(2)
    p = np.clip(rng.dirichlet([3, 3, 3], size=200), 1e-3, 1 - 1e-3)
    p = p / p.sum(axis=1, keepdims=True)
    y = rng.integers(0, 3, 200)
    t = fit_temperature(p, y)
    assert log_loss(apply_temperature(p, t), y) <= log_loss(p, y) + 1e-9


def test_fit_temperature_respects_custom_bounds():
    p = np.array([[0.9, 0.05, 0.05]] * 20)
    y = np.array([0] * 20)  # perfectly confident AND correct: wants T as small as possible
    t = fit_temperature(p, y, bounds=(1.0, 5.0))
    assert t == pytest.approx(1.0, abs=1e-4)  # clamped to the lower bound


# S9 edge cases (p=0/p=1, invalid probability, missing outcome) already covered for the
# underlying metrics by tests/test_metrics.py; calibration reuses metrics.validate via log_loss,
# so the same guarantees carry over -- verified here specifically for the calibration entry points.
def test_fit_temperature_rejects_invalid_probabilities():
    with pytest.raises(ValueError):
        fit_temperature(np.array([[1.2, -0.1, -0.1]]), [0])


def test_fit_temperature_rejects_missing_outcomes():
    with pytest.raises(ValueError):
        fit_temperature(np.empty((0, 3)), [])
