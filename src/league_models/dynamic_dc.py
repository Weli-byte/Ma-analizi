"""Sprint 4d (ADR 0075): Poisson / Dixon-Coles with TIME-VARYING team strength (a Gaussian random walk across seasons), the "Kalman-style" extension of the hierarchical model.

    log(lambda_home) = mu + h + kappa[comp] + A[home, season] - D[away, season]
    log(lambda_away) = mu +     kappa[comp] + A[away, season] - D[home, season]
    A[t, s] = attack of team t in season s (+ the country level alpha, as in `RidgePoissonDC`), D likewise

The first season of a team has a prior N(0, 1 / l2_team); each later season follows the previous one, A[t, s] ~ N(A[t, s-1], 1 / l2_walk). The MAP estimate is a penalised Poisson
likelihood (analytic gradient, L-BFGS), i.e. the posterior mode of a random-walk state-space model, not a full Kalman filter. A team is predicted with its LAST fitted season (the
random-walk mean for the next season); a team without any fitted season gets its country level and a zero deviation. Rows of one fit never include a match that is not history
at the cutoff: `fit` receives training rows only.

`l2_walk` large = strength constant over time (the static hierarchical model); small = strength free to move from season to season."""

import math
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from scipy.optimize import minimize, minimize_scalar

from src.evaluation.dataset import EvalRow

from .splits import season_start_year
from .stat_models import NOT_TRAINED, TRAINED, DCSpec, MatrixPrediction, NotTrained, _pmf, _tau, country_of


@dataclass(frozen=True)
class DynSpec(DCSpec):
    l2_walk: float = 8.0
    competition_terms: bool = True
    country_levels: bool = True


class DynamicPoissonDC:
    model_id = "dynamic_dc"

    def __init__(self, spec: DynSpec | None = None, model_id: str | None = None) -> None:
        self.spec = spec or DynSpec()
        if model_id:
            self.model_id = model_id
        self.status = NOT_TRAINED
        self.diagnostics: dict = {}
        self.rho = 0.0

    # -- layout
    def _index(self, rows: list[EvalRow]) -> None:
        self.teams = sorted({t for r in rows for t in (r.home_id, r.away_id)})
        self.t_idx = {t: i for i, t in enumerate(self.teams)}
        seasons: dict[str, set[int]] = {}
        for r in rows:
            for t in (r.home_id, r.away_id):
                seasons.setdefault(t, set()).add(season_start_year(r.season))
        self.ts_idx: dict[tuple[str, int], int] = {}
        self.ts_prev: list[int] = []        # index of the previous season of the same team, -1 for its first season
        self.last_ts: dict[str, int] = {}
        for t in self.teams:
            prev = -1
            for y in sorted(seasons[t]):
                self.ts_idx[(t, y)] = len(self.ts_prev)
                self.ts_prev.append(prev)
                prev = self.ts_idx[(t, y)]
            self.last_ts[t] = prev
        self.ts_team = np.array([self.t_idx[t] for (t, _y) in sorted(self.ts_idx, key=self.ts_idx.get)])
        self.countries = sorted({country_of(t) for t in self.teams})
        self.c_idx = {c: i for i, c in enumerate(self.countries)}
        self.comps = sorted({r.league_id for r in rows})
        self.k_idx = {k: i for i, k in enumerate(self.comps)}
        self.ts_country = np.array([self.c_idx[country_of(self.teams[i])] for i in self.ts_team])

    def _layout(self) -> None:
        nts, nc, nk = len(self.ts_prev), len(self.countries), len(self.comps)
        sizes = {"mu": 1, "h": 1, "kappa": nk, "alpha": nc, "delta": nc, "a": nts, "d": nts}
        self._sl, pos = {}, 0
        for k, n in sizes.items():
            self._sl[k] = slice(pos, pos + n)
            pos += n
        self._n = pos

    def _etas(self, theta, hi, ai, ki):
        sl = self._sl
        A = theta[sl["a"]] + theta[sl["alpha"]][self.ts_country]
        D = theta[sl["d"]] + theta[sl["delta"]][self.ts_country]
        base = theta[sl["mu"]][0] + theta[sl["kappa"]][ki]
        return base + theta[sl["h"]][0] + A[hi] - D[ai], base + A[ai] - D[hi]

    # -- fit
    def fit(self, rows: list[EvalRow], cutoff: datetime | None = None) -> "DynamicPoissonDC":
        s = self.spec
        self.status = NOT_TRAINED
        if not rows:
            raise NotTrained("no training rows")
        if any(r.home_goals is None or r.away_goals is None for r in rows):
            raise NotTrained("a training row has no goals")
        self._index(rows)
        self._layout()
        sl, nts, nc, nk = self._sl, len(self.ts_prev), len(self.countries), len(self.comps)
        hi = np.array([self.ts_idx[(r.home_id, season_start_year(r.season))] for r in rows])
        ai = np.array([self.ts_idx[(r.away_id, season_start_year(r.season))] for r in rows])
        ki = np.array([self.k_idx[r.league_id] for r in rows])
        yh = np.array([r.home_goals for r in rows], dtype=float)
        ya = np.array([r.away_goals for r in rows], dtype=float)
        n = len(rows)
        w = np.ones(n)
        if s.half_life_days is not None:
            ref = cutoff or max(r.kickoff_utc for r in rows)
            age = np.array([(ref - r.kickoff_utc).total_seconds() / 86400.0 for r in rows])
            w = 0.5 ** (np.clip(age, 0, None) / s.half_life_days)
            w = w * n / w.sum()
        prev = np.array(self.ts_prev)
        has_prev = prev >= 0
        first = ~has_prev
        pen = np.zeros(self._n)
        pen[sl["alpha"]] = pen[sl["delta"]] = s.l2_country
        pen[sl["kappa"]] = s.l2_competition

        def objective(theta):
            eh, ea = self._etas(theta, hi, ai, ki)
            lh, la = np.exp(np.clip(eh, -20, 20)), np.exp(np.clip(ea, -20, 20))
            a, d = theta[sl["a"]], theta[sl["d"]]
            nll = float(np.sum(w * (lh - yh * eh + la - ya * ea)) + 0.5 * np.sum(pen * theta**2))
            nll += 0.5 * s.l2_team * float(np.sum(a[first] ** 2) + np.sum(d[first] ** 2))
            da, dd = a[has_prev] - a[prev[has_prev]], d[has_prev] - d[prev[has_prev]]
            nll += 0.5 * s.l2_walk * float(np.sum(da**2) + np.sum(dd**2))
            gh, ga = w * (lh - yh), w * (la - ya)
            g = pen * theta
            g[sl["mu"]] += gh.sum() + ga.sum()
            g[sl["h"]] += gh.sum()
            g[sl["kappa"]] += np.bincount(ki, gh + ga, nk)
            dA = np.bincount(hi, gh, nts) + np.bincount(ai, ga, nts)
            dD = -np.bincount(ai, gh, nts) - np.bincount(hi, ga, nts)
            g[sl["alpha"]] += np.bincount(self.ts_country, dA, nc)
            g[sl["delta"]] += np.bincount(self.ts_country, dD, nc)
            ga_, gd_ = dA.copy(), dD.copy()
            ga_[first] += s.l2_team * a[first]
            gd_[first] += s.l2_team * d[first]
            idx = np.where(has_prev)[0]
            ga_[idx] += s.l2_walk * da
            np.add.at(ga_, prev[idx], -s.l2_walk * da)
            gd_[idx] += s.l2_walk * dd
            np.add.at(gd_, prev[idx], -s.l2_walk * dd)
            g[sl["a"]] += ga_
            g[sl["d"]] += gd_
            return nll, g

        self._objective = objective   # exposed for the gradient check in the tests
        theta0 = np.zeros(self._n)
        theta0[sl["mu"]] = math.log(max(float(np.mean(np.r_[yh, ya])), 1e-3))
        res = minimize(objective, theta0, jac=True, method="L-BFGS-B", options={"maxiter": s.maxiter, "ftol": 1e-15, "gtol": 1e-9})
        grad_norm = float(np.max(np.abs(objective(res.x)[1])))
        self.theta = res.x
        self.diagnostics = {"optimizer": "scipy L-BFGS-B, analytic gradient", "message": str(res.message), "n_iter": int(res.nit), "max_abs_gradient": grad_norm, "training_rows": n,
                            "teams": len(self.teams), "team_seasons": nts, "converged": bool(np.isfinite(res.fun) and grad_norm / n <= 1e-5), "mean_abs_gradient_per_row": grad_norm / n}  # fmt: skip
        if not self.diagnostics["converged"]:
            raise NotTrained(f"optimizer did not converge: {res.message} (max |grad| {grad_norm:.2e})")
        self._fit_rho(hi, ai, ki, yh, ya, w)
        self.status = TRAINED
        return self

    def _fit_rho(self, hi, ai, ki, yh, ya, w) -> None:
        s = self.spec
        eh, ea = self._etas(self.theta, hi, ai, ki)
        lh, la = np.exp(eh), np.exp(ea)
        lo = max(-1.0 / (1.5 * float(lh.max())), -1.0 / (1.5 * float(la.max())), -s.rho_bounds_cap)
        hi_b = min(1.0, float(np.min(1.0 / (1.5**2 * lh * la))), s.rho_bounds_cap)
        if not s.use_rho:
            self.rho = 0.0
            return
        low = (yh <= 1) & (ya <= 1)

        def neg_ll(rho: float) -> float:
            tau = _tau(yh[low], ya[low], lh[low], la[low], rho)
            return 1e12 if (tau <= 0).any() else float(-(w[low] * np.log(tau)).sum())

        r = minimize_scalar(neg_ll, bounds=(lo * 0.999, hi_b * 0.999), method="bounded", options={"xatol": 1e-8})
        if not r.success:
            raise NotTrained("rho optimizer failed")
        self.rho = float(r.x)

    # -- predict
    def team_effects(self, team: str) -> tuple[float, float, bool]:
        sl = self._sl
        seen = team in self.t_idx
        a = d = 0.0
        if seen:
            i = self.last_ts[team]
            a, d = float(self.theta[sl["a"]][i]), float(self.theta[sl["d"]][i])
        c = country_of(team)
        if c in self.c_idx:
            a += float(self.theta[sl["alpha"]][self.c_idx[c]])
            d += float(self.theta[sl["delta"]][self.c_idx[c]])
        return a, d, seen

    def predict(self, rows: list[EvalRow]) -> MatrixPrediction:
        if self.status != TRAINED:
            raise NotTrained(f"{self.model_id} is {self.status}: no prediction")
        s, sl = self.spec, self._sl
        n, g = len(rows), s.max_goals + 1
        ah, dh, sh = zip(*(self.team_effects(r.home_id) for r in rows), strict=True)
        aa, da, sa = zip(*(self.team_effects(r.away_id) for r in rows), strict=True)
        kappa = np.array([self.theta[sl["kappa"]][self.k_idx[r.league_id]] if r.league_id in self.k_idx else 0.0 for r in rows])
        base = self.theta[sl["mu"]][0] + kappa
        lh, la = np.exp(base + self.theta[sl["h"]][0] + np.array(ah) - np.array(da)), np.exp(base + np.array(aa) - np.array(dh))
        if not (np.isfinite(lh).all() and np.isfinite(la).all() and (lh > 0).all() and (la > 0).all()):
            raise NotTrained("non-positive or non-finite lambda")
        k = np.arange(g)
        ph, pa = _pmf(k[None, :], lh[:, None]), _pmf(k[None, :], la[:, None])
        tail = np.clip(1.0 - ph.sum(axis=1) * pa.sum(axis=1), 0.0, 1.0)
        mats = ph[:, :, None] * pa[:, None, :]
        if s.use_rho and self.rho != 0.0:
            for x, y in ((0, 0), (0, 1), (1, 0), (1, 1)):
                t = _tau(np.full(n, x), np.full(n, y), lh, la, self.rho)
                if (t < 0).any():
                    raise NotTrained("negative Dixon-Coles correction (negative probability mass)")
                mats[:, x, y] *= t
        raw = mats.sum(axis=(1, 2))
        if not (np.isfinite(mats).all() and (mats >= 0).all() and (raw > 0).all()):
            raise NotTrained("invalid score matrix")
        return MatrixPrediction(mats / raw[:, None, None], lh, la, np.array(sh), np.array(sa), tail, raw)
