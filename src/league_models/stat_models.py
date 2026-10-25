"""Sprint 3 (ADR 0065): league statistical models. `LeagueStatModel` interface, a ridge-regularised Poisson /
Dixon-Coles model with optional league terms (the cross-league bridge), and a time-aware Elo with per-country scales.

Mathematics of `RidgePoissonDC` (plan page 9):
    log(lambda_home) = mu + kappa[comp] + h + hdev[comp] + A[home] - D[away] + beta * x_home
    log(lambda_away) = mu + kappa[comp]               + A[away] - D[home] + beta * x_away
    A[t] = attack[t] + alpha[country(t)],   D[t] = defense[t] + delta[country(t)]
Attack/defense deviations, country offsets, competition terms and beta carry an L2 penalty toward 0 (the identifiability
constraint: a prior, not a hard sum-to-zero); mu and the global home advantage h are unpenalised. The country offsets are
identified only by matches between clubs of different countries (UEFA), so they ARE the cross-league bridge. The Dixon-Coles
rho acts on the 0-0, 0-1, 1-0, 1-1 cells with tau >= 0 enforced (a negative mass rejects the model); the grid is truncated at
`max_goals`, its mass outside is reported as the tail mass, and the tau-adjusted grid is renormalised with the pre-normalisation
sum reported. 1X2, over 2.5, BTTS and exact score all come from the same matrix.

Nothing here reads goals of a match that is not history at the fold cutoff: `fit` receives training rows only, `predict`
receives the rows to forecast (their goals are never read)."""

import math
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
from scipy.optimize import minimize, minimize_scalar
from scipy.special import gammaln

from src.evaluation.dataset import EvalRow

NOT_TRAINED = "NOT_TRAINED"
TRAINED = "TRAINED"


class NotTrained(RuntimeError):
    """A fit that did not meet its own acceptance conditions. The caller records NOT_TRAINED and falls back explicitly."""


def country_of(team_id: str) -> str:
    """Country prefix of a canonical team id (`ENG_arsenal` -> `ENG`)."""
    return team_id.split("_", 1)[0]


@dataclass
class MatrixPrediction:
    """Per-row outputs of a goal-distribution model. `matrices` is (n, G+1, G+1), rows index home goals."""

    matrices: np.ndarray
    lam_home: np.ndarray
    lam_away: np.ndarray
    seen_home: np.ndarray
    seen_away: np.ndarray
    tail_mass: np.ndarray  # Poisson mass outside the grid, before the Dixon-Coles adjustment
    raw_sum: np.ndarray  # grid sum after the Dixon-Coles adjustment, before renormalisation

    def one_x_two(self) -> np.ndarray:
        m = self.matrices
        return np.stack(
            [np.tril(m, -1).sum(axis=(1, 2)), np.trace(m, axis1=1, axis2=2), np.triu(m, 1).sum(axis=(1, 2))],
            axis=1,
        )

    def over(self, line: float) -> np.ndarray:
        g = np.arange(self.matrices.shape[1])
        return self.matrices[:, (g[:, None] + g[None, :]) > line].sum(axis=1)

    def btts(self) -> np.ndarray:
        return self.matrices[:, 1:, 1:].sum(axis=(1, 2))

    def p_score(self, h: int, a: int) -> np.ndarray:
        return self.matrices[:, h, a]

    def top_score(self) -> list[str]:
        n, g, _ = self.matrices.shape
        flat = self.matrices.reshape(n, -1).argmax(axis=1)
        return [f"{i // g}-{i % g}" for i in flat]

    def mix(self, other: "MatrixPrediction", w: np.ndarray) -> "MatrixPrediction":
        """(1 - w) * self + w * other, row-wise: a mixture of two valid distributions is a valid distribution."""
        w3 = w[:, None, None]
        return MatrixPrediction(
            (1 - w3) * self.matrices + w3 * other.matrices,
            (1 - w) * self.lam_home + w * other.lam_home,
            (1 - w) * self.lam_away + w * other.lam_away,
            self.seen_home, self.seen_away, np.maximum(self.tail_mass, other.tail_mass),
            np.maximum(self.raw_sum, other.raw_sum),
        )  # fmt: skip

    def take(self, idx: np.ndarray) -> "MatrixPrediction":
        return MatrixPrediction(*(getattr(self, f)[idx] for f in (
            "matrices", "lam_home", "lam_away", "seen_home", "seen_away", "tail_mass", "raw_sum")))  # fmt: skip


class LeagueStatModel:
    """Interface of the Sprint 3 candidates. `status` is TRAINED or NOT_TRAINED; a NOT_TRAINED model must not be asked to
    predict (the caller substitutes an explicit, counted fallback)."""

    model_id: str
    model_version = "1.0.0"
    status: str = NOT_TRAINED
    diagnostics: dict

    def fit(self, rows: list[EvalRow]) -> "LeagueStatModel":
        raise NotImplementedError


# --------------------------------------------------------------------------------------------- Poisson / Dixon-Coles
@dataclass(frozen=True)
class DCSpec:
    use_rho: bool = True
    competition_terms: bool = False
    home_dev: bool = True  # only meaningful with competition_terms
    country_levels: bool = False
    use_rest: bool = False
    half_life_days: float | None = None
    l2_team: float = 2.0
    l2_country: float = 1.0
    l2_competition: float = 1.0
    l2_home_dev: float = 2.0
    l2_beta: float = 1.0
    max_goals: int = 12
    maxiter: int = 2000
    rho_bounds_cap: float = 0.5
    rest_cap_days: float = 14.0
    rest_reference_days: float = 7.0


def rest_features(rows: list[EvalRow], cap: float, reference: float) -> dict[str, tuple[float, float]]:
    """fixture_id -> (home, away) centred log rest days, from the previous match of each team in `rows` (any competition).
    A team's first match has no history: its feature is 0 (= the reference rest). Only earlier kick-offs are used; two
    fixtures with the same kick-off never see each other."""
    last: dict[str, datetime] = {}
    pending: dict[datetime, list[tuple[str, str]]] = {}
    out: dict[str, tuple[float, float]] = {}
    ref = math.log1p(reference)
    for r in sorted(rows, key=lambda x: (x.kickoff_utc, x.fixture_id)):
        # fixtures of an earlier kick-off are history now; fixtures sharing this kick-off are not
        for k in [k for k in pending if k < r.kickoff_utc]:
            for h, a in pending.pop(k):
                last[h] = last[a] = k
        feats = []
        for t in (r.home_id, r.away_id):
            prev = last.get(t)
            days = min(cap, (r.kickoff_utc - prev).total_seconds() / 86400.0) if prev is not None else None
            feats.append(0.0 if days is None else math.log1p(max(days, 0.0)) - ref)
        out[r.fixture_id] = (feats[0], feats[1])
        pending.setdefault(r.kickoff_utc, []).append((r.home_id, r.away_id))
    return out


class RidgePoissonDC(LeagueStatModel):
    model_id = "ridge_dc"

    def __init__(self, spec: DCSpec | None = None, model_id: str | None = None) -> None:
        self.spec = spec or DCSpec()
        if model_id:
            self.model_id = model_id
        self.status = NOT_TRAINED
        self.diagnostics: dict = {}
        self.rho = 0.0

    # -- layout --------------------------------------------------------------------------------------------------
    def _index(self, rows: list[EvalRow]) -> None:
        self.teams = sorted({t for r in rows for t in (r.home_id, r.away_id)})
        self.t_idx = {t: i for i, t in enumerate(self.teams)}
        self.countries = sorted({country_of(t) for t in self.teams})
        self.c_idx = {c: i for i, c in enumerate(self.countries)}
        self.comps = sorted({r.league_id for r in rows})
        self.k_idx = {k: i for i, k in enumerate(self.comps)}
        self.team_country = np.array([self.c_idx[country_of(t)] for t in self.teams])

    def _sizes(self) -> dict[str, int]:
        s, nt, nc, nk = self.spec, len(self.teams), len(self.countries), len(self.comps)
        return {
            "mu": 1, "h": 1, "a": nt, "d": nt,
            "alpha": nc if s.country_levels else 0, "delta": nc if s.country_levels else 0,
            "kappa": nk if s.competition_terms else 0, "hdev": nk if (s.competition_terms and s.home_dev) else 0,
            "beta": 1 if s.use_rest else 0,
        }  # fmt: skip

    def _slices(self) -> dict[str, slice]:
        out, pos = {}, 0
        for k, n in self._sizes().items():
            out[k] = slice(pos, pos + n)
            pos += n
        self._n = pos
        return out

    def _penalties(self) -> np.ndarray:
        s, sl = self.spec, self._sl
        lam = np.zeros(self._n)
        lam[sl["a"]] = lam[sl["d"]] = s.l2_team
        lam[sl["alpha"]] = lam[sl["delta"]] = s.l2_country
        lam[sl["kappa"]] = s.l2_competition
        lam[sl["hdev"]] = s.l2_home_dev
        lam[sl["beta"]] = s.l2_beta
        return lam

    def _etas(self, theta, hi, ai, ki, xh, xa):
        sl = self._sl
        A = theta[sl["a"]].copy()
        D = theta[sl["d"]].copy()
        if self.spec.country_levels:
            A += theta[sl["alpha"]][self.team_country]
            D += theta[sl["delta"]][self.team_country]
        base = theta[sl["mu"]][0]
        eh = base + theta[sl["h"]][0] + A[hi] - D[ai]
        ea = base + A[ai] - D[hi]
        if self.spec.competition_terms:
            eh = eh + theta[sl["kappa"]][ki]
            ea = ea + theta[sl["kappa"]][ki]
            if self.spec.home_dev:
                eh = eh + theta[sl["hdev"]][ki]
        if self.spec.use_rest:
            b = theta[sl["beta"]][0]
            eh = eh + b * xh
            ea = ea + b * xa
        return eh, ea

    # -- fit ---------------------------------------------------------------------------------------------------
    def fit(
        self, rows: list[EvalRow], cutoff: datetime | None = None, rest: dict | None = None
    ) -> "RidgePoissonDC":
        s = self.spec
        self.status = NOT_TRAINED
        if not rows:
            raise NotTrained("no training rows")
        if any(r.home_goals is None or r.away_goals is None for r in rows):
            raise NotTrained("a training row has no goals")
        self._index(rows)
        self._sl = self._slices()
        n = len(rows)
        hi = np.array([self.t_idx[r.home_id] for r in rows])
        ai = np.array([self.t_idx[r.away_id] for r in rows])
        ki = np.array([self.k_idx[r.league_id] for r in rows])
        yh = np.array([r.home_goals for r in rows], dtype=float)
        ya = np.array([r.away_goals for r in rows], dtype=float)
        xh = xa = np.zeros(n)
        if s.use_rest:
            if rest is None:
                raise NotTrained("rest features requested but not supplied")
            xh = np.array([rest[r.fixture_id][0] for r in rows])
            xa = np.array([rest[r.fixture_id][1] for r in rows])
        w = np.ones(n)
        if s.half_life_days is not None:
            ref = cutoff or max(r.kickoff_utc for r in rows)
            age = np.array([(ref - r.kickoff_utc).total_seconds() / 86400.0 for r in rows])
            w = 0.5 ** (np.clip(age, 0, None) / s.half_life_days)
            w = w * n / w.sum()  # mean weight 1, so the penalties keep their meaning
        lam_pen = self._penalties()
        sl, nt, nc, nk = self._sl, len(self.teams), len(self.countries), len(self.comps)

        def objective(theta):
            eh, ea = self._etas(theta, hi, ai, ki, xh, xa)
            lh, la = np.exp(np.clip(eh, -20, 20)), np.exp(np.clip(ea, -20, 20))
            nll = float(np.sum(w * (lh - yh * eh + la - ya * ea)) + 0.5 * np.sum(lam_pen * theta**2))
            gh, ga = w * (lh - yh), w * (la - ya)
            g = lam_pen * theta
            g[sl["mu"]] += gh.sum() + ga.sum()
            g[sl["h"]] += gh.sum()
            dA = np.bincount(hi, gh, nt) + np.bincount(ai, ga, nt)
            dD = -np.bincount(ai, gh, nt) - np.bincount(hi, ga, nt)
            g[sl["a"]] += dA
            g[sl["d"]] += dD
            if s.country_levels:
                g[sl["alpha"]] += np.bincount(self.team_country, dA, nc)
                g[sl["delta"]] += np.bincount(self.team_country, dD, nc)
            if s.competition_terms:
                g[sl["kappa"]] += np.bincount(ki, gh + ga, nk)
                if s.home_dev:
                    g[sl["hdev"]] += np.bincount(ki, gh, nk)
            if s.use_rest:
                g[sl["beta"]] += float(np.sum(gh * xh + ga * xa))
            return nll, g

        theta0 = np.zeros(self._n)
        theta0[sl["mu"]] = math.log(max(float(np.mean(np.r_[yh, ya])), 1e-3))
        res = minimize(objective, theta0, jac=True, method="L-BFGS-B",
                       options={"maxiter": s.maxiter, "ftol": 1e-15, "gtol": 1e-9})  # fmt: skip
        grad_norm = float(np.max(np.abs(objective(res.x)[1])))
        self.theta = res.x
        self.diagnostics = {
            "optimizer": "scipy.optimize.minimize(L-BFGS-B), analytic gradient",
            "message": str(res.message),
            "n_iter": int(res.nit),
            "max_abs_gradient": grad_norm,
            "training_rows": n,
            "teams": len(self.teams),
            "countries": len(self.countries) if s.country_levels else 0,
            "competitions": len(self.comps),
            "penalised_negative_log_likelihood": float(res.fun),
        }
        # L-BFGS-B may report "abnormal termination in line search" at a point whose gradient is already ~0 (float
        # precision): the acceptance test is the gradient itself; the optimizer flag is recorded, never hidden
        self.diagnostics["mean_abs_gradient_per_row"] = grad_norm / n
        self.diagnostics["converged"] = bool(np.isfinite(res.fun) and grad_norm / n <= 1e-5)
        self.diagnostics["optimizer_success_flag"] = bool(res.success)
        if not self.diagnostics["converged"]:
            raise NotTrained(f"optimizer did not converge: {res.message} (max |grad| {grad_norm:.2e})")
        self._fit_rho(hi, ai, ki, xh, xa, yh, ya, w)
        self.status = TRAINED
        return self

    def _fit_rho(self, hi, ai, ki, xh, xa, yh, ya, w) -> None:
        s = self.spec
        eh, ea = self._etas(self.theta, hi, ai, ki, xh, xa)
        lh, la = np.exp(eh), np.exp(ea)
        safety = 1.5  # rows to forecast may have a larger lambda than any training row
        lo = max(-1.0 / (safety * float(lh.max())), -1.0 / (safety * float(la.max())), -s.rho_bounds_cap)
        hi_b = min(1.0, float(np.min(1.0 / (safety**2 * lh * la))), s.rho_bounds_cap)
        self.diagnostics["rho_positivity_interval"] = [lo, hi_b]
        if not s.use_rho:
            self.rho = 0.0
            return
        low = (yh <= 1) & (ya <= 1)
        x, y, lhl, lal, wl = yh[low], ya[low], lh[low], la[low], w[low]

        def neg_ll(rho: float) -> float:
            tau = _tau(x, y, lhl, lal, rho)
            if (tau <= 0).any():
                return 1e12
            return float(-(wl * np.log(tau)).sum())

        r = minimize_scalar(
            neg_ll, bounds=(lo * 0.999, hi_b * 0.999), method="bounded", options={"xatol": 1e-8}
        )
        self.rho = float(r.x)
        self.diagnostics["rho"] = self.rho
        self.diagnostics["rho_optimizer_success"] = bool(r.success)
        if not r.success:
            raise NotTrained("rho optimizer failed")

    # -- predict -----------------------------------------------------------------------------------------------
    def team_effects(self, team: str) -> tuple[float, float, bool]:
        """(attack, defense, seen). An unseen club gets its country level (when modelled) plus a zero deviation."""
        sl = self._sl
        seen = team in self.t_idx
        a = float(self.theta[sl["a"]][self.t_idx[team]]) if seen else 0.0
        d = float(self.theta[sl["d"]][self.t_idx[team]]) if seen else 0.0
        c = country_of(team)
        if self.spec.country_levels and c in self.c_idx:
            a += float(self.theta[sl["alpha"]][self.c_idx[c]])
            d += float(self.theta[sl["delta"]][self.c_idx[c]])
        return a, d, seen

    def predict(self, rows: list[EvalRow], rest: dict | None = None, neutral: bool = False) -> MatrixPrediction:
        """`neutral=True` (ADR 0080): the matches are played at one neutral venue, so there is no home side. The home advantage (global term plus the competition's deviation) is split evenly:
        half is taken from the nominal home side and half is given to the other, exactly the "mid level" rule of the product's rate model; the default (False) is unchanged."""
        if self.status != TRAINED:
            raise NotTrained(f"{self.model_id} is {self.status}: no prediction")
        s, sl = self.spec, self._sl
        n, g = len(rows), self.spec.max_goals + 1
        ea_h, ed_h, sh = zip(*(self.team_effects(r.home_id) for r in rows), strict=True)
        ea_a, ed_a, sa = zip(*(self.team_effects(r.away_id) for r in rows), strict=True)
        hadv = np.full(n, float(self.theta[sl["h"]][0]))
        eh = self.theta[sl["mu"]][0] + self.theta[sl["h"]][0] + np.array(ea_h) - np.array(ed_a)
        ea = self.theta[sl["mu"]][0] + np.array(ea_a) - np.array(ed_h)
        if s.competition_terms:
            kappa = np.array(
                [
                    self.theta[sl["kappa"]][self.k_idx[r.league_id]] if r.league_id in self.k_idx else 0.0
                    for r in rows
                ]
            )
            eh, ea = eh + kappa, ea + kappa
            if s.home_dev:
                dev = np.array(
                    [
                        self.theta[sl["hdev"]][self.k_idx[r.league_id]] if r.league_id in self.k_idx else 0.0
                        for r in rows
                    ]
                )
                eh = eh + dev
                hadv = hadv + dev
        if s.use_rest:
            if rest is None:
                raise NotTrained("rest features requested but not supplied")
            b = self.theta[sl["beta"]][0]
            eh = eh + b * np.array([rest[r.fixture_id][0] for r in rows])
            ea = ea + b * np.array([rest[r.fixture_id][1] for r in rows])
        if neutral:
            eh, ea = eh - hadv / 2.0, ea + hadv / 2.0
        lh, la = np.exp(eh), np.exp(ea)
        if not (np.isfinite(lh).all() and np.isfinite(la).all() and (lh > 0).all() and (la > 0).all()):
            raise NotTrained("non-positive or non-finite lambda")
        k = np.arange(g)
        ph = _pmf(k[None, :], lh[:, None])
        pa = _pmf(k[None, :], la[:, None])
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
        mats = mats / raw[:, None, None]
        return MatrixPrediction(mats, lh, la, np.array(sh), np.array(sa), tail, raw)


def _pmf(k: np.ndarray, lam: np.ndarray) -> np.ndarray:
    return np.exp(k * np.log(lam) - lam - gammaln(k + 1.0))


def _tau(x, y, lh, la, rho):
    """Dixon-Coles low-score factor: only the cells 0-0, 0-1, 1-0, 1-1 differ from 1."""
    t = np.ones_like(lh, dtype=float)
    t = np.where((x == 0) & (y == 0), 1.0 - lh * la * rho, t)
    t = np.where((x == 0) & (y == 1), 1.0 + lh * rho, t)
    t = np.where((x == 1) & (y == 0), 1.0 + la * rho, t)
    t = np.where((x == 1) & (y == 1), 1.0 - rho, t)
    return t


# --------------------------------------------------------------------------------------------- time-aware Elo
@dataclass(frozen=True)
class EloSpec:
    initial_rating: float = 1500.0
    k_factor: float = 20.0
    home_advantage: float = 60.0
    goal_difference_multiplier: bool = True
    season_reversion: float = 0.25
    promoted_penalty: float = 50.0
    min_matches_certain: int = 10
    bridge_k: float = 6.0
    bridge_team_k_share: float = 0.5
    bridge: bool = True  # False = local scope: only the rows given are replayed, no country offsets


@dataclass
class EloState:
    ratings: dict[str, float] = field(default_factory=dict)
    matches: dict[str, int] = field(default_factory=dict)
    offsets: dict[str, float] = field(
        default_factory=dict
    )  # country bridge offsets, learned from UEFA matches only
    season: dict[str, str] = field(default_factory=dict)  # team -> last season its rating was reverted at
    domestic_seen: set[str] = field(default_factory=set)
    uncertain_predictions: int = 0
    promoted_entries: int = 0


def season_year(season: str) -> int:
    return int(season.split("-")[0])


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -60.0, 60.0)))


class TimeAwareElo(LeagueStatModel):
    """Per-country Elo: domestic matches update club ratings inside their own country scale (a club first seen in a
    domestic league enters below the country mean = promoted prior); UEFA matches only move the country offsets and, with a
    reduced K, the clubs. Season change gives back `season_reversion` of the distance to the country mean. Fixtures are
    replayed in kick-off order and every fixture sharing a kick-off is predicted before any of them updates the state."""

    model_id = "time_elo"

    def __init__(
        self, spec: EloSpec | None = None, model_id: str | None = None, uefa_ids: frozenset[str] = frozenset()
    ) -> None:
        self.spec = spec or EloSpec()
        if model_id:
            self.model_id = model_id
        self.uefa_ids = uefa_ids  # competitions treated as international (bridge) matches
        self.state = EloState()
        self.status = NOT_TRAINED
        self.diagnostics: dict = {}
        self._beta, self._t1, self._t2 = 1.0, -0.5, 0.5

    # -- state helpers ---------------------------------------------------------------------------------------
    def _country_mean(self, country: str) -> float:
        vals = [
            r
            for t, r in self.state.ratings.items()
            if country_of(t) == country and t in self.state.domestic_seen
        ]
        return float(np.mean(vals)) if vals else self.spec.initial_rating

    def _ensure(self, team: str, season: str, international: bool) -> None:
        st, sp = self.state, self.spec
        if team not in st.ratings:
            if international or not sp.bridge:
                st.ratings[team] = (
                    sp.initial_rating if not sp.bridge else self._country_mean(country_of(team))
                )
            else:
                st.ratings[team] = self._country_mean(country_of(team)) - (
                    sp.promoted_penalty if st.domestic_seen else 0.0
                )
                st.promoted_entries += 1 if st.domestic_seen else 0
            st.matches[team] = 0
            st.season[team] = season
        elif st.season[team] != season and season_year(season) > season_year(st.season[team]):
            mean = (
                self._country_mean(country_of(team))
                if sp.bridge
                else float(np.mean(list(st.ratings.values())))
            )
            st.ratings[team] = mean + (1.0 - sp.season_reversion) * (st.ratings[team] - mean)
            st.season[team] = season

    def _offset(self, team: str) -> float:
        return self.state.offsets.get(country_of(team), 0.0) if self.spec.bridge else 0.0

    def _diff(self, r: EvalRow) -> float:
        st = self.state
        self._ensure(r.home_id, r.season, r.league_id in self.uefa_ids)
        self._ensure(r.away_id, r.season, r.league_id in self.uefa_ids)
        international = r.league_id in self.uefa_ids
        d = st.ratings[r.home_id] - st.ratings[r.away_id] + self.spec.home_advantage
        if international:
            d += self._offset(r.home_id) - self._offset(r.away_id)
        return d

    def _update(self, r: EvalRow, diff: float) -> None:
        st, sp = self.state, self.spec
        international = r.league_id in self.uefa_ids
        expected = 1.0 / (1.0 + 10.0 ** (-diff / 400.0))
        actual = 1.0 if r.outcome == 0 else (0.5 if r.outcome == 1 else 0.0)
        mult = (
            1.0 + math.log1p(abs((r.home_goals or 0) - (r.away_goals or 0)))
            if sp.goal_difference_multiplier
            else 1.0
        )
        step = sp.k_factor * mult * (actual - expected)
        share = sp.bridge_team_k_share if (international and sp.bridge) else 1.0
        st.ratings[r.home_id] += share * step
        st.ratings[r.away_id] -= share * step
        for t in (r.home_id, r.away_id):
            st.matches[t] += 1
            if not international:
                st.domestic_seen.add(t)
        if international and sp.bridge:
            ch, ca = country_of(r.home_id), country_of(r.away_id)
            if ch != ca:  # a same-country UEFA tie carries no information about the country levels
                bridge_step = sp.bridge_k * mult * (actual - expected)
                st.offsets[ch] = st.offsets.get(ch, 0.0) + bridge_step
                st.offsets[ca] = st.offsets.get(ca, 0.0) - bridge_step

    # -- replay ----------------------------------------------------------------------------------------------
    def replay(self, rows: list[EvalRow], update: bool = True) -> tuple[np.ndarray, list[bool]]:
        """Pre-match rating differences in the order of `rows` (kick-off ordered). Fixtures with an identical kick-off are
        all predicted before any of them is applied. Returns (diffs, uncertain flags)."""
        order = sorted(range(len(rows)), key=lambda i: (rows[i].kickoff_utc, rows[i].fixture_id))
        diffs = np.zeros(len(rows))
        uncertain = [False] * len(rows)
        i = 0
        while i < len(order):
            j = i
            while j < len(order) and rows[order[j]].kickoff_utc == rows[order[i]].kickoff_utc:
                j += 1
            group = order[i:j]
            for idx in group:
                r = rows[idx]
                diffs[idx] = self._diff(r)
                uncertain[idx] = (
                    min(self.state.matches[r.home_id], self.state.matches[r.away_id])
                    < self.spec.min_matches_certain
                )
            if update:
                for idx in group:
                    self._update(rows[idx], float(diffs[idx]))
            i = j
        return diffs, uncertain

    # -- ordinal-logit mapping (fitted on the training replay only) -------------------------------------------
    def _fit_mapping(self, diffs: np.ndarray, outcomes: np.ndarray) -> bool:
        x = diffs / 400.0
        yh, yd, ya = (
            (outcomes == 0).astype(float),
            (outcomes == 1).astype(float),
            (outcomes == 2).astype(float),
        )

        def nll(p):
            beta, t1, gap = p
            t2 = t1 + np.logaddexp(0.0, gap)
            s1, s2 = _sigmoid(t1 - beta * x), _sigmoid(t2 - beta * x)
            ph, pd_, pa = (
                np.clip(1 - s2, 1e-12, None),
                np.clip(s2 - s1, 1e-12, None),
                np.clip(s1, 1e-12, None),
            )
            return -float(np.sum(yh * np.log(ph) + yd * np.log(pd_) + ya * np.log(pa)))

        res = minimize(
            nll, np.array([self._beta, self._t1, 1.0]), method="BFGS", options={"gtol": 1e-8, "maxiter": 500}
        )
        beta, t1, gap = res.x
        self._beta, self._t1, self._t2 = float(beta), float(t1), float(t1 + np.logaddexp(0.0, gap))
        # BFGS may stop with "precision loss" at a point whose gradient is already ~0: judge by the gradient, not the flag
        grad = np.max(np.abs(res.jac)) if getattr(res, "jac", None) is not None else float("inf")
        self.diagnostics["mapping_optimizer"] = {"success": bool(res.success), "message": str(res.message),
                                                  "max_abs_gradient": float(grad), "mean_abs_gradient": float(grad / max(len(outcomes), 1)), "beta": self._beta}  # fmt: skip
        return bool(np.isfinite(res.fun) and grad / max(len(outcomes), 1) < 1e-5 and self._beta > 0)

    def fit(self, rows: list[EvalRow]) -> "TimeAwareElo":
        self.status = NOT_TRAINED
        if not rows:
            raise NotTrained("no training rows")
        self.state = EloState()
        diffs, _ = self.replay(rows, update=True)
        ordered = sorted(rows, key=lambda r: (r.kickoff_utc, r.fixture_id))
        order = sorted(range(len(rows)), key=lambda i: (rows[i].kickoff_utc, rows[i].fixture_id))
        outcomes = np.array([rows[i].outcome for i in order])
        if not self._fit_mapping(diffs[order], outcomes):
            raise NotTrained(f"outcome mapping did not converge: {self.diagnostics['mapping_optimizer']}")
        self.diagnostics.update({
            "teams_rated": len(self.state.ratings), "training_rows": len(ordered), "country_offsets": len(self.state.offsets),
            "promoted_entries": self.state.promoted_entries,
        })  # fmt: skip
        self.status = TRAINED
        return self

    def predict_proba(self, rows: list[EvalRow]) -> tuple[np.ndarray, list[bool]]:
        """Predict `rows` sequentially (each result is applied only after the whole kick-off group is predicted)."""
        if self.status != TRAINED:
            raise NotTrained(f"{self.model_id} is {self.status}: no prediction")
        diffs, uncertain = self.replay(rows, update=True)
        x = diffs / 400.0
        s1, s2 = _sigmoid(self._t1 - self._beta * x), _sigmoid(self._t2 - self._beta * x)
        p = np.stack([1.0 - s2, np.clip(s2 - s1, 0.0, None), s1], axis=1)
        p = p / p.sum(axis=1, keepdims=True)
        self.state.uncertain_predictions += int(sum(uncertain))
        return p, uncertain
