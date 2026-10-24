"""Sprint 4c (ADR 0071): models that are trained SEPARATELY for each competition, and a hierarchical global model whose variance components are learned.

`PerLeagueDC`: one ridge Poisson / Dixon-Coles model per competition, fitted on that competition's finished matches only. Its team penalty and its time decay are not typed in:
they are chosen for that competition by the nested walk-forward selection (`nested.select_dc`) on the competition's own history. A competition with fewer training matches than
`min_train_matches` is not trained (status LOCAL_INELIGIBLE) and the caller keeps the reference for it, visibly.

`HierarchicalDC`: team < country < global random effects plus competition terms (the Sprint 3 bridge model) with the variance components (the penalties, 1 / variance) selected by the
same nested walk-forward over the whole universe, instead of being fixed numbers. This is empirical Bayes by predictive cross-validation, not a marginal-likelihood estimate; the
limit is stated in ADR 0071.

Nothing here reads a result that is not history at the fold cutoff: `fit` receives training rows only."""

import hashlib
import json

import numpy as np

from src.evaluation.dataset import EvalRow

from .nested import dc_grid, inner_folds, make_model, select_dc
from .stat_models import DCSpec, MatrixPrediction, NotTrained, RidgePoissonDC


def grid_edges(raw_grid: dict, spec: DCSpec) -> list[str]:
    """Dimensions where the chosen value is the smallest or largest grid value: the optimum may lie outside the grid (reported, never hidden)."""
    out = []
    for k, vals in raw_grid.items():
        real = [v for v in vals if v is not None]
        v = getattr(spec, k)
        if v is not None and len(real) > 1 and v in (min(real), max(real)):
            out.append(f"{k}={v} is the {'smallest' if v == min(real) else 'largest'} grid value")
    return out


def _theta_digest(mod: RidgePoissonDC) -> str:
    return hashlib.sha256(json.dumps([mod.teams, [round(float(x), 8) for x in mod.theta], round(mod.rho, 8)]).encode("utf-8")).hexdigest()


TARGETS = ("1x2", "over_under", "btts")


def _stitch(n: int, parts: list[tuple[np.ndarray, MatrixPrediction]]) -> MatrixPrediction | None:
    if not parts:
        return None
    g = parts[0][1].matrices.shape[1]
    out = MatrixPrediction(np.zeros((n, g, g)), np.zeros(n), np.zeros(n), np.zeros(n, bool), np.zeros(n, bool), np.zeros(n), np.zeros(n))
    for idx, p in parts:
        for f in ("matrices", "lam_home", "lam_away", "seen_home", "seen_away", "tail_mass", "raw_sum"):
            getattr(out, f)[idx] = getattr(p, f)
    return out


class PerLeagueDC:
    """One separately trained model per competition AND per target. `info[competition]` documents every choice."""

    def __init__(self, s4c: dict, base: DCSpec, line: float, lag_hours: int) -> None:
        self.cfg, self.base, self.line, self.lag_hours = s4c["per_league_dc"], base, line, lag_hours
        self.nested_cfg = s4c["nested"]
        self.models: dict[tuple[str, str], RidgePoissonDC] = {}     # (competition, target) -> model
        self.info: dict[str, dict] = {}

    def fit(self, train: list[EvalRow]) -> "PerLeagueDC":
        grid = dc_grid(self.cfg["grid"], self.base)
        specs = dict(grid)
        for c in sorted({r.league_id for r in train}):
            rows = [r for r in train if r.league_id == c]
            if len(rows) < self.cfg["min_train_matches"]:
                self.info[c] = {"status": "LOCAL_INELIGIBLE", "training_matches": len(rows), "reason": f"{len(rows)} matches < {self.cfg['min_train_matches']}"}
                continue
            folds, dropped = inner_folds(rows, self.lag_hours, int(self.nested_cfg["inner_folds"]), int(self.nested_cfg["min_inner_fit_matches"]))
            if not folds:
                self.info[c] = {"status": "NOT_TRAINED", "training_matches": len(rows), "reason": "no inner fold with enough fit matches", "dropped_inner_folds": dropped}
                continue
            sel = select_dc(grid, folds, self.line)
            fitted: dict[str, RidgePoissonDC] = {}
            per_target: dict[str, dict] = {}
            for t in TARGETS:
                name = sel["chosen"][t]
                if name is None:
                    per_target[t] = {"status": "NOT_TRAINED", "reason": sel["status"]}
                    continue
                try:
                    if name not in fitted:
                        fitted[name] = RidgePoissonDC(specs[name], f"P_dc_sep_{c}").fit(rows)
                except NotTrained as e:
                    per_target[t] = {"status": "NOT_TRAINED", "reason": f"final fit: {e}", "chosen": name}
                    continue
                self.models[(c, t)] = fitted[name]
                spec = specs[name]
                per_target[t] = {"status": "TRAINED", "chosen": name, "chosen_mean_inner_loss": sel["chosen_mean_loss"][t], "grid_edge": grid_edges(self.cfg["grid"], spec),
                                 "rho": round(fitted[name].rho, 6), "parameter_digest": _theta_digest(fitted[name])}  # fmt: skip
            trained = [t for t in TARGETS if per_target[t]["status"] == "TRAINED"]
            self.info[c] = {"status": "TRAINED" if trained else "NOT_TRAINED", "training_matches": len(rows), "teams": len({x for r in rows for x in (r.home_id, r.away_id)}),
                            "per_target": per_target, "inner_folds_used": [f.season for f in folds], "dropped_inner_folds": dropped, "nested_table": sel["table"]}  # fmt: skip
        return self

    def predict(self, rows: list[EvalRow], target: str) -> tuple[MatrixPrediction | None, np.ndarray]:
        """(prediction aligned with `rows` for the spec chosen for `target`, covered mask). Uncovered rows hold zero matrices: the caller substitutes the reference for them."""
        n = len(rows)
        covered = np.zeros(n, bool)
        parts = []
        for (c, t), mod in self.models.items():
            if t != target:
                continue
            idx = np.array([i for i, r in enumerate(rows) if r.league_id == c], dtype=int)
            if len(idx):
                parts.append((idx, mod.predict([rows[i] for i in idx])))
                covered[idx] = True
        return _stitch(n, parts), covered

    def export(self) -> dict:
        out: dict = {}
        for (c, t), mod in sorted(self.models.items()):
            out.setdefault(c, {})[t] = {"info": self.info[c]["per_target"][t], "teams": mod.teams, "theta": [round(float(x), 8) for x in mod.theta], "rho": round(mod.rho, 8)}
        return out


class HierarchicalDC:
    """Global team < country < world model with learned variance components, selected per target by nested walk-forward."""

    def __init__(self, s4c: dict, base: DCSpec, line: float, lag_hours: int, bridge: bool = True, section: str = "hierarchical_dc") -> None:
        self.cfg, self.nested_cfg, self.base, self.line, self.lag_hours, self.bridge = s4c[section], s4c["nested"], base, line, lag_hours, bridge
        self.models: dict[str, RidgePoissonDC] = {}
        self.info: dict = {}

    def fit(self, train: list[EvalRow]) -> "HierarchicalDC":
        fixed = {"competition_terms": True, "country_levels": self.bridge}
        grid = dc_grid(self.cfg["grid"], self.base, **fixed)
        specs = dict(grid)
        folds, dropped = inner_folds(train, self.lag_hours, int(self.nested_cfg["inner_folds"]), int(self.nested_cfg["min_inner_fit_matches"]))
        if not folds:
            self.info = {"status": "NOT_TRAINED", "reason": "no inner fold with enough fit matches", "dropped_inner_folds": dropped}
            raise NotTrained(self.info["reason"])
        sel = select_dc(grid, folds, self.line)
        if not any(sel["chosen"].values()):
            self.info = {"status": "NOT_TRAINED", "reason": sel["status"], "nested": sel["table"]}
            raise NotTrained(sel["status"])
        fitted: dict[str, RidgePoissonDC] = {}
        per_target = {}
        for t in TARGETS:
            name = sel["chosen"][t]
            if name is None:
                raise NotTrained(f"{t}: no hierarchical candidate trained")
            if name not in fitted:
                fitted[name] = make_model(specs[name], "H_dc_hier").fit(train)
            self.models[t] = fitted[name]
            spec = specs[name]
            per_target[t] = {"chosen": name, "chosen_mean_inner_loss": sel["chosen_mean_loss"][t], "grid_edge": grid_edges(self.cfg["grid"], spec), "half_life_days": spec.half_life_days,
                             "variance_components": {"team": round(1.0 / spec.l2_team, 6), "country": round(1.0 / spec.l2_country, 6), "competition": round(1.0 / spec.l2_competition, 6), **({"season_walk": round(1.0 / spec.l2_walk, 6)} if hasattr(spec, "l2_walk") else {})},
                             "parameter_digest": _theta_digest(fitted[name])}  # fmt: skip
        self.info = {"status": "TRAINED", "bridge_country_levels": self.bridge, "per_target": per_target, "inner_folds_used": [f.season for f in folds], "dropped_inner_folds": dropped, "nested_table": sel["table"]}
        return self

    def predict(self, rows: list[EvalRow], target: str) -> MatrixPrediction:
        if target not in self.models:
            raise NotTrained("hierarchical model is not trained")
        return self.models[target].predict(rows)


# ------------------------------------------------------------------------------------------------ exact-score audit
def exact_score_metrics(matrices: np.ndarray, rows: list[EvalRow], top_k: int) -> dict:
    """Log score of the realised scoreline, top-1 and top-k hit rates for score matrices (n, G, G). A scoreline outside the grid has probability 0 and is counted, not hidden."""
    n, g, _ = matrices.shape
    hg = np.array([r.home_goals for r in rows])
    ag = np.array([r.away_goals for r in rows])
    inside = (hg < g) & (ag < g)
    p = np.where(inside, matrices[np.arange(n), np.minimum(hg, g - 1), np.minimum(ag, g - 1)], 0.0)
    flat = matrices.reshape(n, -1)
    order = np.argsort(-flat, axis=1)[:, :top_k]
    actual = np.minimum(hg, g - 1) * g + np.minimum(ag, g - 1)
    hit_k = ((order == actual[:, None]) & inside[:, None]).any(axis=1)
    hit_1 = (order[:, 0] == actual) & inside
    return {"n": int(n), "log_score": float(np.mean(-np.log(np.clip(p, 1e-12, 1.0)))), "top1_hit_rate": float(hit_1.mean()), f"top{top_k}_hit_rate": float(hit_k.mean()),
            "outside_grid": int((~inside).sum())}  # fmt: skip


def constant_scoreline_matrices(train: list[EvalRow], n: int, grid: int) -> np.ndarray:
    """The same add-one-smoothed empirical scoreline distribution of the training window for every row (the model-free exact-score baseline)."""
    counts = np.ones((grid, grid))
    for r in train:
        if r.home_goals < grid and r.away_goals < grid:
            counts[r.home_goals, r.away_goals] += 1
    return np.broadcast_to(counts / counts.sum(), (n, grid, grid)).copy()
