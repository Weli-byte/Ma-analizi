"""S5 — Poisson / Dixon-Coles goal-scoring model.
S0-S7 hardening Phase 7 (ADR-0014 amendment): IPF convergence tracking, continuous rho
optimization, optional time decay, scoreline tail-mass measurement, a joint-MLE DC variant.

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
Convergence is tracked (max absolute parameter delta per sweep) against `convergence_tolerance`,
up to `max_iterations` sweeps; `diagnostics["converged"]`/`["iterations_used"]`/`["final_delta"]`
always report the outcome, and `fail_on_non_convergence=True` raises rather than silently using
an unconverged fit.

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
  {0-0, 1-0, 0-1, 1-1}, parameterized by `rho`, fit by bounded scalar optimization
  (`scipy.optimize.minimize_scalar`) maximizing the training log-likelihood AFTER
  `alpha`/`beta`/`home_adv` are fixed (sequential, not joint — `DixonColesJointMLE` below is
  the joint-MLE research variant, kept separate, never the default).
- Optional time decay (`decay_half_life_days`): older matches contribute proportionally less to
  the IPF sums and the rho log-likelihood, weight = `0.5 ** (age_days / half_life)` where age is
  measured from the LAST training match's kickoff. `None` (default) = every match equally
  weighted, byte-identical to the pre-decay model.
- Fit happens ONLY on the training period passed to `fit()`; future fixtures are never read.
- A team unseen in training has no `alpha`/`beta` and falls back to the league-average
  strength (0.0 in log-space); counted in `diagnostics["unseen_team_rows"]`, never silent.
- `scoreline_matrix` reports `captured_mass`/`tail_mass`: the truncated `max_goals` grid folds
  any higher-scoring tail into the boundary row/column so probabilities still sum to 1, but very
  large `lambda` values could mean a non-negligible tail is being approximated as boundary mass;
  `predict_proba` warns (`warnings.warn`) if any row's tail mass exceeds `tail_mass_warn_threshold`.
"""

import warnings
from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize, minimize_scalar

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


def _decay_weights(kickoffs, half_life_days: float | None) -> np.ndarray:
    if half_life_days is None:
        return np.ones(len(kickoffs))
    ref = max(kickoffs)
    age_days = np.array([(ref - t).total_seconds() / 86400.0 for t in kickoffs])
    return 0.5 ** (age_days / half_life_days)


@dataclass(frozen=True)
class TeamStrength:
    team_id: str
    attack: float
    defense: float


class PoissonModel(BaselineModel):
    """Independent-goals Poisson model. Attack/defense/home-advantage only; see module docstring."""

    model_id = "poisson"
    model_version = "1.1.0"  # 1.1.0: IPF convergence tracking + optional decay (Phase 7)
    model_class = "statistical"

    def __init__(
        self,
        max_goals: int = 10,
        max_iterations: int = 200,
        convergence_tolerance: float = 1e-6,
        fail_on_non_convergence: bool = False,
        decay_half_life_days: float | None = None,
        tail_mass_warn_threshold: float = 0.01,
    ) -> None:
        super().__init__()
        self.max_goals = max_goals
        self.max_iterations = max_iterations
        self.convergence_tolerance = convergence_tolerance
        self.fail_on_non_convergence = fail_on_non_convergence
        self.decay_half_life_days = decay_half_life_days
        self.tail_mass_warn_threshold = tail_mass_warn_threshold
        self.attack: dict[str, float] = {}
        self.defense: dict[str, float] = {}
        self.home_adv: float = 0.0
        self.converged: bool = False
        self.iterations_used: int = 0
        self.final_delta: float = float("inf")
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

    def _ipf_update(self, table, teams, is_attack, home, away, hg, ag, w) -> float:
        """Applies one IPF update sweep to `table`; returns the max absolute delta applied."""
        lam_h, lam_a = self._lambdas(home, away)
        max_delta = 0.0
        for t in teams:
            home_mask, away_mask = home == t, away == t
            if is_attack:
                actual = (hg * w)[home_mask].sum() + (ag * w)[away_mask].sum()
                expected = (lam_h * w)[home_mask].sum() + (lam_a * w)[away_mask].sum()
            else:  # defense: goals CONCEDED
                actual = (ag * w)[home_mask].sum() + (hg * w)[away_mask].sum()
                expected = (lam_a * w)[home_mask].sum() + (lam_h * w)[away_mask].sum()
            if expected > _LOG_EPS and actual > 0:
                delta = float(np.log(actual / expected))
            elif actual == 0 and expected > _LOG_EPS:
                delta = -0.25  # nudge down; never a hard silent zero
            else:
                delta = 0.0
            table[t] = table.get(t, 0.0) + delta
            max_delta = max(max_delta, abs(delta))
        return max_delta

    def fit(self, train: list[EvalRow]) -> "PoissonModel":
        rows = [r for r in train if r.home_goals is not None and r.away_goals is not None]
        skipped = len(train) - len(rows)
        if not rows:
            raise ValueError("PoissonModel.fit: no training rows carry goal counts")
        home = np.array([r.home_id for r in rows])
        away = np.array([r.away_id for r in rows])
        hg = np.array([r.home_goals for r in rows], dtype=float)
        ag = np.array([r.away_goals for r in rows], dtype=float)
        w = _decay_weights([r.kickoff_utc for r in rows], self.decay_half_life_days)
        teams = sorted(set(home) | set(away))
        self.attack = {t: 0.0 for t in teams}
        self.defense = {t: 0.0 for t in teams}
        self.home_adv = float(np.log(max((hg * w).sum(), _LOG_EPS) / max((ag * w).sum(), _LOG_EPS)))

        self.converged = False
        self.iterations_used = 0
        self.final_delta = float("inf")
        for i in range(self.max_iterations):
            d1 = self._ipf_update(self.attack, teams, True, home, away, hg, ag, w)
            mean_a = float(np.mean(list(self.attack.values())))
            self.attack = {t: v - mean_a for t, v in self.attack.items()}  # identifiability constraint
            d2 = self._ipf_update(self.defense, teams, False, home, away, hg, ag, w)
            lam_h, lam_a = self._lambdas(home, away)
            ha_delta = float(np.log(max((hg * w).sum(), _LOG_EPS) / max((lam_h * w).sum(), _LOG_EPS)))
            self.home_adv += ha_delta
            self.iterations_used = i + 1
            self.final_delta = max(d1, d2, abs(ha_delta))
            if self.final_delta < self.convergence_tolerance:
                self.converged = True
                break

        if not self.converged and self.fail_on_non_convergence:
            raise RuntimeError(
                f"{self.model_id}: IPF did not converge in {self.iterations_used} iterations "
                f"(final_delta={self.final_delta:.2e} >= tolerance={self.convergence_tolerance:.2e})"
            )

        self._fitted = True
        self._fit_extra(home, away, hg, ag, w)
        self.diagnostics = {
            "n_teams": len(teams),
            "training_rows": len(rows),
            "rows_missing_goals": skipped,
            "home_adv": round(self.home_adv, 4),
            "mean_attack": round(float(np.mean(list(self.attack.values()))), 6),
            "max_goals": self.max_goals,
            "decay_half_life_days": self.decay_half_life_days,
            "converged": self.converged,
            "iterations_used": self.iterations_used,
            "final_delta": round(self.final_delta, 8),
            "max_iterations": self.max_iterations,
            "convergence_tolerance": self.convergence_tolerance,
            **self._extra_diagnostics(),
        }
        return self

    # ---- hooks Dixon-Coles overrides ---------------------------------------------------
    def _fit_extra(self, home, away, hg, ag, w) -> None:
        return

    def _extra_diagnostics(self) -> dict:
        return {}

    def _tau(self, x: np.ndarray, y: np.ndarray, lam_h: np.ndarray, lam_a: np.ndarray) -> np.ndarray:
        return np.ones_like(x, dtype=float)

    # ---- scoreline matrix / 1X2 --------------------------------------------------------
    def expected_goals(self, home_id: str, away_id: str) -> tuple[float, float, bool, bool]:
        """Pre-match goal rates (lambda_home, lambda_away) for one fixture, plus whether each team
        was seen in training. An unseen team gets league-average strength (existing behaviour);
        the flags let callers record that instead of hiding it."""
        if not self._fitted:
            raise RuntimeError("PoissonModel.expected_goals called before fit()")
        lam_h, lam_a = self._lambdas(np.array([home_id]), np.array([away_id]))
        seen_h = home_id in self.attack or home_id in self.defense
        seen_a = away_id in self.attack or away_id in self.defense
        return float(lam_h[0]), float(lam_a[0]), seen_h, seen_a

    def scoreline_matrix(self, lam_h: float, lam_a: float) -> np.ndarray:
        """(max_goals+1, max_goals+1) matrix; tail mass folded into the last row/col so it sums to 1.
        `self.last_captured_mass`/`self.last_tail_mass` record how much of the TRUE (untruncated)
        distribution the `max_goals` grid actually captured before that folding — measured, not
        just assumed adequate."""
        k = np.arange(self.max_goals + 1)
        ph = _poisson_pmf(k, np.array([lam_h]))[0]
        pa = _poisson_pmf(k, np.array([lam_a]))[0]
        captured = float(ph.sum() * pa.sum())
        self.last_captured_mass = captured
        self.last_tail_mass = max(0.0, 1.0 - captured)
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
        lam_h, lam_a = self._lambdas(home, away)
        out = np.empty((len(rows), 3))
        max_tail = 0.0
        for i, (lh, la) in enumerate(zip(lam_h, lam_a, strict=True)):
            mat = self.scoreline_matrix(float(lh), float(la))
            out[i, 0] = np.sum(np.tril(mat, -1))  # home goals > away goals
            out[i, 1] = np.trace(mat)  # draw
            out[i, 2] = np.sum(np.triu(mat, 1))  # away wins
            max_tail = max(max_tail, self.last_tail_mass)
        self.diagnostics["unseen_team_rows"] = unseen
        self.diagnostics["max_tail_mass"] = round(max_tail, 8)
        if max_tail > self.tail_mass_warn_threshold:
            warnings.warn(
                f"{self.model_id}: scoreline tail mass {max_tail:.4f} exceeds "
                f"tail_mass_warn_threshold={self.tail_mass_warn_threshold} for max_goals="
                f"{self.max_goals} — the truncated grid may be a poor approximation for some "
                "fixtures; consider raising max_goals.",
                stacklevel=2,
            )
        return out / out.sum(axis=1, keepdims=True)


class DixonColesModel(PoissonModel):
    """PoissonModel + the Dixon & Coles (1997) low-score correlation correction, rho fit by
    bounded scalar optimization (sequential: alpha/beta/home_adv fixed first). See
    `DixonColesJointMLE` for the joint-MLE research variant (never the default)."""

    model_id = "dixon_coles"
    model_version = "1.1.0"

    def __init__(self, rho_bounds: tuple[float, float] = (-0.3, 0.3), **kwargs) -> None:
        super().__init__(**kwargs)
        self.rho_bounds = rho_bounds
        self.rho = 0.0
        self.rho_optimizer_diagnostics: dict = {}

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

    def _dc_neg_loglik(self, rho: float, lam_h, lam_a, hg, ag, w) -> float:
        self.rho = rho
        tau = self._tau(hg, ag, lam_h, lam_a)
        base = -lam_h - lam_a + hg * np.log(np.clip(lam_h, _LOG_EPS, None)) + ag * np.log(
            np.clip(lam_a, _LOG_EPS, None)
        )
        ll = np.sum(w * (base + np.log(np.clip(tau, 1e-12, None))))
        return -float(ll)

    def _fit_extra(self, home, away, hg, ag, w) -> None:
        lam_h, lam_a = self._lambdas(home, away)
        result = minimize_scalar(
            self._dc_neg_loglik,
            bounds=self.rho_bounds,
            args=(lam_h, lam_a, hg, ag, w),
            method="bounded",
            options={"xatol": 1e-6},
        )
        self.rho = float(result.x)
        self.rho_optimizer_diagnostics = {
            "optimizer": "scipy.optimize.minimize_scalar(bounded)",
            "success": bool(result.success),
            "n_iter": int(result.nit),
            "objective": round(float(result.fun), 6),
            "bounds": list(self.rho_bounds),
        }

    def _extra_diagnostics(self) -> dict:
        return {"rho": round(self.rho, 4), "rho_optimizer": self.rho_optimizer_diagnostics}


class DixonColesJointMLE(DixonColesModel):
    """Research variant (Phase 7, audit finding M-05): jointly optimizes attack, defense,
    home_adv AND rho in one `scipy.optimize.minimize` call, instead of DixonColesModel's
    sequential IPF-then-rho fit. NOT the default `dixon_coles` model and NOT auto-selected —
    compare the two under walk-forward before ever treating this as a replacement (ADR-0014's
    Phase 7 amendment). Identifiability: one team's attack is pinned to 0 (equivalent to
    `DixonColesModel`'s mean-zero recentering, just enforced by omission from the free
    parameter vector instead of a post-hoc correction)."""

    model_id = "dixon_coles_v2_joint_mle"
    model_version = "0.1.0-research"

    def __init__(self, max_iterations: int = 300, **kwargs) -> None:
        kwargs.pop("max_iterations", None)
        super().__init__(max_iterations=max_iterations, **kwargs)
        self.joint_optimizer_diagnostics: dict = {}

    def fit(self, train: list[EvalRow]) -> "DixonColesJointMLE":
        rows = [r for r in train if r.home_goals is not None and r.away_goals is not None]
        skipped = len(train) - len(rows)
        if not rows:
            raise ValueError(f"{self.model_id}.fit: no training rows carry goal counts")
        home = np.array([r.home_id for r in rows])
        away = np.array([r.away_id for r in rows])
        hg = np.array([r.home_goals for r in rows], dtype=float)
        ag = np.array([r.away_goals for r in rows], dtype=float)
        w = _decay_weights([r.kickoff_utc for r in rows], self.decay_half_life_days)
        teams = sorted(set(home) | set(away))
        n = len(teams)
        idx = {t: i for i, t in enumerate(teams)}
        h_idx = np.array([idx[t] for t in home])
        a_idx = np.array([idx[t] for t in away])

        # Warm start from the sequential IPF+bounded-rho fit: still a genuinely joint
        # optimization from there on (not a shortcut), just efficiently initialized.
        warm = DixonColesModel(
            max_goals=self.max_goals,
            max_iterations=self.max_iterations,
            convergence_tolerance=self.convergence_tolerance,
            decay_half_life_days=self.decay_half_life_days,
        ).fit(rows)
        attack0 = np.array([warm.attack.get(t, 0.0) for t in teams])
        defense0 = np.array([warm.defense.get(t, 0.0) for t in teams])
        # theta = [attack_1..attack_{n-1} (attack_0 pinned to 0), defense_0..defense_{n-1},
        #          home_adv, rho_raw (tanh-transformed into rho_bounds)]
        rho_lo, rho_hi = self.rho_bounds
        rho0 = np.clip(warm.rho, rho_lo + 1e-6, rho_hi - 1e-6)
        rho_raw0 = float(np.arctanh(2 * (rho0 - rho_lo) / (rho_hi - rho_lo) - 1))
        theta0 = np.concatenate([attack0[1:] - attack0[0], defense0, [warm.home_adv, rho_raw0]])

        def unpack(theta):
            attack = np.concatenate([[0.0], theta[: n - 1]])
            defense = theta[n - 1 : 2 * n - 1]
            home_adv = theta[2 * n - 1]
            rho_raw = theta[2 * n]
            rho = rho_lo + (rho_hi - rho_lo) * (np.tanh(rho_raw) + 1) / 2
            return attack, defense, home_adv, rho

        def neg_log_lik(theta):
            attack, defense, home_adv, rho = unpack(theta)
            lam_h = np.exp(attack[h_idx] + defense[a_idx] + home_adv)
            lam_a = np.exp(attack[a_idx] + defense[h_idx])
            tau = self._tau_static(hg, ag, lam_h, lam_a, rho)
            base = -lam_h - lam_a + hg * np.log(np.clip(lam_h, _LOG_EPS, None)) + ag * np.log(
                np.clip(lam_a, _LOG_EPS, None)
            )
            return -float(np.sum(w * (base + np.log(np.clip(tau, 1e-12, None)))))

        result = minimize(
            neg_log_lik,
            theta0,
            method="L-BFGS-B",
            options={"maxiter": self.max_iterations, "ftol": self.convergence_tolerance},
        )
        attack, defense, home_adv, rho = unpack(result.x)
        self.attack = dict(zip(teams, attack.tolist(), strict=True))
        self.defense = dict(zip(teams, defense.tolist(), strict=True))
        self.home_adv = float(home_adv)
        self.rho = float(rho)
        self.converged = bool(result.success)
        self.iterations_used = int(result.nit)
        self.final_delta = float("nan")  # gradient-norm-based convergence, not IPF's delta metric
        self.joint_optimizer_diagnostics = {
            "optimizer": "scipy.optimize.minimize(L-BFGS-B)",
            "success": bool(result.success),
            "status": int(result.status),
            "message": str(result.message),
            "n_iter": int(result.nit),
            "objective": round(float(result.fun), 6),
        }
        if not self.converged and self.fail_on_non_convergence:
            raise RuntimeError(f"{self.model_id}: joint MLE did not converge: {result.message}")

        self._fitted = True
        self.diagnostics = {
            "n_teams": n,
            "training_rows": len(rows),
            "rows_missing_goals": skipped,
            "home_adv": round(self.home_adv, 4),
            "rho": round(self.rho, 4),
            "max_goals": self.max_goals,
            "decay_half_life_days": self.decay_half_life_days,
            "converged": self.converged,
            "iterations_used": self.iterations_used,
            "joint_optimizer": self.joint_optimizer_diagnostics,
            "warm_start_from": "dixon_coles (sequential IPF + bounded rho)",
        }
        return self

    @staticmethod
    def _tau_static(x, y, lam_h, lam_a, rho):
        t = np.ones_like(lam_h)
        m00 = (x == 0) & (y == 0)
        m01 = (x == 0) & (y == 1)
        m10 = (x == 1) & (y == 0)
        m11 = (x == 1) & (y == 1)
        t = np.where(m00, 1.0 - lam_h * lam_a * rho, t)
        t = np.where(m01, 1.0 + lam_h * rho, t)
        t = np.where(m10, 1.0 + lam_a * rho, t)
        t = np.where(m11, 1.0 - rho, t)
        return np.clip(t, 1e-6, None)


REGISTRY: dict[str, type[BaselineModel]] = {
    PoissonModel.model_id: PoissonModel,
    DixonColesModel.model_id: DixonColesModel,
    DixonColesJointMLE.model_id: DixonColesJointMLE,
}
