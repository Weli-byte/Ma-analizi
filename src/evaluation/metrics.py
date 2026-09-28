"""Proper scoring rules for 1X2. Probabilities: (n, 3) array, columns [home, draw, away].
Outcomes: int array, 0 = home, 1 = draw, 2 = away (ordered for RPS).

Tie rule (ADR 0012 / docs/benchmark_protocol.md): when several classes share the maximum
probability, accuracy gives fractional credit 1/k if the true class is among the k tied classes
(the expected accuracy under uniformly random tie-breaking). A draw is never silently turned into
a home win.
"""

import numpy as np

EPS = 1e-15  # log-loss clip so p=0 on the realised outcome stays finite
TOL = 1e-6
TIE_TOL = 1e-12


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
    return float(np.mean(np.sum((p - np.eye(3)[y]) ** 2, axis=1)))


def rps(probs, outcomes) -> float:
    """Ranked Probability Score for ordered outcomes H < D < A (range 0..1)."""
    p, y = _prep(probs, outcomes)
    cum_p = np.cumsum(p, axis=1)[:, :2]
    cum_o = np.cumsum(np.eye(3)[y], axis=1)[:, :2]
    return float(np.mean(np.sum((cum_p - cum_o) ** 2, axis=1) / 2.0))


def _credit(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Per-sample correctness with the explicit tie rule (see module docstring)."""
    top = p.max(axis=1, keepdims=True)
    tied = p >= top - TIE_TOL
    k = tied.sum(axis=1)
    return tied[np.arange(len(y)), y] / k


def accuracy(probs, outcomes) -> float:
    """Top-1 accuracy with fractional credit on exact ties (secondary metric only)."""
    p, y = _prep(probs, outcomes)
    return float(np.mean(_credit(p, y)))


def ece(probs, outcomes, bins: int = 10) -> float:
    """Top-label Expected Calibration Error with equal-width confidence bins on [1/3, 1]."""
    p, y = _prep(probs, outcomes)
    conf = p.max(axis=1)
    correct = _credit(p, y)
    edges = np.linspace(1 / 3, 1.0, bins + 1)
    idx = np.clip(np.digitize(conf, edges[1:-1]), 0, bins - 1)
    total = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            total += m.mean() * abs(correct[m].mean() - conf[m].mean())
    return float(total)


METRICS = {"log_loss": log_loss, "brier": brier, "rps": rps, "accuracy": accuracy, "ece": ece}


def compute_metrics(names: list[str], probs, outcomes, bins: int = 10) -> dict[str, float]:
    out = {}
    for name in names:
        if name not in METRICS:
            raise KeyError(f"unknown metric {name!r}")
        out[name] = ece(probs, outcomes, bins) if name == "ece" else METRICS[name](probs, outcomes)
    return out


def bootstrap_ci(
    names: list[str],
    probs,
    outcomes,
    samples: int,
    seed: int,
    bins: int = 10,
    alpha: float = 0.05,
) -> dict[str, dict]:
    """Percentile bootstrap over fixtures. Deterministic for a given seed. Not a significance test."""
    p, y = _prep(probs, outcomes)
    rng = np.random.default_rng(seed)
    n = len(y)
    draws: dict[str, list[float]] = {m: [] for m in names}
    for _ in range(samples):
        idx = rng.integers(0, n, n)
        vals = compute_metrics(names, p[idx], y[idx], bins)
        for m in names:
            draws[m].append(vals[m])
    out = {}
    for m in names:
        a = np.asarray(draws[m])
        out[m] = {
            "metric": m,
            "mean": float(a.mean()),
            "lower": float(np.quantile(a, alpha / 2)),
            "upper": float(np.quantile(a, 1 - alpha / 2)),
            "bootstrap_samples": samples,
            "random_seed": seed,
            "n": n,
        }
    return out
