"""S9: temperature scaling (Guo et al. 2017), the simplest standard multiclass calibration
method -- a single scalar `T` that sharpens (`T<1`) or softens (`T>1`) a model's existing
probabilities without changing the argmax, fit by minimizing log loss.

Protocol (ADR 0020): `T` must be fit on a set DISJOINT from the one calibrated metrics are
reported on, or "calibrated" quality is just overfit to the reporting set. This module is
policy-free about WHICH rows go where -- `fit_temperature`/`apply_temperature` operate on
whatever `(probs, outcomes)` arrays the caller passes. `run_baselines.py` documents its own
split choice (first half of validation seasons fits T, second half reports both raw and
calibrated metrics) in the same ADR.
"""

import numpy as np
from scipy.optimize import minimize_scalar

from .metrics import EPS, _prep, log_loss

T_BOUNDS = (0.05, 20.0)  # T<=0 is undefined (log(p)/T blows up); 20 is already near-uniform


def _scale(probs: np.ndarray, t: float) -> np.ndarray:
    """p_i^(1/T), renormalized -- equivalent to temperature-scaling the pre-softmax logits when
    probs already come from a softmax, and well-defined for any valid probability vector."""
    logp = np.log(np.clip(probs, EPS, 1.0)) / t
    unnorm = np.exp(logp - logp.max(axis=1, keepdims=True))  # subtract max: overflow-safe
    return unnorm / unnorm.sum(axis=1, keepdims=True)


def fit_temperature(probs, outcomes, bounds: tuple[float, float] = T_BOUNDS) -> float:
    """Minimizes log loss over T on the given (probs, outcomes). Raises on the same invalid
    input `metrics.validate` already rejects (NaN/inf, wrong shape, bad outcomes)."""
    p, y = _prep(probs, outcomes)

    def objective(t: float) -> float:
        return log_loss(_scale(p, t), y)

    result = minimize_scalar(objective, bounds=bounds, method="bounded")
    return float(result.x)


def apply_temperature(probs, t: float) -> np.ndarray:
    p = np.asarray(probs, dtype=float)
    if t <= 0:
        raise ValueError(f"T must be > 0, got {t}")
    return _scale(p, t)
