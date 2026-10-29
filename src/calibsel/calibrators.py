"""Probability calibrators (ADR 0089). Each is fitted on (probabilities, outcomes) and applied to new probabilities; none looks at the data it is later scored on.

Multiclass (1X2): `identity`, `temperature` (one scalar on the log-probabilities), `vector` (per-class scale and bias), `dirichlet` (full matrix on the log-probabilities, L2 towards the identity).
Binary (over/under, BTTS): `identity`, `temperature` (one scalar on the logit), `platt` (scale and bias on the logit), `isotonic` (monotone step function, needs enough rows).
Regularisation constants are fixed here, before any data is looked at. Fitting is deterministic (no random numbers); a calibrator exposes `describe()` for the registry.
"""

import hashlib
import json
from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize, minimize_scalar
from sklearn.isotonic import IsotonicRegression

EPS = 1e-9
L2_VECTOR = 1e-3
L2_DIRICHLET = 1e-2
MIN_ISOTONIC_ROWS = 400
MULTICLASS = ("identity", "temperature", "vector", "dirichlet")
BINARY = ("identity", "temperature", "platt", "isotonic")


def _softmax(z: np.ndarray) -> np.ndarray:
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def _logp(p: np.ndarray) -> np.ndarray:
    return np.log(np.clip(p, EPS, 1.0))


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, EPS, 1 - EPS)
    return np.log(p / (1 - p))


def _sigmoid(t: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-t))


@dataclass
class Calibrator:
    kind: str
    target: str
    params: dict
    n_fit: int
    _model: object = None

    def apply(self, p: np.ndarray) -> np.ndarray:
        p = np.asarray(p, dtype=float)
        k = self.kind
        if k == "identity":
            return p.copy()
        if self.target == "1x2":
            z = _logp(p)
            if k == "temperature":
                return _softmax(z / self.params["T"])
            b = np.asarray(self.params["b"])
            if k == "vector":
                return _softmax(z * np.asarray(self.params["a"]) + b)
            return _softmax(z @ np.asarray(self.params["W"]).T + b)  # dirichlet
        if k == "temperature":
            return _sigmoid(_logit(p) / self.params["T"])
        if k == "platt":
            return _sigmoid(self.params["a"] * _logit(p) + self.params["b"])
        return np.clip(self._model.predict(p), 1e-3, 1 - 1e-3)  # isotonic

    def describe(self) -> dict:
        d = {"kind": self.kind, "target": self.target, "n_fit": self.n_fit, "params": json.loads(json.dumps(self.params, default=float))}
        d["checksum"] = hashlib.sha256(json.dumps(d, sort_keys=True).encode("utf-8")).hexdigest()
        return d


def _nll_multi(q: np.ndarray, y: np.ndarray) -> float:
    return float(-np.log(np.clip(q[np.arange(len(y)), y], EPS, 1.0)).mean())


def fit_multiclass(kind: str, p: np.ndarray, y: np.ndarray) -> Calibrator:
    if kind not in MULTICLASS:
        raise ValueError(f"unknown multiclass calibrator {kind!r}")
    n = len(y)
    if kind == "identity":
        return Calibrator(kind, "1x2", {}, n)
    z = _logp(p)
    if kind == "temperature":
        r = minimize_scalar(lambda lt: _nll_multi(_softmax(z / np.exp(lt)), y), bounds=(-1.6, 1.6), method="bounded")
        return Calibrator(kind, "1x2", {"T": float(np.exp(r.x))}, n)
    if kind == "vector":
        def f(v):
            a, b = v[:3], v[3:]
            return _nll_multi(_softmax(z * a + b), y) + L2_VECTOR * float(((a - 1) ** 2).sum() + (b**2).sum())

        r = minimize(f, np.concatenate([np.ones(3), np.zeros(3)]), method="L-BFGS-B")
        return Calibrator(kind, "1x2", {"a": r.x[:3].tolist(), "b": r.x[3:].tolist()}, n)

    def g(v):
        w, b = v[:9].reshape(3, 3), v[9:]
        return _nll_multi(_softmax(z @ w.T + b), y) + L2_DIRICHLET * float(((w - np.eye(3)) ** 2).sum() + (b**2).sum())

    r = minimize(g, np.concatenate([np.eye(3).ravel(), np.zeros(3)]), method="L-BFGS-B")
    return Calibrator("dirichlet", "1x2", {"W": r.x[:9].reshape(3, 3).tolist(), "b": r.x[9:].tolist()}, n)


def fit_binary(kind: str, p: np.ndarray, y: np.ndarray, target: str) -> Calibrator:
    if kind not in BINARY:
        raise ValueError(f"unknown binary calibrator {kind!r}")
    n = len(y)
    y = np.asarray(y, dtype=float)
    if kind == "identity":
        return Calibrator(kind, target, {}, n)
    t = _logit(p)

    def nll(q: np.ndarray) -> float:
        q = np.clip(q, EPS, 1 - EPS)
        return float(-(y * np.log(q) + (1 - y) * np.log(1 - q)).mean())

    if kind == "temperature":
        r = minimize_scalar(lambda lt: nll(_sigmoid(t / np.exp(lt))), bounds=(-1.6, 1.6), method="bounded")
        return Calibrator(kind, target, {"T": float(np.exp(r.x))}, n)
    if kind == "platt":
        r = minimize(lambda v: nll(_sigmoid(v[0] * t + v[1])) + L2_VECTOR * ((v[0] - 1) ** 2 + v[1] ** 2), np.array([1.0, 0.0]), method="L-BFGS-B")
        return Calibrator(kind, target, {"a": float(r.x[0]), "b": float(r.x[1])}, n)
    if n < MIN_ISOTONIC_ROWS:
        raise ValueError(f"isotonic needs at least {MIN_ISOTONIC_ROWS} rows, got {n}")
    iso = IsotonicRegression(out_of_bounds="clip", y_min=1e-3, y_max=1 - 1e-3).fit(p, y)
    return Calibrator(kind, target, {"knots": int(len(iso.X_thresholds_))}, n, iso)


def fit(kind: str, target: str, p: np.ndarray, y: np.ndarray) -> Calibrator:
    return fit_multiclass(kind, p, y) if target == "1x2" else fit_binary(kind, p, y, target)


def candidates(target: str) -> tuple[str, ...]:
    return MULTICLASS if target == "1x2" else BINARY
