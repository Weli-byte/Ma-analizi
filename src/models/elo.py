"""S4 — leakage-safe sequential Elo rating with an ordinal-logit 1X2 mapping.
S0-S7 hardening Phase 6 (ADR-0013 amendment): scipy-based convergence-tracked optimizer,
optional time decay, MOV availability infrastructure.

Rating updates happen only AFTER a fixture's result is known, and always use the rating that
was current BEFORE that fixture (pre-match). Each fixture updates the two teams' ratings
exactly once, tracked by fixture_id, so replaying the same chronologically-ordered sequence
always reproduces an identical rating history (the "replay" determinism S4 requires).

The rating difference (home - away + home_advantage) is mapped to 1X2 probabilities via a
3-outcome ordinal logistic ("proportional odds") model fitted once on the training replay
only — evaluation outcomes never influence the mapping or the ratings used to predict them.
"""

from dataclasses import dataclass
from datetime import datetime

import numpy as np
from scipy.optimize import minimize

from src.evaluation.dataset import EvalRow

from .baselines import BaselineModel

OUTCOME_TO_HOME_SCORE = {0: 1.0, 1: 0.5, 2: 0.0}  # H, D, A -> home's Elo "actual score"


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -60.0, 60.0)))


@dataclass(frozen=True)
class RatingEvent:
    """One timestamped rating update, for audit and replay-determinism tests."""

    fixture_id: str
    kickoff_utc: str
    home_id: str
    away_id: str
    home_rating_pre: float
    away_rating_pre: float
    home_rating_post: float
    away_rating_post: float


@dataclass(frozen=True)
class OptimizerDiagnostics:
    """scipy.optimize.minimize's own convergence report — never silently treat an unconverged
    fit as success (S0-S7 hardening Phase 6)."""

    optimizer: str
    success: bool
    status: int
    message: str
    n_iter: int
    objective: float
    tolerance: float
    initial_params: tuple[float, float, float]
    final_params: tuple[float, float, float]


class EloModel(BaselineModel):
    """Team-strength Elo: initial rating, home advantage, configurable K, optional time decay
    (rating regresses toward `initial_rating` between a team's matches, older information fading
    over calendar time), optional margin-of-victory K-scaling (off by default — no goal-margin
    feature exists yet, and this model never fabricates one), full rating history, idempotent
    updates."""

    model_id = "elo"
    model_version = "1.1.0"  # 1.1.0: scipy optimizer + decay (Phase 6); rating formula unchanged
    model_class = "statistical"

    def __init__(
        self,
        initial_rating: float = 1500.0,
        k_factor: float = 20.0,
        home_advantage: float = 60.0,
        use_margin_of_victory: bool = False,
        decay_half_life_days: float | None = None,
    ) -> None:
        super().__init__()
        self.initial_rating = initial_rating
        self.k_factor = k_factor
        self.home_advantage = home_advantage
        self.use_margin_of_victory = use_margin_of_victory
        self.decay_half_life_days = decay_half_life_days
        self.ratings: dict[str, float] = {}
        self._last_seen: dict[str, datetime] = {}
        self.history: list[RatingEvent] = []
        self._processed: set[str] = set()
        self._mov_unavailable = 0
        # ordinal-logit mapping parameters (act on diff/ELO_SCALE); overwritten by fit()
        self._beta = 1.0
        self._theta1 = -0.5
        self._theta2 = 0.5
        self._fitted = False
        self.optimizer_diagnostics: OptimizerDiagnostics | None = None

    # ---- rating engine ---------------------------------------------------------------
    def _decayed_rating(self, team: str, at: datetime) -> float:
        raw = self.ratings.get(team, self.initial_rating)
        if self.decay_half_life_days is None or team not in self._last_seen:
            return raw
        days = (at - self._last_seen[team]).total_seconds() / 86400.0
        if days <= 0:
            return raw
        weight = 0.5 ** (days / self.decay_half_life_days)  # exp(-ln(2)/half_life * age), i.e. a
        return self.initial_rating + (raw - self.initial_rating) * weight  # half-life decay curve

    def _pre_match_diff(self, row: EvalRow) -> float:
        rh = self._decayed_rating(row.home_id, row.kickoff_utc)
        ra = self._decayed_rating(row.away_id, row.kickoff_utc)
        return rh - ra + self.home_advantage

    def _apply_result(self, row: EvalRow, diff: float) -> None:
        if row.fixture_id in self._processed:  # idempotent: never update the same fixture twice
            return
        rh = self._decayed_rating(row.home_id, row.kickoff_utc)
        ra = self._decayed_rating(row.away_id, row.kickoff_utc)
        expected_home = 1.0 / (1.0 + 10 ** (-diff / 400.0))
        s_home = OUTCOME_TO_HOME_SCORE[row.outcome]
        k = self.k_factor
        if self.use_margin_of_victory:
            goal_difference = row.features.get("goal_difference")
            margin_of_victory_available = row.features.get("margin_of_victory_available")
            if not margin_of_victory_available or goal_difference is None:
                self._mov_unavailable += 1  # counted, never silently ignored
            else:
                k = k * np.log(abs(goal_difference) + 1.0) * (2.2 / (abs(rh - ra) * 0.001 + 2.2))
        delta = k * (s_home - expected_home)
        new_rh, new_ra = rh + delta, ra - delta
        self.ratings[row.home_id] = new_rh
        self.ratings[row.away_id] = new_ra
        self._last_seen[row.home_id] = row.kickoff_utc
        self._last_seen[row.away_id] = row.kickoff_utc
        self.history.append(
            RatingEvent(
                row.fixture_id,
                row.kickoff_utc.isoformat(),
                row.home_id,
                row.away_id,
                rh,
                ra,
                new_rh,
                new_ra,
            )
        )
        self._processed.add(row.fixture_id)

    def replay(self, rows: list[EvalRow]) -> list[float]:
        """Process rows strictly in the given (chronological) order: compute each row's
        pre-match diff for prediction, then update ratings from that row's own outcome."""
        diffs = []
        for r in rows:
            diff = self._pre_match_diff(r)
            diffs.append(diff)
            self._apply_result(r, diff)
        return diffs

    # ---- ordinal-logit 1X2 mapping ---------------------------------------------------
    ELO_SCALE = 400.0  # standard Elo normalisation; keeps the optimizer's x well-scaled
    OPT_TOLERANCE = 1e-9
    OPT_MAX_ITER = 500

    def _fit_outcome_mapping(self, diffs: np.ndarray, outcomes: np.ndarray) -> None:
        """3-outcome proportional-odds fit via scipy.optimize.minimize (BFGS) on the negative
        log-likelihood. y_ord: away < draw < home. theta2 = theta1 + softplus(gap) keeps
        theta1 < theta2. Convergence is checked and recorded, never assumed
        (`self.optimizer_diagnostics`); an unconverged fit is used as-is but flagged, not hidden."""
        x = diffs / self.ELO_SCALE
        y_home = (outcomes == 0).astype(float)
        y_draw = (outcomes == 1).astype(float)
        y_away = (outcomes == 2).astype(float)

        def neg_log_lik(params: np.ndarray) -> float:
            beta, theta1, gap = params
            theta2 = theta1 + np.logaddexp(0.0, gap)
            z1, z2 = theta1 - beta * x, theta2 - beta * x
            s1, s2 = _sigmoid(z1), _sigmoid(z2)
            p_home = np.clip(1.0 - s2, 1e-12, None)
            p_draw = np.clip(s2 - s1, 1e-12, None)
            p_away = np.clip(s1, 1e-12, None)
            ll = np.sum(y_home * np.log(p_home) + y_draw * np.log(p_draw) + y_away * np.log(p_away))
            return -float(ll)

        x0 = np.array([self._beta, self._theta1, 1.0])
        result = minimize(
            neg_log_lik,
            x0,
            method="BFGS",
            options={"gtol": self.OPT_TOLERANCE, "maxiter": self.OPT_MAX_ITER},
        )
        beta, theta1, gap = result.x
        self._beta = float(beta)
        self._theta1 = float(theta1)
        self._theta2 = float(theta1 + np.logaddexp(0.0, gap))
        self.optimizer_diagnostics = OptimizerDiagnostics(
            optimizer="scipy.optimize.minimize(BFGS)",
            success=bool(result.success),
            status=int(result.status),
            message=str(result.message),
            n_iter=int(result.nit),
            objective=float(result.fun),
            tolerance=self.OPT_TOLERANCE,
            initial_params=tuple(float(v) for v in x0),
            final_params=(self._beta, self._theta1, float(gap)),
        )

    def _map_probs(self, diffs: np.ndarray) -> np.ndarray:
        x = diffs / self.ELO_SCALE
        z1 = self._theta1 - self._beta * x
        z2 = self._theta2 - self._beta * x
        s1, s2 = _sigmoid(z1), _sigmoid(z2)
        p_away, p_draw, p_home = s1, np.clip(s2 - s1, 0.0, None), 1.0 - s2
        out = np.stack([p_home, p_draw, p_away], axis=1)
        return out / out.sum(axis=1, keepdims=True)

    # ---- BaselineModel interface ------------------------------------------------------
    def fit(self, train: list[EvalRow]) -> "EloModel":
        ordered = sorted(train, key=lambda r: (r.kickoff_utc, r.fixture_id))
        diffs = np.array(self.replay(ordered))
        outcomes = np.array([r.outcome for r in ordered])
        self._fit_outcome_mapping(diffs, outcomes)  # convergence status always in diagnostics below
        self._fitted = True
        self.diagnostics = {
            "teams_rated": len(self.ratings),
            "training_updates": len(self.history),
            "beta": round(self._beta, 6),
            "theta1": round(self._theta1, 4),
            "theta2": round(self._theta2, 4),
            "k_factor": self.k_factor,
            "home_advantage": self.home_advantage,
            "use_margin_of_victory": self.use_margin_of_victory,
            "mov_unavailable_rows": self._mov_unavailable,
            "decay_half_life_days": self.decay_half_life_days,
            "optimizer": {
                "name": self.optimizer_diagnostics.optimizer,
                "success": self.optimizer_diagnostics.success,
                "status": self.optimizer_diagnostics.status,
                "message": self.optimizer_diagnostics.message,
                "n_iter": self.optimizer_diagnostics.n_iter,
                "objective": round(self.optimizer_diagnostics.objective, 6),
                "tolerance": self.optimizer_diagnostics.tolerance,
            },
        }
        return self

    def predict_proba(self, rows: list[EvalRow]) -> np.ndarray:
        """Rows must be in chronological order (as load_rows guarantees). Predicts each row
        from the pre-match state, then advances the rating state with that row's own outcome —
        call once per evaluation; a repeated call recomputes diffs from the already-advanced
        state, which is intentionally out of scope here (the runner calls it exactly once)."""
        if not self._fitted:
            raise RuntimeError("EloModel.predict_proba called before fit()")
        diffs = np.array(self.replay(rows))
        return self._map_probs(diffs)


REGISTRY: dict[str, type[BaselineModel]] = {EloModel.model_id: EloModel}
