"""S11: OOF ensemble building blocks."""

import numpy as np
import pytest

from src.evaluation.ensemble import (
    build_meta_features,
    fit_lightgbm_stacking,
    fit_logistic_stacking,
    fit_validation_weights,
    predict_lightgbm_stacking,
    predict_logistic_stacking,
    simple_mean,
    weighted_average,
)
from src.evaluation.metrics import log_loss, validate


def dirichlet(n, seed, alpha=(2, 2, 2)):
    return np.random.default_rng(seed).dirichlet(alpha, size=n)


# ------------------------------------------------------------------------------- simple_mean
def test_simple_mean_is_the_elementwise_average():
    p1 = np.array([[1.0, 0.0, 0.0]])
    p2 = np.array([[0.0, 1.0, 0.0]])
    m = simple_mean([p1, p2])
    np.testing.assert_allclose(m, [[0.5, 0.5, 0.0]])


def test_simple_mean_renormalizes_and_stays_valid():
    probs = [dirichlet(50, 0), dirichlet(50, 1), dirichlet(50, 2)]
    m = simple_mean(probs)
    validate(m)


def test_simple_mean_ignores_nan_rows_from_an_abstaining_model():
    p1 = np.array([[0.6, 0.3, 0.1]])
    p2 = np.array([[np.nan, np.nan, np.nan]])  # this model abstained on this fixture
    m = simple_mean([p1, p2])
    np.testing.assert_allclose(m, p1)


def test_simple_mean_rejects_a_row_no_model_predicts():
    p1 = np.array([[np.nan, np.nan, np.nan]])
    p2 = np.array([[np.nan, np.nan, np.nan]])
    with pytest.raises(ValueError):
        simple_mean([p1, p2])


# ------------------------------------------------------------------- validation-weighted
def test_fit_validation_weights_favors_the_better_model():
    y = np.array([0] * 50)
    good = np.array([[0.9, 0.05, 0.05]] * 50)
    bad = np.array([[0.4, 0.3, 0.3]] * 50)
    w = fit_validation_weights([good, bad], y)
    assert w.sum() == pytest.approx(1.0)
    assert w[0] > w[1]


def test_weighted_average_matches_simple_mean_at_equal_weights():
    probs = [dirichlet(30, 0), dirichlet(30, 1)]
    np.testing.assert_allclose(weighted_average(probs, [0.5, 0.5]), simple_mean(probs), atol=1e-10)


def test_weighted_average_rejects_weights_that_do_not_sum_to_one():
    probs = [dirichlet(10, 0), dirichlet(10, 1)]
    with pytest.raises(ValueError):
        weighted_average(probs, [0.5, 0.6])


def test_weighted_average_rejects_wrong_length():
    probs = [dirichlet(10, 0), dirichlet(10, 1)]
    with pytest.raises(ValueError):
        weighted_average(probs, [1.0])


# --------------------------------------------------------------------------- meta features
def test_build_meta_features_shape_without_confidence():
    probs = [dirichlet(20, 0), dirichlet(20, 1), dirichlet(20, 2)]
    X = build_meta_features(probs)
    assert X.shape == (20, 9)


def test_build_meta_features_shape_with_confidence():
    probs = [dirichlet(20, 0), dirichlet(20, 1)]
    conf = [np.full(20, 0.8), np.full(20, 0.6)]
    X = build_meta_features(probs, conf)
    assert X.shape == (20, 8)
    np.testing.assert_allclose(X[:, 6], 0.8)
    np.testing.assert_allclose(X[:, 7], 0.6)


# -------------------------------------------------------------------------- logistic stacking
def test_logistic_stacking_output_is_a_valid_distribution():
    probs = [dirichlet(300, 0), dirichlet(300, 1)]
    y = np.random.default_rng(2).integers(0, 3, 300)
    X = build_meta_features(probs)
    model = fit_logistic_stacking(X, y)
    pred = predict_logistic_stacking(model, X)
    validate(pred)
    assert pred.shape == (300, 3)


def test_logistic_stacking_learns_a_strong_signal():
    """One base model IS the true label (one-hot) -- the meta-model should learn to trust it."""
    rng = np.random.default_rng(3)
    y = rng.integers(0, 3, 400)
    perfect = np.eye(3)[y]
    noisy = dirichlet(400, 4)
    X = build_meta_features([perfect, noisy])
    model = fit_logistic_stacking(X, y)
    pred = predict_logistic_stacking(model, X)
    assert log_loss(pred, y) < log_loss(noisy, y)


# -------------------------------------------------------------------------- lightgbm stacking
def test_lightgbm_stacking_output_is_a_valid_distribution():
    probs = [dirichlet(300, 0), dirichlet(300, 1)]
    y = np.random.default_rng(5).integers(0, 3, 300)
    X = build_meta_features(probs)
    booster = fit_lightgbm_stacking(X, y, num_leaves=7, min_data_in_leaf=5, seed=0)
    pred = predict_lightgbm_stacking(booster, X)
    validate(pred)
    assert pred.shape == (300, 3)


def test_lightgbm_stacking_is_deterministic_given_a_seed():
    probs = [dirichlet(200, 0), dirichlet(200, 1)]
    y = np.random.default_rng(6).integers(0, 3, 200)
    X = build_meta_features(probs)
    b1 = fit_lightgbm_stacking(X, y, num_leaves=7, min_data_in_leaf=5, seed=0, deterministic=True)
    b2 = fit_lightgbm_stacking(X, y, num_leaves=7, min_data_in_leaf=5, seed=0, deterministic=True)
    np.testing.assert_allclose(predict_lightgbm_stacking(b1, X), predict_lightgbm_stacking(b2, X))
