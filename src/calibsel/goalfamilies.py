"""Goal-distribution families for the market model (ADR 0091): the candidates sprint 10 of Phase 2 could not add.

Every family keeps the market model's expected goals (lh, la) of each out-of-fold row and only changes the SHAPE of the score grid:
  dc          production: independent Poisson with the Dixon-Coles low-score correction (rho of the fit)
  poisson     independent Poisson, no correction
  bivariate   bivariate Poisson: home = Y1 + Y3, away = Y2 + Y3 with E[home] = lh, E[away] = la and covariance lambda3 = theta * min(lh, la)
  negbin      independent negative binomial margins with the same means and a common dispersion r (variance = mu + mu^2 / r)
The single shape parameter (theta, r) is fitted by exact-score log-likelihood on the FIT window only (a fixed grid, chosen before any data was read). Selection and the one test read follow the
protocol of ADR 0089 (`protocol.WINDOWS`, `GATE_MIN_GAIN`, block bootstrap with alpha / candidates): a family is chosen per target on 2022-23 and scored once on 2023-24.
"""

from datetime import datetime

import numpy as np
from scipy.stats import nbinom, poisson

from src.league_models import bench_metrics as bm
from src.markets.model import scoreline_matrix

from . import protocol

MAX_GOALS = 10
THETA_GRID = (0.0, 0.02, 0.05, 0.08, 0.12, 0.16, 0.2, 0.3)
R_GRID = (5.0, 10.0, 20.0, 40.0, 80.0, 160.0)
FAMILIES = ("dc", "poisson", "bivariate", "negbin")
EPS = 1e-12


def grid(family: str, lh: float, la: float, rho: float, shape: float | None) -> np.ndarray:
    k = np.arange(MAX_GOALS + 1)
    if family == "dc":
        return scoreline_matrix(lh, la, rho, MAX_GOALS)
    if family == "poisson":
        m = np.outer(poisson.pmf(k, lh), poisson.pmf(k, la))
    elif family == "negbin":
        r = float(shape)
        m = np.outer(nbinom.pmf(k, r, r / (r + lh)), nbinom.pmf(k, r, r / (r + la)))
    else:  # bivariate
        l3 = float(shape) * min(lh, la)
        l1, l2 = max(lh - l3, 1e-6), max(la - l3, 1e-6)
        p1, p2, p3 = poisson.pmf(k, l1), poisson.pmf(k, l2), poisson.pmf(k, l3)
        m = np.zeros((MAX_GOALS + 1, MAX_GOALS + 1))
        for c in range(MAX_GOALS + 1):  # the common part c is added to both sides
            m[c:, c:] += p3[c] * np.outer(p1[: MAX_GOALS + 1 - c], p2[: MAX_GOALS + 1 - c])
    return m / m.sum()


def markets(m: np.ndarray) -> tuple[np.ndarray, float, float]:
    i, j = np.indices(m.shape)
    return np.array([m[i > j].sum(), m[i == j].sum(), m[i < j].sum()]), float(m[(i + j) > 2].sum()), float(m[1:, 1:].sum())


def score_nll(rows: list[dict], family: str, shape: float | None) -> float:
    tot = 0.0
    for r in rows:
        m = grid(family, r["lh"], r["la"], r["rho"], shape)
        tot -= np.log(max(m[min(r["hg"], MAX_GOALS), min(r["ag"], MAX_GOALS)], EPS))
    return tot / len(rows)


def fit_shape(rows: list[dict], family: str) -> float | None:
    if family in ("dc", "poisson"):
        return None
    options = THETA_GRID if family == "bivariate" else R_GRID
    return min(options, key=lambda s: score_nll(rows, family, s))


def predict(rows: list[dict], family: str, shape: float | None) -> dict[str, np.ndarray]:
    p1, po, pb = [], [], []
    for r in rows:
        a, b, c = markets(grid(family, r["lh"], r["la"], r["rho"], shape))
        p1.append(a)
        po.append(b)
        pb.append(c)
    return {"1x2": np.array(p1), "over_2_5": np.array(po), "btts": np.array(pb)}


def study(rows: list[dict]) -> dict:
    fit_rows, sel_rows, test_rows = protocol.window(rows, "fit"), protocol.window(rows, "select"), protocol.window(rows, "test")
    shapes = {f: fit_shape(fit_rows, f) for f in FAMILIES}
    fit_nll = {f: score_nll(fit_rows, f, shapes[f]) for f in FAMILIES}
    sel = {f: predict(sel_rows, f, shapes[f]) for f in FAMILIES}
    out: dict = {"shapes": shapes, "fit_exact_score_nll": fit_nll, "select_exact_score_nll": {f: score_nll(sel_rows, f, shapes[f]) for f in FAMILIES}, "targets": {}}
    blocks = [bm.iso_week_block(datetime.fromisoformat(r["kickoff"])) for r in test_rows]
    tried = len(FAMILIES) - 1
    for t in protocol.TARGETS:
        _, ys = protocol.arrays(sel_rows, t)
        table = {f: protocol.summary(t, sel[f][t], ys) for f in FAMILIES}
        base = table["dc"]
        passing = {f: v for f, v in table.items() if f != "dc" and base["log_loss"] - v["log_loss"] >= protocol.GATE_MIN_GAIN and v["brier"] <= base["brier"] and v["ece"] <= base["ece"] + protocol.GATE_ECE_TOLERANCE}
        chosen = min(passing, key=lambda f: passing[f]["log_loss"]) if passing else "dc"
        entry = {"select_window": table, "chosen": chosen, "gate_passed": bool(passing)}
        _, yt = protocol.arrays(test_rows, t)
        q_dc = predict(test_rows, "dc", shapes["dc"])[t]
        entry["test_dc"] = protocol.summary(t, q_dc, yt)
        if chosen != "dc":
            refit = fit_shape(fit_rows + sel_rows, chosen)
            q = predict(test_rows, chosen, refit)[t]
            entry["refit_shape"] = refit
            entry["test_chosen"] = protocol.summary(t, q, yt)
            alpha = 0.05 / tried
            d = bm.paired_block_bootstrap(protocol.losses(t, q, yt), protocol.losses(t, q_dc, yt), blocks, protocol.BOOTSTRAP, protocol.SEED, alpha)
            entry["test_paired"] = {"delta": float(d.delta), "interval": [float(d.lower), float(d.upper)], "alpha": alpha, "blocks": int(d.n_blocks)}
            entry["status"] = "SHADOW_CANDIDATE" if d.upper < 0 and entry["test_chosen"]["brier"] <= entry["test_dc"]["brier"] else "KEEP_CHAMPION"
        else:
            entry["status"] = "KEEP_CHAMPION"
        out["targets"][t] = entry
    return out
