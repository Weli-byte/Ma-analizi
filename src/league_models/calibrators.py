"""Sprint 5 (ADR 0067): probability calibrators and their walk-forward selection.

Every method works in log-odds space so a calibrated 1X2 row stays on the simplex and a calibrated binary probability stays inside (0, 1):

    1X2     log q_k = a * log p_k + b_k + d[league, k]     (k = H, D, A; b_H = d_H = 0)
    binary  logit q = a * logit p + b + d[league]

none         identity (the explicit fallback of every failed fit)
temperature  a only (b = d = 0)
affine       a and b (global)
hier         a, b and a league offset d with a ridge penalty (a small league is pulled to the global calibration: the hierarchical calibrator
             that replaces a per-league isotonic fit on little data)
isotonic     binary only, global, monotone step function (needs a minimum sample; otherwise NOT_FITTED and the identity is used)

A fit uses ONLY the calibration rows it is given; selection between methods is an inner walk-forward over chronological folds
(fit on the folds before k, score fold k). A degenerate fit (one class, too few rows, non-finite parameters) never raises into the
caller's pipeline: it is recorded as NOT_FITTED_* and applies the identity, visibly."""

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import minimize

EPS = 1e-6
METHODS_1X2 = ("none", "temperature", "affine", "hier")
METHODS_BINARY = ("none", "temperature", "affine", "hier", "isotonic")
FITTED = "FITTED"


class CalibrationError(ValueError):
    """Malformed probabilities (non-finite, out of bounds, not on the simplex) or an unusable calibrator."""


def check_probs(p: np.ndarray, target: str, atol: float = 1e-6) -> np.ndarray:
    p = np.asarray(p, dtype=float)
    if not np.isfinite(p).all():
        raise CalibrationError("non-finite probability")
    if target == "1x2":
        if p.ndim != 2 or p.shape[1] != 3:
            raise CalibrationError("1X2 probabilities must have shape (n, 3)")
        if (p < -atol).any() or (p > 1 + atol).any() or not np.allclose(p.sum(axis=1), 1.0, atol=atol):
            raise CalibrationError("1X2 rows must lie on the probability simplex")
    else:
        if p.ndim != 1 or (p < -atol).any() or (p > 1 + atol).any():
            raise CalibrationError("binary probabilities must lie in [0, 1]")
    return p


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, EPS, 1 - EPS)
    return np.log(p / (1 - p))


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -40, 40)))


def _log_softmax(z: np.ndarray) -> np.ndarray:
    z = z - z.max(axis=1, keepdims=True)
    return z - np.log(np.exp(z).sum(axis=1, keepdims=True))


@dataclass
class Calibrator:
    method: str
    target: str
    status: str = FITTED
    reason: str = ""
    a: float = 1.0
    b: np.ndarray = field(default_factory=lambda: np.zeros(3))
    league_index: dict[str, int] = field(default_factory=dict)
    d: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    iso_x: np.ndarray = field(default_factory=lambda: np.zeros(0))
    iso_y: np.ndarray = field(default_factory=lambda: np.zeros(0))
    n_fit: int = 0

    @property
    def effective_method(self) -> str:
        return self.method if self.status == FITTED else "none"

    def _offsets(self, leagues) -> np.ndarray:
        k = self.d.shape[1]
        out = np.zeros((len(leagues), k))
        for i, lg in enumerate(leagues):
            j = self.league_index.get(lg)
            if j is not None:
                out[i] = self.d[j]
        return out

    def apply(self, p: np.ndarray, leagues) -> np.ndarray:
        p = check_probs(p, self.target)
        if self.effective_method == "none":
            return p.copy()
        if self.target == "1x2":
            z = self.a * np.log(np.clip(p, EPS, 1.0)) + self.b
            if self.effective_method == "hier":
                z = z + self._offsets(leagues)
            q = np.exp(_log_softmax(z))
        elif self.effective_method == "isotonic":
            q = np.interp(np.clip(p, 0, 1), self.iso_x, self.iso_y)
        else:
            z = self.a * _logit(p) + float(self.b[0])
            if self.effective_method == "hier":
                z = z + self._offsets(leagues)[:, 0]
            q = _sigmoid(z)
        q = np.clip(q, EPS, 1 - EPS) if self.target != "1x2" else q / q.sum(axis=1, keepdims=True)
        return check_probs(q, self.target)

    def as_dict(self) -> dict:
        return {"method": self.method, "effective_method": self.effective_method, "target": self.target, "status": self.status, "reason": self.reason,
                "slope_a": round(float(self.a), 6), "bias_b": [round(float(x), 6) for x in self.b], "n_fit": self.n_fit,
                "league_offsets": {lg: [round(float(x), 6) for x in self.d[j]] for lg, j in sorted(self.league_index.items())} if self.d.size else {},
                "isotonic_knots": int(len(self.iso_x))}  # fmt: skip


def _nll(target: str, z_or_p, y: np.ndarray) -> float:
    if target == "1x2":
        return float(-np.mean(_log_softmax(z_or_p)[np.arange(len(y)), y]))
    q = np.clip(_sigmoid(z_or_p), EPS, 1 - EPS)
    return float(-np.mean(y * np.log(q) + (1 - y) * np.log(1 - q)))


def fit_calibrator(method: str, target: str, p: np.ndarray, y: np.ndarray, leagues, *, ridge: float = 50.0, min_isotonic: int = 500, min_rows: int = 100) -> Calibrator:
    """Fit on the calibration rows only. `ridge` is the penalty on league offsets (in units of rows: a league with far fewer rows than `ridge`
    keeps an offset near 0)."""
    valid = METHODS_1X2 if target == "1x2" else METHODS_BINARY
    if method not in valid:
        raise CalibrationError(f"method {method!r} is not valid for {target}")
    p = check_probs(p, target)
    y = np.asarray(y, dtype=int)
    cal = Calibrator(method, target, n_fit=int(len(y)))
    if method == "none":
        return cal
    k = 3 if target == "1x2" else 2
    counts = np.bincount(y, minlength=k) if len(y) else np.zeros(k, int)
    if len(y) < min_rows:
        return Calibrator(method, target, "NOT_FITTED_TOO_FEW_ROWS", f"{len(y)} rows < {min_rows}", n_fit=int(len(y)))
    if (counts == 0).any():
        return Calibrator(method, target, "NOT_FITTED_ONE_CLASS", f"class counts {counts.tolist()}", n_fit=int(len(y)))
    if method == "isotonic":
        if len(y) < min_isotonic:
            return Calibrator(method, target, "NOT_FITTED_TOO_FEW_ROWS", f"{len(y)} rows < {min_isotonic} needed for isotonic", n_fit=int(len(y)))
        from sklearn.isotonic import IsotonicRegression

        iso = IsotonicRegression(y_min=EPS, y_max=1 - EPS, out_of_bounds="clip").fit(p, y)
        cal.iso_x, cal.iso_y = np.asarray(iso.X_thresholds_, float), np.asarray(iso.y_thresholds_, float)
        return cal
    feats = np.log(np.clip(p, EPS, 1.0)) if target == "1x2" else _logit(p)
    if method == "hier":
        names = sorted(set(leagues))
        cal.league_index = {lg: i for i, lg in enumerate(names)}
        li = np.array([cal.league_index[lg] for lg in leagues])
    nb = 2 if target == "1x2" else 1
    nbias = nb if method in ("affine", "hier") else 0
    n_league = len(cal.league_index) if method == "hier" else 0

    def unpack(theta):
        return float(np.exp(theta[0])), theta[1 : 1 + nbias], theta[1 + nbias :].reshape(n_league, nb)

    def objective(theta):
        a, bias, d = unpack(theta)
        z = a * feats
        if target == "1x2":
            z = z.copy()
            if nbias:
                z[:, 1:] += bias
            if n_league:
                z[:, 1:] += d[li]
        else:
            if nbias:
                z = z + bias[0]
            if n_league:
                z = z + d[li, 0]
        pen = ridge * float(np.sum(d**2)) / max(len(y), 1) if n_league else 0.0
        return _nll(target, z, y) + pen

    res = minimize(objective, np.zeros(1 + nbias + n_league * nb), method="L-BFGS-B", options={"maxiter": 500})
    if not np.isfinite(res.fun) or not np.isfinite(res.x).all():
        return Calibrator(method, target, "NOT_FITTED_NONFINITE", "optimizer returned a non-finite value", n_fit=int(len(y)))
    a, bias, d = unpack(res.x)
    cal.a = a
    if target == "1x2":
        cal.b = np.concatenate([[0.0], bias if nbias else np.zeros(2)])
        cal.d = np.concatenate([np.zeros((n_league, 1)), d], axis=1) if n_league else np.zeros((0, 3))
    else:
        cal.b = np.array([bias[0]]) if nbias else np.zeros(1)
        cal.d = d if n_league else np.zeros((0, 1))
    return cal


def select_method(folds_p: list[np.ndarray], folds_y: list[np.ndarray], folds_leagues: list, target: str, *, first_eval_fold: int, margin: float, methods=None, **fit_kw) -> dict:
    """Inner walk-forward: for k >= first_eval_fold, fit each method on folds 0..k-1 and score fold k (log loss). Choose the lowest mean log loss, but only
    leave `none` when the gain is at least `margin`. ECE is reported by the caller, never used to select."""
    methods = methods or (METHODS_1X2 if target == "1x2" else METHODS_BINARY)
    scores: dict[str, list[float]] = {m: [] for m in methods}
    statuses: dict[str, set] = {m: set() for m in methods}
    for k in range(first_eval_fold, len(folds_p)):
        p_tr, y_tr = np.concatenate(folds_p[:k]), np.concatenate(folds_y[:k])
        lg_tr = [lg for fl in folds_leagues[:k] for lg in fl]
        y_ev = np.asarray(folds_y[k], dtype=int)
        for m in methods:
            cal = fit_calibrator(m, target, p_tr, y_tr, lg_tr, **fit_kw)
            statuses[m].add(cal.status)
            q = cal.apply(folds_p[k], folds_leagues[k])
            scores[m].append(_nll_probs(target, q, y_ev))
    mean = {m: float(np.mean(v)) for m, v in scores.items()}
    best = min(mean, key=lambda m: (mean[m], methods.index(m)))
    chosen = best if mean["none"] - mean[best] >= margin else "none"
    return {"walk_forward_log_loss_by_fold": {m: [round(x, 6) for x in v] for m, v in scores.items()}, "mean_log_loss": {m: round(v, 6) for m, v in mean.items()},
            "fit_statuses": {m: sorted(s) for m, s in statuses.items()}, "best_method": best, "chosen": chosen, "margin": margin,
            "reason": "none unless a method gains at least the margin in mean walk-forward log loss; ECE is reported, not used to select"}  # fmt: skip


def _nll_probs(target: str, q: np.ndarray, y: np.ndarray) -> float:
    if target == "1x2":
        return float(-np.mean(np.log(np.clip(q[np.arange(len(y)), y], 1e-15, 1.0))))
    q = np.clip(q, 1e-15, 1 - 1e-15)
    return float(-np.mean(y * np.log(q) + (1 - y) * np.log(1 - q)))
