"""Paired model-versus-market evaluation on prospective rows (ADR 0087). Chronological, selection and report parts never mix.

Rows are sorted by kick-off. The first half SELECTS (the vig-removal method of the market-only candidate, the weight of the fixed convex blend `w * market + (1 - w) * model`); the second half
REPORTS. Nothing is tuned on the report half. Paired differences are mean log-loss differences per match with a percentile bootstrap over matches (fixed seed). A full-sample table is given too and
is labelled descriptive: it was not used for any choice. OOF-trained stacking is NOT run below the pre-registered sample gate.
"""

import numpy as np

from .devig import METHODS

EPS = 1e-12
WEIGHT_GRID = tuple(round(0.1 * i, 1) for i in range(11))      # blend weight of the market, 0 = model only, 1 = market only
BOOTSTRAP = 2000
SEED = 20261029
SWITCH_GATE_REPORT_ROWS = 300                                    # pre-registered (ADR 0087): report-half rows before the production market rule may be replaced
STACKING_GATE = 600                                              # pre-registered (ADR 0087): rows with >= 3 bookmakers before a trained stacker is fitted


def log_loss(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    return -np.log(np.maximum(p[np.arange(len(y)), y], EPS))


def brier(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    return np.sum((p - np.eye(3)[y]) ** 2, axis=1)


def rps(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    return np.sum((np.cumsum(p, axis=1) - np.cumsum(np.eye(3)[y], axis=1))[:, :2] ** 2, axis=1) / 2


def accuracy(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    return (np.argmax(p, axis=1) == y).astype(float)


def ece(p: np.ndarray, y: np.ndarray, bins: int = 5) -> float:
    """Expected calibration error of the top pick (equal-width confidence bins). Noisy below a few hundred rows."""
    conf, hit = p.max(axis=1), accuracy(p, y)
    edges = np.linspace(conf.min(), conf.max() + 1e-9, bins + 1)
    total = 0.0
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        m = (conf >= lo) & (conf < hi)
        if m.any():
            total += m.mean() * abs(conf[m].mean() - hit[m].mean())
    return float(total)


def metrics(p: np.ndarray, y: np.ndarray) -> dict:
    return {"n": len(y), "log_loss": float(log_loss(p, y).mean()), "brier": float(brier(p, y).mean()), "rps": float(rps(p, y).mean()), "accuracy": float(accuracy(p, y).mean()), "ece": ece(p, y)}


def paired(a: np.ndarray, b: np.ndarray, y: np.ndarray) -> dict:
    """Mean log-loss difference a - b per match (negative = a better) with a 95 % percentile bootstrap interval over matches."""
    d = log_loss(a, y) - log_loss(b, y)
    rng = np.random.default_rng(SEED)
    boots = d[rng.integers(0, len(d), size=(BOOTSTRAP, len(d)))].mean(axis=1)
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {"mean": float(d.mean()), "ci95": [float(lo), float(hi)], "n": len(d)}


def _stack(rows: list[dict], key) -> np.ndarray:
    return np.array([key(r) for r in rows])


def evaluate(rows: list[dict]) -> dict:
    n = len(rows)
    if n < 20:
        return {"n": n, "status": "TOO_FEW_ROWS"}
    cut = (n + 1) // 2
    sel, rep = rows[:cut], rows[cut:]
    y_sel, y_rep, y_all = (_stack(r, lambda x: x["y"]) for r in (sel, rep, rows))
    method_ll = {m: float(log_loss(_stack(sel, lambda x, m=m: x["market"][m]), y_sel).mean()) for m in METHODS}
    method = min(method_ll, key=method_ll.get)
    mk_sel, mo_sel = _stack(sel, lambda x: x["market"][method]), _stack(sel, lambda x: x["p_model"])
    weight_ll = {w: float(log_loss(w * mk_sel + (1 - w) * mo_sel, y_sel).mean()) for w in WEIGHT_GRID}
    w = min(weight_ll, key=weight_ll.get)
    base = np.bincount(y_sel, minlength=3) / len(y_sel)

    def cands(rs: list[dict]) -> dict[str, np.ndarray]:
        mk, mo = _stack(rs, lambda x: x["market"][method]), _stack(rs, lambda x: x["p_model"])
        return {
            "base rate (selection half)": np.tile(base, (len(rs), 1)), "model only": mo,
            "market only (normalization)": _stack(rs, lambda x: x["market"]["normalization"]), f"market only ({method}, selected)": mk,
            f"convex blend w={w} (selected)": w * mk + (1 - w) * mo, "production headline (stored)": _stack(rs, lambda x: x["p_headline"]),
        }  # fmt: skip

    c_rep, c_all = cands(rep), cands(rows)
    ref = "market only (normalization)"
    out = {
        "n": n, "n_selection": len(sel), "n_report": len(rep), "selected_method": method, "method_log_loss_selection": method_ll, "selected_weight": w, "weight_log_loss_selection": weight_ll,
        "report": {k: metrics(v, y_rep) for k, v in c_rep.items()}, "report_paired_vs_market": {k: paired(v, c_rep[ref], y_rep) for k, v in c_rep.items() if k != ref},
        "report_paired_vs_production": paired(c_rep[f"market only ({method}, selected)"], c_rep["production headline (stored)"], y_rep),
        "all_paired_vs_production": paired(c_all[f"market only ({method}, selected)"], c_all["production headline (stored)"], y_all),
        "switch_gate": {"report_rows": len(rep), "needed": SWITCH_GATE_REPORT_ROWS, "status": "NOT_MET" if len(rep) < SWITCH_GATE_REPORT_ROWS else "CHECK_INTERVAL"},
        "all_rows_descriptive": {k: metrics(v, y_all) for k, v in c_all.items()}, "all_rows_paired_vs_market": {k: paired(v, c_all[ref], y_all) for k, v in c_all.items() if k != ref},
        "stacking": {"status": "NOT_RUN", "reason": f"{n} rows with >= 3 bookmakers < {STACKING_GATE} (pre-registered gate)"} if n < STACKING_GATE else {"status": "GATE_OPEN"},
        "sharp_available": int(sum(1 for r in rows if r["sharp"] is not None)),
    }  # fmt: skip
    leagues: dict[str, int] = {}
    for r in rows:
        leagues[r["league"]] = leagues.get(r["league"], 0) + 1
    out["leagues"] = leagues
    return out
