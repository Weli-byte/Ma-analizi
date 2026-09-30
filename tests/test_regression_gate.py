"""S0-S7 hardening Phase 42 (audit finding M-19): the composed regression gate."""

import numpy as np

from src.evaluation.regression_gate import run_regression_gate
from tests.test_features import synthetic_season


def test_gate_passes_when_all_checks_clean():
    probs = np.array([[0.5, 0.3, 0.2], [0.2, 0.3, 0.5]])
    outcomes = np.array([0, 2])
    result = run_regression_gate(
        probs=probs,
        outcomes=outcomes,
        leakage_matches=synthetic_season(n_rounds=6),
        leakage_samples=30,
        current_feature_names=["a", "b"],
        expected_feature_names=["a", "b"],
    )
    assert result.passed


def test_gate_catches_invalid_probabilities():
    bad = np.array([[0.5, 0.3, 0.3]])  # sums to 1.1
    result = run_regression_gate(probs=bad, outcomes=np.array([0]))
    assert not result.passed
    assert result.probability_errors and not result.leakage_violations and not result.missing_features


def test_gate_catches_leaking_feature_fn():
    def leaky_fn(fx, history, cutoff):
        from src.features.compute import compute_features

        r = compute_features(fx, history, cutoff)
        r.values["cheat"] = float(fx.home_goals - fx.away_goals)  # peeks at this match's own result
        return r

    result = run_regression_gate(
        leakage_matches=synthetic_season(n_rounds=6),
        leakage_samples=40,
        leakage_seed=5,
        leakage_feature_fn=leaky_fn,
    )
    assert not result.passed
    assert result.leakage_violations and not result.probability_errors and not result.missing_features


def test_gate_catches_missing_features():
    result = run_regression_gate(
        current_feature_names=["a"],
        expected_feature_names=["a", "b", "c"],
    )
    assert not result.passed
    assert result.missing_features == ("b", "c")


def test_gate_skips_checks_whose_inputs_are_not_provided():
    result = run_regression_gate()  # nothing supplied -> vacuously passes, nothing was skipped silently
    assert result.passed
