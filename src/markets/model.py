"""Rate models for goals, corners, yellow cards and shots on target (ADR 0041).

Each count is modelled as  lambda_home = mu_home[league] * attack[home] * defence[away]  (and mirrored for
the away side), fitted by iterative proportional fitting with exponential time decay and shrinkage of team
strengths toward 1. Goals use Poisson with the Dixon-Coles low-score correction; the other counts use a
negative binomial (overdispersion estimated by moments, Poisson when there is none). Teams without history
get strength 1 and are FLAGGED, never silently treated as known.
"""

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.stats import nbinom, poisson

from src.config import MarketsConfig

from .data import StatMatch

MODEL_VERSION = "markets-1.0.0"


@dataclass
class RateTable:
    attack: dict[str, float]
    defence: dict[str, float]
    mu_home: dict[str, float]
    mu_away: dict[str, float]
    alpha: float  # NB dispersion (0 = Poisson)
    n_rows: int
    team_matches: dict[str, float] = field(default_factory=dict)  # decayed match weight per team

    def lambdas(self, home: str, away: str, league: str) -> tuple[float, float]:
        a, d = self.attack, self.defence
        return (
            self.mu_home[league] * a.get(home, 1.0) * d.get(away, 1.0),
            self.mu_away[league] * a.get(away, 1.0) * d.get(home, 1.0),
        )


def decay_weights(kickoffs: list[datetime], as_of: datetime, half_life_days: float) -> np.ndarray:
    age = np.array([(as_of - k).total_seconds() / 86400.0 for k in kickoffs])
    return 0.5 ** (np.maximum(age, 0.0) / half_life_days)


def fit_rates(
    hy: np.ndarray,
    ay: np.ndarray,
    homes: list[str],
    aways: list[str],
    leagues: list[str],
    w: np.ndarray,
    cfg: MarketsConfig,
) -> RateTable:
    teams = sorted({*homes, *aways})
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    h = np.array([idx[t] for t in homes])
    a = np.array([idx[t] for t in aways])
    lg = sorted(set(leagues))
    li = np.array([lg.index(x) for x in leagues])
    att = np.ones(n)
    dfn = np.ones(n)
    mu_h = np.array([np.average(hy[li == i], weights=w[li == i]) for i in range(len(lg))])
    mu_a = np.array([np.average(ay[li == i], weights=w[li == i]) for i in range(len(lg))])
    base = float((np.average(hy, weights=w) + np.average(ay, weights=w)) / 2)
    k = cfg.shrink_pseudo_matches * base
    for _ in range(cfg.ipf_iterations):
        num = np.bincount(h, w * hy, n) + np.bincount(a, w * ay, n)
        den = np.bincount(h, w * mu_h[li] * dfn[a], n) + np.bincount(a, w * mu_a[li] * dfn[h], n)
        att = (num + k) / (den + k)
        numd = np.bincount(a, w * hy, n) + np.bincount(h, w * ay, n)
        dend = np.bincount(a, w * mu_h[li] * att[h], n) + np.bincount(h, w * mu_a[li] * att[a], n)
        dfn = (numd + k) / (dend + k)
        att, dfn = att / att.mean(), dfn / dfn.mean()
        for i in range(len(lg)):
            m = li == i
            mu_h[i] = np.sum(w[m] * hy[m]) / np.sum(w[m] * att[h[m]] * dfn[a[m]])
            mu_a[i] = np.sum(w[m] * ay[m]) / np.sum(w[m] * att[a[m]] * dfn[h[m]])
    lam_h = mu_h[li] * att[h] * dfn[a]
    lam_a = mu_a[li] * att[a] * dfn[h]
    y = np.concatenate([hy, ay])
    lam = np.concatenate([lam_h, lam_a])
    ww = np.concatenate([w, w])
    alpha = max(0.0, float(np.sum(ww * ((y - lam) ** 2 - lam)) / np.sum(ww * lam**2)))
    tm = np.bincount(h, w, n) + np.bincount(a, w, n)
    return RateTable(
        dict(zip(teams, att.tolist(), strict=True)),
        dict(zip(teams, dfn.tolist(), strict=True)),
        dict(zip(lg, mu_h.tolist(), strict=True)),
        dict(zip(lg, mu_a.tolist(), strict=True)),
        alpha,
        len(hy),
        dict(zip(teams, tm.tolist(), strict=True)),
    )


def count_pmf(mu: float, alpha: float, kmax: int) -> np.ndarray:
    k = np.arange(kmax + 1)
    if alpha < 1e-6:
        p = poisson.pmf(k, mu)
    else:
        r = 1.0 / alpha
        p = nbinom.pmf(k, r, r / (r + mu))
    p = np.asarray(p, dtype=float)
    p[-1] += max(0.0, 1.0 - p.sum())  # fold the far tail into the last bin
    return p


def dc_tau(x: int, y: int, lh: float, la: float, rho: float) -> float:
    if x == 0 and y == 0:
        return 1.0 - lh * la * rho
    if x == 0 and y == 1:
        return 1.0 + lh * rho
    if x == 1 and y == 0:
        return 1.0 + la * rho
    if x == 1 and y == 1:
        return 1.0 - rho
    return 1.0


def scoreline_matrix(lh: float, la: float, rho: float, max_goals: int) -> np.ndarray:
    m = np.outer(count_pmf(lh, 0.0, max_goals), count_pmf(la, 0.0, max_goals))
    for x in (0, 1):
        for y in (0, 1):
            m[x, y] *= dc_tau(x, y, lh, la, rho)
    return m / m.sum()


def fit_rho(table: RateTable, ms: list[StatMatch], w: np.ndarray, max_goals: int) -> float:
    """Maximise the weighted log-likelihood of the observed scorelines over rho."""
    lams = [table.lambdas(m.home_id, m.away_id, m.league) for m in ms]
    cells = [(min(m.home_goals, max_goals), min(m.away_goals, max_goals)) for m in ms]

    def nll(rho: float) -> float:
        tot = 0.0
        for (x, y), (lh, la), wi in zip(cells, lams, w, strict=True):
            tot -= wi * np.log(max(scoreline_matrix(lh, la, rho, max_goals)[x, y], 1e-12))
        return tot

    return float(minimize_scalar(nll, bounds=(-0.3, 0.3), method="bounded", options={"xatol": 1e-3}).x)


@dataclass
class MarketModels:
    goals: RateTable
    rho: float
    counts: dict[str, RateTable]
    as_of: datetime
    n_matches: int
    cfg: MarketsConfig


def fit_markets(matches: list[StatMatch], as_of: datetime, cfg: MarketsConfig) -> MarketModels:
    """Fit on matches whose result was available at `as_of` (nothing later can enter)."""
    ms = [m for m in matches if m.available_at <= as_of]
    if len(ms) < 200:
        raise ValueError(f"only {len(ms)} matches available before {as_of.isoformat()}")
    w = decay_weights([m.kickoff_utc for m in ms], as_of, cfg.half_life_days)
    homes, aways, lgs = [m.home_id for m in ms], [m.away_id for m in ms], [m.league for m in ms]
    goals = fit_rates(
        np.array([m.home_goals for m in ms], float),
        np.array([m.away_goals for m in ms], float),
        homes,
        aways,
        lgs,
        w,
        cfg,
    )
    goals.alpha = 0.0  # goals are Poisson (the Dixon-Coles term handles low scores)
    rho = fit_rho(goals, ms, w, cfg.max_goals)
    counts = {}
    for stat in cfg.count_stats:
        sub = [m for m in ms if m.home_stats[stat] is not None and m.away_stats[stat] is not None]
        if len(sub) < 200:
            continue
        counts[stat] = fit_rates(
            np.array([m.home_stats[stat] for m in sub], float),
            np.array([m.away_stats[stat] for m in sub], float),
            [m.home_id for m in sub],
            [m.away_id for m in sub],
            [m.league for m in sub],
            decay_weights([m.kickoff_utc for m in sub], as_of, cfg.half_life_days),
            cfg,
        )
    return MarketModels(goals, rho, counts, as_of, len(ms), cfg)
