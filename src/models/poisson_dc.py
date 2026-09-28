"""S5 — Poisson / Dixon-Coles goal-scoring model.

## Model

Each team i has a log-scale attack strength `alpha_i` and defense strength `beta_i`. For a
match with home team h and away team a:

    lambda_home = exp(alpha_h + beta_a + home_adv)
    lambda_away = exp(alpha_a + beta_h)

Goals are modelled as independent Poisson(lambda_home) / Poisson(lambda_away) draws. The
scoreline probability matrix is the outer product of the two Poisson pmfs (truncated at
`max_goals`, with the remaining mass folded into the last row/column so it still sums to 1),
and 1X2 probabilities are the matrix's strictly-upper / diagonal / strictly-lower sums.

## Fitting

`alpha`/`beta`/`home_adv` are fit by iterative proportional fitting (IPF): holding all other
parameters fixed, each team's attack (then defense, then home_adv) is rescaled in log-space by
the log-ratio of its actual to model-expected goals. This is the classical algorithm for
Poisson log-linear models and is exactly equivalent to Poisson MLE with a canonical link.

## Identifiability

The model has a translation invariance: `alpha_i -> alpha_i + c`, `beta_i -> beta_i - c` for
any team leaves every `lambda` unchanged, so `alpha`/`beta` are only identified up to that
shift. This implementation resolves it by recentering `alpha` to mean zero after every IPF
sweep (a standard constraint); `home_adv` and every team's *relative* strength are unaffected.

## Assumptions & limitations

- Goals are assumed Poisson-distributed and (for the base `PoissonModel`) independent between
  home and away score — a known simplification: real match scores are weakly negatively
  correlated, most visible in low-scoring games (Dixon & Coles, 1997).
- `DixonColesModel` corrects exactly that: a `tau(x, y)` multiplier on the four scorelines
  {0-0, 1-0, 0-1, 1-1}, parameterized by `rho`, fit by a 1-D grid search maximizing the
  training log-likelihood after `alpha`/`beta`/`home_adv` are fixed.
- No time decay: every training match has equal weight regardless of recency (in scope for a
  later sprint, not S4/S5).
- Fit happens ONLY on the training period passed to `fit()`; future fixtures are never read.
- A team unseen in training has no `alpha`/`beta` and falls back to the league-average
  strength (0.0 in log-space); counted in `diagnostics["unseen_team_rows"]`, never silent.
"""

from dataclasses import dataclass

import numpy as np

from src.evaluation.dataset import EvalRow

from .baselines import BaselineModel

_LOG_EPS = 1e-9


def _poisson_pmf(k: np.ndarray, lam: np.ndarray) -> np.ndarray:
    """log-space-stable Poisson pmf, k: (max_goals+1,), lam: (n,) -> (n, max_goals+1)."""
    k = k[None, :]
    lam = lam[:, None]
    log_p = k * np.log(np.clip(lam, _LOG_EPS, None)) - lam - np.array(
        [sum(np.log(np.arange(1, i + 1))) for i in k[0]]
    )
    return np.exp(log_p)


@dataclass(frozen=True)
class TeamStrength:
    team_id: str
    attack: float
    defense: float


class PoissonModel(BaselineModel):
    """Independent-goals Poisson model. Attack/defense/home-advantage only; see module docstring."""

    model_id = "poisson"
    model_version = "1.0.0"
    model_class = "statistical"

    def __init__(self, max_goals: int = 10, ipf_sweeps: int = 40) -> None:
        super().__init__()
        self.max_goals = max_goals
        self.ipf_sweeps = ipf_sweeps
        self.attack: dict[str, float] = {}
        self.defense: dict[str, float] = {}
        self.home_adv: float = 0.0
        self._fitted = False

    # ---- fitting (IPF) ---------------------------------------------------------------
    def _strength(self, table: dict[str, float], team: str) -> float:
        return table.get(team, 0.0)  # unseen team -> league-average strength (log-scale 0)

    def _lambdas(self, home: np.ndarray, away: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        a_h = np.array([self._strength(self.attack, t) for t in home])
        d_h = np.array([self._strength(self.defense, t) for t in home])
        a_a = np.array([self._strength(self.attack, t) for t in away])
        d_a = np.array([self._strength(self.defense, t) for t in away])
        lam_h = np.exp(a_h + d_a + self.home_adv)
        lam_a = np.exp(a_a + d_h)
        return lam_h, lam_a

    def _ipf_update(self, table: dict[str, float], teams: list[str], is_attack: bool, home, away, hg, ag):
        lam_h, lam_a = self._lambdas(home, away)
        for t in teams:
            home_mask, away_mask = home == t, away == t
            if is_attack:
                actual = hg[home_mask].sum() + ag[away_mask].sum()
                expected = lam_h[home_mask].sum() + lam_a[away_mask].sum()
            else:  # defense: goals CONCEDED
                actual = ag[home_mask].sum() + hg[away_mask].sum()
                expected = lam_a[home_mask].sum() + lam_h[away_mask].sum()
            if expected > _LOG_EPS and actual > 0:
                table[t] = table.get(t, 0.0) + np.log(actual / expected)
            elif actual == 0 and expected > _LOG_EPS:
                table[t] = table.get(t, 0.0) - 0.25  # nudge down; never a hard silent zero

    def fit(self, train: list[EvalRow]) -> "PoissonModel":
        rows = [r for r in train if r.home_goals is not None and r.away_goals is not None]
        skipped = len(train) - len(rows)
        if not rows:
            raise ValueError("PoissonModel.fit: no training rows carry goal counts")
        home = np.array([r.home_id for r in rows])
        away = np.array([r.away_id for r in rows])
        hg = np.array([r.home_goals for r in rows], dtype=float)
        ag = np.array([r.away_goals for r in rows], dtype=float)
        teams = sorted(set(home) | set(away))
        self.attack = {t: 0.0 for t in teams}
        self.defense = {t: 0.0 for t in teams}
        self.home_adv = float(np.log(max(hg.mean(), _LOG_EPS) / max(ag.mean(), _LOG_EPS)))

        for _ in range(self.ipf_sweeps):
            self._ipf_update(self.attack, teams, True, home, away, hg, ag)
            mean_a = float(np.mean(list(self.attack.values())))
            self.attack = {t: v - mean_a for t, v in self.attack.items()}  # identifiability constraint
            self._ipf_update(self.defense, teams, False, home, away, hg, ag)
            lam_h, lam_a = self._lambdas(home, away)
            self.home_adv += float(np.log(max(hg.sum(), _LOG_EPS) / max(lam_h.sum(), _LOG_EPS)))

        self._fitted = True
        self._fit_extra(home, away, hg, ag)
        self.diagnostics = {
            "n_teams": len(teams),
            "training_rows": len(rows),
            "rows_missing_goals": skipped,
            "home_adv": round(self.home_adv, 4),
            "mean_attack": round(float(np.mean(list(self.attack.values()))), 6),
            "max_goals": self.max_goals,
            **self._extra_diagnostics(),
        }
        return self

    # ---- hooks Dixon-Coles overrides ---------------------------------------------------
    def _fit_extra(self, home, away, hg, ag) -> None:
        return

    def _extra_diagnostics(self) -> dict:
        return {}

    def _tau(self, x: np.ndarray, y: np.ndarray, lam_h: np.ndarray, lam_a: np.ndarray) -> np.ndarray:
        return np.ones_like(x, dtype=float)

    # ---- scoreline matrix / 1X2 --------------------------------------------------------
    def scoreline_matrix(self, lam_h: float, lam_a: float) -> np.ndarray:
        """(max_goals+1, max_goals+1) matrix; tail mass folded into the last row/col so it sums to 1."""
        k = np.arange(self.max_goals + 1)
        ph = _poisson_pmf(k, np.array([lam_h]))[0]
        pa = _poisson_pmf(k, np.array([lam_a]))[0]
        ph[-1] += max(0.0, 1.0 - ph.sum())
        pa[-1] += max(0.0, 1.0 - pa.sum())
        mat = np.outer(ph, pa)
        x, y = np.meshgrid(k, k, indexing="ij")
        mat = mat * self._tau(x.ravel(), y.ravel(), np.array([lam_h]), np.array([lam_a])).reshape(mat.shape)
        return mat / mat.sum()

    def predict_proba(self, rows: list[EvalRow]) -> np.ndarray:
        if not self._fitted:
            raise RuntimeError(f"{self.model_id}.predict_proba called before fit()")
        home = np.array([r.home_id for r in rows])
        away = np.array([r.away_id for r in rows])
        unseen = sum(1 for t in {*home, *away} if t not in self.attack)
        self.diagnostics["unseen_team_rows"] = unseen
        lam_h, lam_a = self._lambdas(home, away)
        out = np.empty((len(rows), 3))
        for i, (lh, la) in enumerate(zip(lam_h, lam_a, strict=True)):
            mat = self.scoreline_matrix(float(lh), float(la))
            out[i, 0] = np.sum(np.tril(mat, -1))  # home goals > away goals
            out[i, 1] = np.trace(mat)  # draw
            out[i, 2] = np.sum(np.triu(mat, 1))  # away wins
        return out / out.sum(axis=1, keepdims=True)


class DixonColesModel(PoissonModel):
    """PoissonModel + the Dixon & Coles (1997) low-score correlation correction."""

    model_id = "dixon_coles"
    model_version = "1.0.0"

    def __init__(self, max_goals: int = 10, ipf_sweeps: int = 40, rho_grid: tuple[float, float, float] = (
        -0.2, 0.2, 0.005,
    )) -> None:  # fmt: skip
        super().__init__(max_goals, ipf_sweeps)
        self.rho_grid = rho_grid
        self.rho = 0.0

    def _tau(self, x, y, lam_h, lam_a):
        lam_h = np.broadcast_to(lam_h, x.shape)
        lam_a = np.broadcast_to(lam_a, x.shape)
        t = np.ones_like(lam_h)
        m00 = (x == 0) & (y == 0)
        m01 = (x == 0) & (y == 1)
        m10 = (x == 1) & (y == 0)
        m11 = (x == 1) & (y == 1)
        t = np.where(m00, 1.0 - lam_h * lam_a * self.rho, t)
        t = np.where(m01, 1.0 + lam_h * self.rho, t)
        t = np.where(m10, 1.0 + lam_a * self.rho, t)
        t = np.where(m11, 1.0 - self.rho, t)
        return np.clip(t, 1e-6, None)

    def _dc_loglik(self, rho: float, lam_h, lam_a, hg, ag) -> float:
        self.rho = rho
        ll = 0.0
        for lh, la, h, a in zip(lam_h, lam_a, hg, ag, strict=True):
            base = -lh - la + h * np.log(max(lh, _LOG_EPS)) + a * np.log(max(la, _LOG_EPS))
            tau = self._tau(np.array([h]), np.array([a]), np.array([lh]), np.array([la]))[0]
            ll += base + np.log(max(tau, 1e-12))
        return float(ll)

    def _fit_extra(self, home, away, hg, ag) -> None:
        lam_h, lam_a = self._lambdas(home, away)
        lo, hi, step = self.rho_grid
        candidates = np.arange(lo, hi + step, step)
        best_rho, best_ll = 0.0, -np.inf
        for rho in candidates:
            ll = self._dc_loglik(float(rho), lam_h, lam_a, hg, ag)
            if ll > best_ll:
                best_ll, best_rho = ll, float(rho)
        self.rho = best_rho

    def _extra_diagnostics(self) -> dict:
        return {"rho": round(self.rho, 4)}


REGISTRY: dict[str, type[BaselineModel]] = {
    PoissonModel.model_id: PoissonModel,
    DixonColesModel.model_id: DixonColesModel,
}
