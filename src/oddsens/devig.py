"""Vig removal for decimal odds (ADR 0087). Pure functions, all return probabilities that sum to 1.

- `normalization`: proportional scaling of the implied probabilities (the project baseline, `src.odds.math.devig`).
- `power`: p_i = q_i ** k with k >= 1 chosen so the probabilities sum to 1 (more of the margin is taken from the long shots).
- `shin`: Shin (1993) insider-trading model; solves for the insider share z by bisection on the sum-to-one condition.
For two-way markets (over/under, BTTS) the same three functions work with two prices.
"""

import numpy as np

METHODS = ("normalization", "power", "shin")


def implied(odds) -> np.ndarray:
    o = np.asarray(odds, dtype=float)
    if o.ndim != 1 or len(o) < 2 or not np.all(np.isfinite(o)) or np.any(o <= 1.0):
        raise ValueError("decimal odds must be finite and greater than 1.0")
    return 1.0 / o


def overround(odds) -> float:
    return float(implied(odds).sum() - 1.0)


def normalization(odds) -> np.ndarray:
    q = implied(odds)
    return q / q.sum()


def power(odds) -> np.ndarray:
    q = implied(odds)
    if q.sum() <= 1.0:  # no margin (or negative): nothing to remove
        return q / q.sum()
    lo, hi = 1.0, 60.0
    for _ in range(200):
        k = (lo + hi) / 2
        if np.sum(q**k) > 1.0:
            lo = k
        else:
            hi = k
    p = q ** ((lo + hi) / 2)
    return p / p.sum()


def shin(odds) -> np.ndarray:
    q = implied(odds)
    s = q.sum()
    if s <= 1.0:
        return q / s

    def probs(z: float) -> np.ndarray:
        return (np.sqrt(z * z + 4.0 * (1.0 - z) * q * q / s) - z) / (2.0 * (1.0 - z))

    lo, hi = 0.0, 0.999
    for _ in range(200):
        z = (lo + hi) / 2
        if probs(z).sum() > 1.0:
            lo = z
        else:
            hi = z
    p = probs((lo + hi) / 2)
    return p / p.sum()


def remove_vig(odds, method: str) -> np.ndarray:
    if method not in METHODS:
        raise ValueError(f"unknown method {method!r}; use one of {METHODS}")
    return {"normalization": normalization, "power": power, "shin": shin}[method](odds)
