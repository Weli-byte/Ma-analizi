"""Distribution comparisons with an explicit sample-size floor (ADR 0092).

- continuous values (probabilities, expected goals): PSI on the reference's quantile bins (the project's `src.mlops.monitor.psi`), Jensen-Shannon divergence on the same bins, two-sample
  Kolmogorov-Smirnov statistic and p-value;
- categorical values (league mix, a yes/no flag): Jensen-Shannon divergence of the category shares (PSI and KS are not meaningful there).
Below MIN_N rows on either side the answer is INSUFFICIENT_DATA, never a number that looks like a measurement.
"""

from collections import Counter

import numpy as np
from scipy.stats import ks_2samp

from src.mlops.monitor import psi

MIN_N = 100
EPS = 1e-4


def _js(p: np.ndarray, q: np.ndarray) -> float:
    p, q = np.clip(p, EPS, None), np.clip(q, EPS, None)
    p, q = p / p.sum(), q / q.sum()
    m = (p + q) / 2
    return float(0.5 * (p * np.log2(p / m)).sum() + 0.5 * (q * np.log2(q / m)).sum())


def continuous(reference, current, bins: int = 10) -> dict:
    r, c = np.asarray(reference, dtype=float), np.asarray(current, dtype=float)
    if len(r) < MIN_N or len(c) < MIN_N:
        return {"status": "INSUFFICIENT_DATA", "n_reference": len(r), "n_current": len(c), "min_n": MIN_N}
    edges = np.unique(np.quantile(r, np.linspace(0, 1, bins + 1)))
    edges[0], edges[-1] = -np.inf, np.inf
    hr, hc = np.histogram(r, edges)[0].astype(float), np.histogram(c, edges)[0].astype(float)
    ks = ks_2samp(r, c)
    return {"status": "MEASURED", "n_reference": len(r), "n_current": len(c), "psi": psi(r, c, bins), "js": _js(hr, hc), "ks": float(ks.statistic), "ks_p": float(ks.pvalue),
            "mean_reference": float(r.mean()), "mean_current": float(c.mean())}  # fmt: skip


def categorical(reference: list, current: list) -> dict:
    if len(reference) < MIN_N or len(current) < MIN_N:
        return {"status": "INSUFFICIENT_DATA", "n_reference": len(reference), "n_current": len(current), "min_n": MIN_N}
    cats = sorted({*reference, *current}, key=str)
    cr, cc = Counter(reference), Counter(current)
    p, q = np.array([cr[k] for k in cats], float), np.array([cc[k] for k in cats], float)
    return {"status": "MEASURED", "n_reference": len(reference), "n_current": len(current), "js": _js(p, q),
            "share_reference": {str(k): round(cr[k] / len(reference), 4) for k in cats}, "share_current": {str(k): round(cc[k] / len(current), 4) for k in cats}}  # fmt: skip
