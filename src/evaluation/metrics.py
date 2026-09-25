"""Proper scoring rules for 1X2. Probabilities: (n, 3) array, columns [home, draw, away].
Outcomes: int array, 0 = home, 1 = draw, 2 = away (ordered for RPS).
"""

import numpy as np

EPS = 1e-15  # log-loss clip so p=0 on the realised outcome stays finite
TOL = 1e-6


def validate(probs: np.ndarray, outcomes: np.ndarray | None = None) -> None:
    p = np.asarray(probs, dtype=float)
    if p.ndim != 2 or p.shape[1] != 3:
        raise ValueError(f"probs must have shape (n, 3), got {p.shape}")
    if not np.isfinite(p).all():
        raise ValueError("probabilities contain NaN/inf")
    if (p < 0).any() or (p > 1).any():
        raise ValueError("probabilities must lie in [0, 1]")
    if not np.allclose(p.sum(axis=1), 1.0, atol=TOL):
        raise ValueError("each probability row must sum to 1")
    if outcomes is not None:
        y = np.asarray(outcomes)
        if len(y) != len(p):
            raise ValueError("probs and outcomes differ in length")
        if len(y) and (y.min() < 0 or y.max() > 2):
            raise ValueError("outcomes must be 0 (home), 1 (draw) or 2 (away)")


def _prep(probs, outcomes) -> tuple[np.ndarray, np.ndarray]:
    p = np.asarray(probs, dtype=float)
    y = np.asarray(outcomes, dtype=int)
    validate(p, y)
    if len(y) == 0:
        raise ValueError("no samples")
    return p, y


def log_loss(probs, outcomes) -> float:
    p, y = _prep(probs, outcomes)
    return float(-np.mean(np.log(np.clip(p[np.arange(len(y)), y], EPS, 1.0))))


def brier(probs, outcomes) -> float:
    """Multiclass Brier: mean over samples of sum_k (p_k - o_k)^2 (range 0..2)."""
    p, y = _prep(probs, outcomes)
    onehot = np.eye(3)[y]
    return float(np.mean(np.sum((p - onehot) ** 2, axis=1)))


def rps(probs, outcomes) -> float:
    """Ranked Probability Score for ordered outcomes H < D < A (range 0..1)."""
    p, y = _prep(probs, outcomes)
    cum_p = np.cumsum(p, axis=1)[:, :2]
    cum_o = np.cumsum(np.eye(3)[y], axis=1)[:, :2]
    return float(np.mean(np.sum((cum_p - cum_o) ** 2, axis=1) / 2.0))


def accuracy(probs, outcomes) -> float:
    """Top-1 accuracy (secondary metric only). Ties resolve to the first class (home)."""
    p, y = _prep(probs, outcomes)
    return float(np.mean(np.argmax(p, axis=1) == y))


METRICS = {"log_loss": log_loss, "brier": brier, "rps": rps, "accuracy": accuracy}


def all_metrics(probs, outcomes) -> dict[str, float]:
    return {name: fn(probs, outcomes) for name, fn in METRICS.items()}
