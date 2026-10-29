"""Chronological calibration and selection protocol (ADR 0089). Windows by season, never mixed:

  fit     2020-21, 2021-22   every candidate calibrator is fitted here
  select  2022-23            the candidate is chosen here, under the pre-registered gate below
  test    2023-24            read ONCE, only for the chosen calibrator and the identity (champion)

Gate (fixed before any data was looked at): a candidate replaces the identity only if, on the select window, it lowers log loss by at least GATE_MIN_GAIN, does not worsen Brier, and does not
worsen ECE by more than GATE_ECE_TOLERANCE. The chosen calibrator is refitted on fit + select and scored once on test, with a paired block bootstrap over ISO weeks whose alpha is divided by
the number of candidates tried (winner's-curse guard). Status: SHADOW_CANDIDATE only if the test interval is wholly below 0 and Brier and ECE do not worsen; otherwise KEEP_CHAMPION. Nothing
is served either way; serving a calibrator needs its own ADR.
"""

from datetime import datetime

import numpy as np
from scipy.optimize import minimize

from src.league_models import bench_metrics as bm

from . import calibrators as cal

WINDOWS = {"fit": ("2020-21", "2021-22"), "select": ("2022-23",), "test": ("2023-24",)}
GATE_MIN_GAIN = 0.0010
GATE_ECE_TOLERANCE = 0.005
MIN_LEAGUE_ROWS = 800          # a league-specific calibrator is not even tried with fewer fit + select rows in that league
BOOTSTRAP = 2000
SEED = 20261029
TARGETS = ("1x2", "over_2_5", "btts")
EPS = 1e-9


def arrays(rows: list[dict], target: str) -> tuple[np.ndarray, np.ndarray]:
    if target == "1x2":
        return np.array([r["p"] for r in rows]), np.array([r["y"] for r in rows])
    key, lab = ("po", "o") if target == "over_2_5" else ("pb", "b")
    return np.array([r[key] for r in rows]), np.array([r[lab] for r in rows])


def losses(target: str, p: np.ndarray, y: np.ndarray) -> np.ndarray:
    if target == "1x2":
        return -np.log(np.clip(p[np.arange(len(y)), y], EPS, 1.0))
    q = np.clip(p, EPS, 1 - EPS)
    return -(y * np.log(q) + (1 - y) * np.log(1 - q))


def brier(target: str, p: np.ndarray, y: np.ndarray) -> float:
    if target == "1x2":
        return float(np.sum((p - np.eye(3)[y]) ** 2, axis=1).mean())
    return float(((p - y) ** 2).mean())


def ece(target: str, p: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    """Top-label ECE for 1X2, probability-of-1 ECE for binary targets; equal-width bins."""
    if target == "1x2":
        conf, hit = p.max(axis=1), (p.argmax(axis=1) == y).astype(float)
    else:
        conf, hit = p, y.astype(float)
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(conf, edges) - 1, 0, bins - 1)
    return float(sum((idx == k).mean() * abs(conf[idx == k].mean() - hit[idx == k].mean()) for k in range(bins) if (idx == k).any()))


def slope_intercept(p: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Logistic recalibration y ~ sigmoid(a * logit(p) + b): a = 1, b = 0 for a calibrated forecast; a < 1 means over-confident."""
    t = np.log(np.clip(p, EPS, 1 - EPS) / np.clip(1 - p, EPS, 1))
    y = y.astype(float)

    def nll(v):
        q = np.clip(1 / (1 + np.exp(-(v[0] * t + v[1]))), EPS, 1 - EPS)
        return float(-(y * np.log(q) + (1 - y) * np.log(1 - q)).mean())

    r = minimize(nll, np.array([1.0, 0.0]), method="L-BFGS-B")
    return float(r.x[0]), float(r.x[1])


def summary(target: str, p: np.ndarray, y: np.ndarray) -> dict:
    out = {"n": int(len(y)), "log_loss": float(losses(target, p, y).mean()), "brier": brier(target, p, y), "ece": ece(target, p, y)}
    if target == "1x2":
        out["slope_intercept_home"] = slope_intercept(p[:, 0], (y == 0).astype(int))
        out["slope_intercept_away"] = slope_intercept(p[:, 2], (y == 2).astype(int))
        out["accuracy"] = float((p.argmax(axis=1) == y).mean())
    else:
        out["slope_intercept"] = slope_intercept(p, y)
        out["accuracy"] = float(((p >= 0.5) == (y == 1)).mean())
        out["mean_prediction"], out["observed_rate"] = float(p.mean()), float(y.mean())
    return out


def window(rows: list[dict], name: str) -> list[dict]:
    return [r for r in rows if r["season"] in WINDOWS[name]]


def _fitter(kind: str, t: str, eligible: list[str]):
    """A function rows -> (apply(rows) -> probabilities, registry entry). `league_<kind>`: one calibrator per eligible league, the global one for every other league."""
    if not kind.startswith("league_"):
        def fit_fn(rows):
            p, y = arrays(rows, t)
            c = cal.fit(kind, t, p, y)
            return (lambda rs: c.apply(arrays(rs, t)[0])), c.describe()

        return fit_fn
    base = kind[len("league_"):]

    def fit_league(rows):
        p, y = arrays(rows, t)
        glob = cal.fit(base, t, p, y)
        per = {lg: cal.fit(base, t, *arrays([r for r in rows if r["league"] == lg], t)) for lg in eligible}

        def apply(rs):
            q = glob.apply(arrays(rs, t)[0])
            pr = arrays(rs, t)[0]
            for lg, c in per.items():
                m = np.array([r["league"] == lg for r in rs])
                if m.any():
                    q[m] = c.apply(pr[m])
            return q

        return apply, {"kind": kind, "global": glob.describe(), "leagues": {lg: c.describe() for lg, c in per.items()}}

    return fit_league


def select_and_test(rows: list[dict]) -> dict:
    fit_rows, sel_rows, test_rows = window(rows, "fit"), window(rows, "select"), window(rows, "test")
    out: dict = {"windows": {k: {"seasons": list(v), "rows": len(window(rows, k))} for k, v in WINDOWS.items()}, "targets": {}}
    leagues = sorted({r["league"] for r in rows})
    out["league_rows_fit_select"] = {lg: sum(1 for r in fit_rows + sel_rows if r["league"] == lg) for lg in leagues}
    eligible = [lg for lg, n in out["league_rows_fit_select"].items() if n >= MIN_LEAGUE_ROWS]
    out["league_calibrator_leagues"] = eligible
    blocks = [bm.iso_week_block(datetime.fromisoformat(r["kickoff"])) for r in test_rows]
    for t in TARGETS:
        ps, ys = arrays(sel_rows, t)
        ident_sel = summary(t, ps, ys)
        kinds = [*cal.candidates(t), *([f"league_{'temperature' if t == '1x2' else 'platt'}"] if eligible else [])]
        table, tried = {}, 0
        for kind in kinds:
            try:
                apply, _ = _fitter(kind, t, eligible)(fit_rows)
            except ValueError as e:
                table[kind] = {"status": f"NOT_FITTED: {e}"}
                continue
            tried += 1 if kind != "identity" else 0
            table[kind] = summary(t, apply(sel_rows), ys)
        passing = {k: v for k, v in table.items() if k != "identity" and "log_loss" in v and ident_sel["log_loss"] - v["log_loss"] >= GATE_MIN_GAIN and v["brier"] <= ident_sel["brier"] and v["ece"] <= ident_sel["ece"] + GATE_ECE_TOLERANCE}
        chosen = min(passing, key=lambda k: passing[k]["log_loss"]) if passing else "identity"
        pt, yt = arrays(test_rows, t)
        apply, registry = _fitter(chosen, t, eligible)(fit_rows + sel_rows)
        q = apply(test_rows)
        ident_test, chosen_test = summary(t, pt, yt), summary(t, q, yt)
        entry = {"select_window": table, "chosen": chosen, "gate_passed": bool(passing), "candidates_tried": tried, "registry": registry, "test_identity": ident_test, "test_chosen": chosen_test}
        if chosen != "identity":
            alpha = 0.05 / max(tried, 1)
            d = bm.paired_block_bootstrap(losses(t, q, yt), losses(t, pt, yt), blocks, BOOTSTRAP, SEED, alpha)
            entry["test_paired"] = {"delta": float(d.delta), "interval": [float(d.lower), float(d.upper)], "alpha": alpha, "blocks": int(d.n_blocks)}
            ok = d.upper < 0 and chosen_test["brier"] <= ident_test["brier"] and chosen_test["ece"] <= ident_test["ece"] + GATE_ECE_TOLERANCE
            entry["status"] = "SHADOW_CANDIDATE" if ok else "KEEP_CHAMPION"
        else:
            entry["status"] = "KEEP_CHAMPION"
        out["targets"][t] = entry
    out["test_rows_read_once"] = True
    return out


TIERS = ((0.0, 0.40), (0.40, 0.50), (0.50, 0.60), (0.60, 0.70), (0.70, 1.01))


def confidence_tiers(rows: list[dict]) -> list[dict]:
    """1X2 top-pick tiers of the champion on the given rows: share of matches, mean stated probability, hit rate."""
    from src.markets.track import wilson

    p, y = arrays(rows, "1x2")
    top, hit = p.max(axis=1), (p.argmax(axis=1) == y)
    out = []
    for lo, hi in TIERS:
        m = (top >= lo) & (top < hi)
        n = int(m.sum())
        out.append({"tier": f"{lo:.2f}-{min(hi, 1.0):.2f}", "n": n, "coverage": n / len(y), "mean_stated": float(top[m].mean()) if n else None, "hit_rate": float(hit[m].mean()) if n else None,
                    "ci95": wilson(int(hit[m].sum()), n) if n else None})  # fmt: skip
    return out
