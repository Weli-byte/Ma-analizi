"""S9: the per-bin data behind `metrics.ece` (reliability curve) and a plain confidence
histogram -- `ece` already computes this binning internally but only returns the single
scalar summary; this module exposes the bins themselves for a reliability diagram/report table.
"""

import numpy as np

from .metrics import _credit, _prep


def reliability_curve(probs, outcomes, bins: int = 10) -> list[dict]:
    """One row per bin with ANY samples, in bin order: `confidence` (mean top-label probability
    in the bin), `accuracy` (mean fractional-credit correctness, same tie rule as `metrics.ece`),
    `count`, and `gap` (confidence - accuracy; positive = overconfident). Empty bins are omitted,
    never fabricated as a zero/zero row."""
    p, y = _prep(probs, outcomes)
    conf = p.max(axis=1)
    correct = _credit(p, y)
    edges = np.linspace(1 / 3, 1.0, bins + 1)
    idx = np.clip(np.digitize(conf, edges[1:-1]), 0, bins - 1)
    rows = []
    for b in range(bins):
        m = idx == b
        if not m.any():
            continue
        c, a = float(conf[m].mean()), float(correct[m].mean())
        rows.append(
            {
                "bin": b,
                "bin_range": (round(float(edges[b]), 4), round(float(edges[b + 1]), 4)),
                "confidence": round(c, 6),
                "accuracy": round(a, 6),
                "count": int(m.sum()),
                "gap": round(c - a, 6),
            }
        )
    return rows


def confidence_histogram(probs, bins: int = 10) -> list[dict]:
    """Distribution of top-label confidence, independent of correctness (unlike the reliability
    curve) -- answers "how confident is this model, regardless of whether it's right?"."""
    p = np.asarray(probs, dtype=float)
    conf = p.max(axis=1)
    edges = np.linspace(1 / 3, 1.0, bins + 1)
    counts, _ = np.histogram(conf, bins=edges)
    return [
        {"bin": b, "bin_range": (round(float(edges[b]), 4), round(float(edges[b + 1]), 4)), "count": int(c)}
        for b, c in enumerate(counts)
        if c > 0
    ]
