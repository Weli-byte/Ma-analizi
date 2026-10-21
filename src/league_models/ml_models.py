"""Sprint 4 (ADR 0066): gradient-boosting wrappers, the inner-validation tuner, local-model eligibility and the shrinkage blend.

Everything here is deterministic on a given machine (single thread, fixed seeds, `deterministic=True`) and runs on CPU only.
`NotTrainedML` is the explicit failure of a fit; callers record it and fall back visibly, nothing is defaulted silently.
The tuner receives the inner-fit and inner-validation blocks and nothing else: it cannot see the eval season of the fold."""

import hashlib
import time
from dataclasses import dataclass

import numpy as np
import yaml

from . import bench_metrics as bm
from .competitions import ROOT

ML_CONFIG = ROOT / "configs" / "league_models" / "ml_models.yaml"
TARGETS = ("1x2", "over_under", "btts")
OK = "TRAINED"


class NotTrainedML(RuntimeError):
    """A fit that cannot be trusted (one class, too few rows, non-finite output). The caller falls back explicitly."""


@dataclass(frozen=True)
class MLConfig:
    raw: dict
    fingerprint: str


def load_ml_config(path=ML_CONFIG) -> MLConfig:
    text = path.read_text(encoding="utf-8")
    return MLConfig(yaml.safe_load(text), hashlib.sha256(text.encode("utf-8")).hexdigest())


def log_loss(target: str, p: np.ndarray, y: np.ndarray) -> float:
    rows = bm.rows_log_loss_1x2(p, y) if target == "1x2" else bm.binary_log_loss_rows(p, y)
    return float(np.mean(rows))


def n_classes(target: str) -> int:
    return 3 if target == "1x2" else 2


def check_trainable(target: str, y: np.ndarray, min_minority: int = 2) -> None:
    counts = np.bincount(np.asarray(y, dtype=int), minlength=n_classes(target))
    if len(y) == 0 or (counts == 0).any():
        raise NotTrainedML(f"{target}: a class has no training example (counts {counts.tolist()}): one-class fold")
    if counts.min() < min_minority:
        raise NotTrainedML(f"{target}: the rarest class has {int(counts.min())} examples < {min_minority}")


def class_weights(target: str, y: np.ndarray, mode: str) -> np.ndarray | None:
    if mode == "none":
        return None
    if mode != "balanced":
        raise ValueError(f"unknown class_weight {mode!r}")
    counts = np.bincount(y, minlength=n_classes(target)).astype(float)
    w = len(y) / (len(counts) * np.maximum(counts, 1.0))
    return w[y]


class GBM:
    """One boosted-tree model for one target. `backend` is lightgbm or xgboost. Columns are chosen by `cols` (a feature subset of the
    shared schema), so a global and a local model read the same columns in the same order."""

    def __init__(self, backend: str, target: str, params: dict, names: list[str], categorical: list[str], seed: int, threads: int = 1, class_weight: str = "none") -> None:
        if backend not in ("lightgbm", "xgboost"):
            raise ValueError(f"unknown backend {backend!r}")
        self.backend, self.target, self.params = backend, target, dict(params)
        self.names, self.categorical = list(names), [c for c in categorical if c in names]
        self.seed, self.threads, self.class_weight = seed, threads, class_weight
        self.booster = None
        self.best_iteration = 0
        self.status = "NOT_TRAINED"

    # -- parameter translation
    def _lgb_params(self) -> dict:
        k = n_classes(self.target)
        p = {**self.params, "objective": "multiclass" if k == 3 else "binary", "metric": "multi_logloss" if k == 3 else "binary_logloss",
             "seed": self.seed, "bagging_seed": self.seed, "feature_fraction_seed": self.seed, "data_random_seed": self.seed,
             "deterministic": True, "force_col_wise": True, "num_threads": self.threads, "verbose": -1}  # fmt: skip
        if k == 3:
            p["num_class"] = 3
        return p

    def _xgb_params(self) -> dict:
        k = n_classes(self.target)
        p = {**self.params, "objective": "multi:softprob" if k == 3 else "binary:logistic", "eval_metric": "mlogloss" if k == 3 else "logloss",
             "tree_method": "hist", "nthread": self.threads, "seed": self.seed, "verbosity": 0}  # fmt: skip
        if k == 3:
            p["num_class"] = 3
        return p

    # -- fitting
    def fit(self, X: np.ndarray, y: np.ndarray, rounds: int, valid: tuple[np.ndarray, np.ndarray] | None = None, early_stopping: int | None = None) -> "GBM":
        y = np.asarray(y, dtype=int)
        check_trainable(self.target, y)
        w = class_weights(self.target, y, self.class_weight)
        if self.backend == "lightgbm":
            import lightgbm as lgb

            dtrain = lgb.Dataset(X, label=y, weight=w, feature_name=self.names, categorical_feature=self.categorical or "auto", free_raw_data=False)
            kwargs = {}
            if valid is not None and early_stopping:
                dval = lgb.Dataset(valid[0], label=np.asarray(valid[1], dtype=int), reference=dtrain, feature_name=self.names,
                                   categorical_feature=self.categorical or "auto", free_raw_data=False)  # fmt: skip
                kwargs = {"valid_sets": [dval], "callbacks": [lgb.early_stopping(early_stopping, verbose=False)]}
            self.booster = lgb.train(self._lgb_params(), dtrain, num_boost_round=rounds, **kwargs)
            self.best_iteration = int(self.booster.best_iteration or self.booster.current_iteration())
        else:
            import xgboost as xgb

            dtrain = xgb.DMatrix(X, label=y, weight=w, missing=np.nan, feature_names=self.names)
            kwargs = {}
            if valid is not None and early_stopping:
                dval = xgb.DMatrix(valid[0], label=np.asarray(valid[1], dtype=int), missing=np.nan, feature_names=self.names)
                kwargs = {"evals": [(dval, "val")], "early_stopping_rounds": early_stopping, "verbose_eval": False}
            self.booster = xgb.train(self._xgb_params(), dtrain, num_boost_round=rounds, **kwargs)
            self.best_iteration = int(getattr(self.booster, "best_iteration", rounds - 1)) + 1 if kwargs else rounds
        self.status = OK
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if self.status != OK:
            raise NotTrainedML(f"{self.backend}/{self.target} is {self.status}")
        if self.backend == "lightgbm":
            p = self.booster.predict(X, num_iteration=self.best_iteration)
        else:
            import xgboost as xgb

            p = self.booster.predict(xgb.DMatrix(X, missing=np.nan, feature_names=self.names), iteration_range=(0, self.best_iteration))
        p = np.asarray(p, dtype=float)
        if not np.isfinite(p).all():
            raise NotTrainedML("non-finite probability from the booster")
        if p.ndim == 2:
            p = np.clip(p, 1e-12, None)
            return p / p.sum(axis=1, keepdims=True)
        return np.clip(p, 1e-12, 1.0 - 1e-12)

    def model_text(self) -> str:
        if self.backend == "lightgbm":
            return self.booster.model_to_string(num_iteration=self.best_iteration)
        return bytes(self.booster.save_raw(raw_format="json")).decode("utf-8")

    def gain_by_feature(self) -> dict[str, float]:
        if self.backend == "lightgbm":
            g = self.booster.feature_importance(importance_type="gain")
            return {n: float(v) for n, v in zip(self.names, g, strict=True)}
        sc = self.booster.get_score(importance_type="total_gain")
        return {n: float(sc.get(n, 0.0)) for n in self.names}


# ---------------------------------------------------------------------------------------------------- tuning
def tune(mcfg: MLConfig, target: str, Xfit, yfit, Xval, yval, names: list[str], categorical: list[str], seed: int) -> dict:
    """Optuna (TPE, seeded) on the inner walk-forward block: fit on the earlier seasons, score on the last training season. Returns the
    best parameters, the early-stopped round count and EVERY trial. The caller must not pass anything from the eval season."""
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    t = mcfg.raw["tuning"]
    space, g = t["space"], mcfg.raw["gbm"]
    digits = int(t["objective_rounding_digits"])
    records: list[dict] = []

    def objective(trial) -> float:
        params = {**g["defaults"], **{k: trial.suggest_categorical(k, v) for k, v in space.items()}}
        model = GBM("lightgbm", target, params, names, categorical, seed, g["threads"], g["class_weight"])
        t0 = time.perf_counter()
        try:
            model.fit(Xfit, yfit, t["max_rounds"], valid=(Xval, yval), early_stopping=t["early_stopping_rounds"])
            value = round(log_loss(target, model.predict_proba(Xval), yval), digits)
            state = "COMPLETE"
        except NotTrainedML as e:  # a trial that cannot train is recorded and scores infinity, it is never skipped silently
            value, state = float("inf"), f"FAILED: {e}"
        records.append({"trial": trial.number, "params": {k: params[k] for k in space}, "validation_log_loss": value,
                        "best_iteration": model.best_iteration, "state": state, "fit_seconds_excluded_from_hashes": time.perf_counter() - t0})  # fmt: skip
        return value if np.isfinite(value) else 1e9

    study = optuna.create_study(direction="minimize", sampler=optuna.samplers.TPESampler(seed=seed, n_startup_trials=int(t["n_startup_trials"])))
    t0 = time.perf_counter()
    study.optimize(objective, n_trials=int(t["max_trials"]), timeout=float(t["wallclock_budget_seconds"]), show_progress_bar=False)
    elapsed = time.perf_counter() - t0
    complete = [r for r in records if r["state"] == "COMPLETE"]
    if not complete:
        raise NotTrainedML(f"{target}: no tuning trial trained")
    best = min(complete, key=lambda r: (r["validation_log_loss"], r["trial"]))
    return {"target": target, "best_params": best["params"], "best_trial": best["trial"], "best_iteration": best["best_iteration"],
            "best_validation_log_loss": best["validation_log_loss"], "trials_run": len(records), "max_trials": int(t["max_trials"]),
            "stopped_by": "max_trials" if len(records) >= int(t["max_trials"]) else "wallclock (NOT reproducible)",
            "sampler": f"TPE seed {seed}", "objective": "validation log loss on the inner block (last training season)",
            "fit_rows": int(len(yfit)), "validation_rows": int(len(yval)), "wallclock_seconds_excluded_from_hashes": elapsed,
            "trials": [{k: v for k, v in r.items() if k != "fit_seconds_excluded_from_hashes"} for r in records]}  # fmt: skip


def full_params(mcfg: MLConfig, tuned: dict | None) -> dict:
    return {**mcfg.raw["gbm"]["defaults"], **(tuned or {})}


# ---------------------------------------------------------------------------------------------------- local eligibility and blend
def local_eligibility(mcfg: MLConfig, n_train: int, y_by_target: dict[str, np.ndarray]) -> tuple[bool, str]:
    """Plan tier table: under `min_train_matches` there is no independent league model; a one-class / near one-class target also blocks it."""
    loc = mcfg.raw["local"]
    if n_train < loc["min_train_matches"]:
        return False, f"LOCAL_INELIGIBLE: {n_train} training matches < {loc['min_train_matches']}"
    for target, y in y_by_target.items():
        counts = np.bincount(np.asarray(y, dtype=int), minlength=n_classes(target))
        if counts.min() < loc["min_minority_class"]:
            return False, f"LOCAL_INELIGIBLE: {target} has a class with {int(counts.min())} examples < {loc['min_minority_class']}"
    return True, "ELIGIBLE" if n_train >= loc["proof_required_below"] else f"ELIGIBLE_EVIDENCE_ONLY: {n_train} < {loc['proof_required_below']} matches (proof required)"


def blend_weight(mcfg: MLConfig, target: str, p_global: np.ndarray, p_local: np.ndarray, y: np.ndarray) -> dict:
    """w for p = (1 - w) * p_global + w * p_local from OUT-OF-FIT predictions of the inner validation block: grid minimum of the log loss,
    required to beat the global model by `min_improvement`, then shrunk by n / (n + k). Little data or no gain gives w = 0."""
    b = mcfg.raw["blend"]
    n = int(len(y))
    out = {"validation_rows": n}
    if n < b["min_validation_matches"]:
        return {**out, "weight": 0.0, "reason": f"{n} validation matches < {b['min_validation_matches']}: weight 0"}
    grid = np.arange(0.0, 1.0 + 1e-9, b["weight_grid_step"])
    losses = [log_loss(target, (1 - w) * p_global + w * p_local, y) for w in grid]
    best = float(grid[int(np.argmin(losses))])
    gain = losses[0] - min(losses)
    out.update({"best_raw_weight": best, "validation_log_loss_global": losses[0], "validation_log_loss_local": losses[-1],
                "validation_log_loss_at_best": float(min(losses)), "validation_gain": float(gain)})  # fmt: skip
    if gain < b["min_improvement"]:
        return {**out, "weight": 0.0, "reason": f"blend gain {gain:.4f} < {b['min_improvement']}: weight 0"}
    return {**out, "weight": best * n / (n + b["shrink_k"]), "reason": "LEARNED"}


def mix(p_global: np.ndarray, p_local: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Row-wise convex combination: stays on the simplex (1X2) or in [0, 1] (binary)."""
    w = np.asarray(w, dtype=float)
    ww = w[:, None] if p_global.ndim == 2 else w
    return (1.0 - ww) * p_global + ww * p_local
