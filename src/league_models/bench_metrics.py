"""Sprint 2 (ADR 0064): binary-target metrics, per-row losses and the date-block paired bootstrap.

1X2 metrics are NOT reimplemented: `src.evaluation.metrics` (class order HOME/DRAW/AWAY) is reused. This module adds
what the plan asks beyond it: binary log loss / Brier / accuracy / ECE / AUC (AUC only when both classes exist),
the stated probability policy, and a block bootstrap whose blocks are ISO weeks (the sources publish no matchweek).

Probability policy (documented, one place):
  - probabilities must be finite and in [0, 1]; a row that does not sum to 1 within TOL is rejected by 1X2
    validation, never silently renormalised by the metric;
  - log loss clips the probability of the realised class to [EPS, 1] (`EPS` below, same as src.evaluation.metrics);
  - model outputs are normalised ONCE inside the model wrapper (benchmark.py), before any metric sees them."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from src.evaluation import metrics as m1x2

EPS = m1x2.EPS
TOL = m1x2.TOL


def _binary(p, y) -> tuple[np.ndarray, np.ndarray]:
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=int)
    if p.ndim != 1 or len(p) != len(y):
        raise ValueError("p and y must be 1-D arrays of the same length")
    if len(y) == 0:
        raise ValueError("no samples")
    if not np.isfinite(p).all() or (p < 0).any() or (p > 1).any():
        raise ValueError("probabilities must be finite and lie in [0, 1]")
    if not np.isin(y, (0, 1)).all():
        raise ValueError("binary outcomes must be 0 or 1")
    return p, y


def binary_log_loss_rows(p, y) -> np.ndarray:
    p, y = _binary(p, y)
    q = np.where(y == 1, p, 1.0 - p)
    return -np.log(np.clip(q, EPS, 1.0))


def binary_brier_rows(p, y) -> np.ndarray:
    p, y = _binary(p, y)
    return (p - y) ** 2


def binary_accuracy(p, y) -> float:
    """Threshold 0.5; an exact p == 0.5 earns fractional credit 1/2 (no silent tie-break)."""
    p, y = _binary(p, y)
    credit = np.where(p > 0.5, y == 1, np.where(p < 0.5, y == 0, 0.5))
    return float(np.mean(credit))


def binary_ece(p, y, bins: int = 10) -> float:
    """Top-label ECE: confidence = max(p, 1-p) on [0.5, 1], equal-width bins."""
    p, y = _binary(p, y)
    conf = np.maximum(p, 1.0 - p)
    pred = (p >= 0.5).astype(int)
    correct = np.where(p == 0.5, 0.5, (pred == y).astype(float))
    edges = np.linspace(0.5, 1.0, bins + 1)
    idx = np.clip(np.digitize(conf, edges[1:-1]), 0, bins - 1)
    total = 0.0
    for b in range(bins):
        sel = idx == b
        if sel.any():
            total += sel.mean() * abs(correct[sel].mean() - conf[sel].mean())
    return float(total)


def binary_auc(p, y) -> float | None:
    """Mann-Whitney AUC with average ranks for ties; None when only one class is present (undefined)."""
    p, y = _binary(p, y)
    pos, neg = int((y == 1).sum()), int((y == 0).sum())
    if pos == 0 or neg == 0:
        return None
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty(len(p))
    sp = p[order]
    i = 0
    while i < len(sp):
        j = i
        while j + 1 < len(sp) and sp[j + 1] == sp[i]:
            j += 1
        ranks[order[i : j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return float((ranks[y == 1].sum() - pos * (pos + 1) / 2.0) / (pos * neg))


def binary_metrics(p, y, bins: int = 10) -> dict[str, float | None]:
    return {
        "log_loss": float(binary_log_loss_rows(p, y).mean()),
        "brier": float(binary_brier_rows(p, y).mean()),
        "accuracy": binary_accuracy(p, y),
        "ece_raw": binary_ece(p, y, bins),
        "auc": binary_auc(p, y),
    }


def reliability_bins(p, y, bins: int = 10) -> list[dict]:
    """Calibration-curve table of the positive class: per equal-width bin on [0, 1]: n, mean prediction, observed
    rate. Diagnostics only; nothing is fitted from it."""
    p, y = _binary(p, y)
    edges = np.linspace(0.0, 1.0, bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    out = []
    for b in range(bins):
        sel = idx == b
        out.append(
            {
                "bin": f"[{edges[b]:.1f},{edges[b + 1]:.1f}{']' if b == bins - 1 else ')'}",
                "n": int(sel.sum()),
                "mean_prediction": float(p[sel].mean()) if sel.any() else None,
                "observed_rate": float(y[sel].mean()) if sel.any() else None,
            }
        )
    return out


def reliability_bins_1x2(probs, outcomes, bins: int = 10) -> list[dict]:
    """Top-label reliability table for 1X2 (confidence vs credited correctness)."""
    p = np.asarray(probs, dtype=float)
    y = np.asarray(outcomes, dtype=int)
    m1x2.validate(p, y)
    conf = p.max(axis=1)
    correct = m1x2._credit(p, y)
    edges = np.linspace(1 / 3, 1.0, bins + 1)
    idx = np.clip(np.digitize(conf, edges[1:-1]), 0, bins - 1)
    out = []
    for b in range(bins):
        sel = idx == b
        out.append(
            {
                "bin": f"[{edges[b]:.2f},{edges[b + 1]:.2f}{']' if b == bins - 1 else ')'}",
                "n": int(sel.sum()),
                "mean_confidence": float(conf[sel].mean()) if sel.any() else None,
                "observed_accuracy": float(correct[sel].mean()) if sel.any() else None,
            }
        )
    return out


# ---------------------------------------------------------------- per-row losses for the bootstrap
def rows_log_loss_1x2(probs, outcomes) -> np.ndarray:
    p = np.asarray(probs, dtype=float)
    y = np.asarray(outcomes, dtype=int)
    m1x2.validate(p, y)
    return -np.log(np.clip(p[np.arange(len(y)), y], EPS, 1.0))


def rows_brier_1x2(probs, outcomes) -> np.ndarray:
    p = np.asarray(probs, dtype=float)
    y = np.asarray(outcomes, dtype=int)
    m1x2.validate(p, y)
    return np.sum((p - np.eye(3)[y]) ** 2, axis=1)


def rows_rps_1x2(probs, outcomes) -> np.ndarray:
    p = np.asarray(probs, dtype=float)
    y = np.asarray(outcomes, dtype=int)
    m1x2.validate(p, y)
    cum_p = np.cumsum(p, axis=1)[:, :2]
    cum_o = np.cumsum(np.eye(3)[y], axis=1)[:, :2]
    return np.sum((cum_p - cum_o) ** 2, axis=1) / 2.0


def iso_week_block(kickoff: datetime) -> str:
    year, week, _ = kickoff.isocalendar()
    return f"{year}-W{week:02d}"


@dataclass(frozen=True)
class BlockCI:
    n: int
    n_blocks: int
    mean: float
    lower: float
    upper: float
    samples: int
    seed: int
    block: str = "iso_week"


@dataclass(frozen=True)
class PairedDelta:
    """delta = mean(loss_model) - mean(loss_reference); negative = the model scores better (lower loss)."""

    n: int
    n_blocks: int
    delta: float
    lower: float
    upper: float
    share_below_zero: float  # share of bootstrap draws with delta < 0 (descriptive, not a p-value)
    samples: int
    seed: int
    block: str = "iso_week"


def _block_index(blocks: Sequence[str]) -> tuple[np.ndarray, int]:
    labels = sorted(set(blocks))
    lookup = {b: i for i, b in enumerate(labels)}
    return np.array([lookup[b] for b in blocks], dtype=int), len(labels)


def _block_sums(values: np.ndarray, idx: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    sums = np.bincount(idx, weights=values, minlength=k)
    counts = np.bincount(idx, minlength=k).astype(float)
    return sums, counts


def block_bootstrap_mean(
    losses, blocks: Sequence[str], samples: int, seed: int, alpha: float = 0.05
) -> BlockCI:
    v = np.asarray(losses, dtype=float)
    if len(v) == 0 or len(v) != len(blocks):
        raise ValueError("losses and blocks must be non-empty and of equal length")
    idx, k = _block_index(blocks)
    sums, counts = _block_sums(v, idx, k)
    pick = np.random.default_rng(seed).integers(0, k, (samples, k))
    draws = sums[pick].sum(axis=1) / counts[pick].sum(axis=1)
    return BlockCI(
        len(v), k, float(v.mean()), float(np.quantile(draws, alpha / 2)), float(np.quantile(draws, 1 - alpha / 2)),
        samples, seed,
    )  # fmt: skip


def paired_block_bootstrap(
    loss_model, loss_reference, blocks: Sequence[str], samples: int, seed: int, alpha: float = 0.05
) -> PairedDelta:
    """Both models are scored on the SAME resampled blocks, so the delta is a paired quantity."""
    a = np.asarray(loss_model, dtype=float)
    b = np.asarray(loss_reference, dtype=float)
    if len(a) == 0 or len(a) != len(b) or len(a) != len(blocks):
        raise ValueError("loss vectors and blocks must be non-empty and of equal length")
    idx, k = _block_index(blocks)
    sa, counts = _block_sums(a, idx, k)
    sb, _ = _block_sums(b, idx, k)
    pick = np.random.default_rng(seed).integers(0, k, (samples, k))
    draws = (sa[pick].sum(axis=1) - sb[pick].sum(axis=1)) / counts[pick].sum(axis=1)
    return PairedDelta(
        len(a), k, float(a.mean() - b.mean()), float(np.quantile(draws, alpha / 2)),
        float(np.quantile(draws, 1 - alpha / 2)), float((draws < 0).mean()), samples, seed,
    )  # fmt: skip
