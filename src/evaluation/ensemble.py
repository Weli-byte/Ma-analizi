"""S11: combine base models' out-of-fold (OOF) probabilities into a learned ensemble.

Policy-free, like `calibration.py`: every function here takes already-aligned arrays. The
orchestrator (`run_ensemble.py`) owns loading walk-forward's OOF predictions, aligning them by
fixture, and the fit/report chronological split (ADR 0022) -- exactly the same separation of
concerns S9 established for calibration.

Meta input per sample (ADR 0022): each base model's `[p_home, p_draw, p_away]`, concatenated in
a FIXED model order, optionally followed by each model's confidence (LLM-sourced predictions
only; classical models have none, padded with 1.0 = "fully confident in its own estimate" since
they always commit to a single, unhedged distribution).
"""

import numpy as np
from sklearn.linear_model import LogisticRegression

from .metrics import EPS, log_loss, validate

N_CLASSES = 3


def simple_mean(probs_list: list[np.ndarray]) -> np.ndarray:
    """Unweighted average across models, renormalized (handles per-model NaN rows as
    "this model abstains" -- averaged only over the models that didn't)."""
    stack = np.stack(probs_list, axis=0)  # (n_models, n_samples, 3)
    all_nan = np.isnan(stack).all(axis=(0, 2))
    if all_nan.any():
        raise ValueError("a sample has no non-NaN prediction from any model")
    mean = np.nanmean(stack, axis=0)
    return mean / mean.sum(axis=1, keepdims=True)


def fit_validation_weights(probs_list: list[np.ndarray], outcomes) -> np.ndarray:
    """One weight per model, inversely proportional to its OWN log loss on the given
    (validation/fit) set -- a model that is twice as good (half the log loss) gets roughly twice
    the weight. Softmax-free, simple, and transparent: `w_i = 1/loss_i`, renormalized to sum 1."""
    y = np.asarray(outcomes)
    losses = np.array([log_loss(p, y) for p in probs_list])
    inv = 1.0 / np.clip(losses, EPS, None)
    return inv / inv.sum()


def weighted_average(probs_list: list[np.ndarray], weights) -> np.ndarray:
    w = np.asarray(weights, dtype=float)
    if len(w) != len(probs_list) or not np.isclose(w.sum(), 1.0, atol=1e-6):
        raise ValueError(f"weights must have length {len(probs_list)} and sum to 1, got {w}")
    stack = np.stack(probs_list, axis=0)
    combined = np.tensordot(w, stack, axes=1)
    return combined / combined.sum(axis=1, keepdims=True)


def build_meta_features(
    probs_list: list[np.ndarray], confidences: list[np.ndarray] | None = None
) -> np.ndarray:
    """Horizontal stack: each model's 3 probabilities, then (if given) each model's confidence.
    Column order is FIXED by `probs_list`'s order -- callers must predict with the same order
    they fit with."""
    cols = list(probs_list)
    if confidences is not None:
        cols += [c.reshape(-1, 1) if c.ndim == 1 else c for c in confidences]
    return np.concatenate([c if c.ndim == 2 else c.reshape(len(c), -1) for c in cols], axis=1)


def fit_logistic_stacking(X: np.ndarray, y, max_iter: int = 1000) -> LogisticRegression:
    """Multinomial logistic regression over the meta-features -- the simplest LEARNED combiner,
    as a baseline against `LightGBM` stacking."""
    model = LogisticRegression(max_iter=max_iter)  # multinomial by default for a 3-class target
    model.fit(X, np.asarray(y))
    return model


def predict_logistic_stacking(model: LogisticRegression, X: np.ndarray) -> np.ndarray:
    """`sklearn` orders `predict_proba` columns by `model.classes_`, not necessarily 0/1/2 --
    reordered here so the caller always gets `[p_home, p_draw, p_away]`."""
    raw = model.predict_proba(X)
    order = [list(model.classes_).index(c) for c in (0, 1, 2)]
    out = raw[:, order]
    validate(out)
    return out


def fit_lightgbm_stacking(X: np.ndarray, y, **lgbm_kwargs):
    import lightgbm as lgb

    params = {
        "objective": "multiclass", "num_class": N_CLASSES, "verbosity": -1,
        "deterministic": True, "num_threads": 1, **lgbm_kwargs,
    }  # fmt: skip
    return lgb.train(params, lgb.Dataset(X, label=np.asarray(y)))


def predict_lightgbm_stacking(booster, X: np.ndarray) -> np.ndarray:
    out = np.asarray(booster.predict(X))
    validate(out)
    return out
