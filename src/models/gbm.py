"""S6 — XGBoost / LightGBM multiclass 1X2 models: leakage-safe features, chronological
internal validation, Optuna tuning, early stopping, SHAP diagnostics, artifact reload.

Feature matrix: exactly `src.features.registry.produced_names()` — the S2 leakage-safe
feature contract, nothing else — plus each feature's own `_available` flag. Both boosters
route NaN natively as "missing"; the explicit flag additionally gives the model an
unambiguous "was this observed" signal, matching the project's no-silent-fallback policy.

Internal chronological split: the LAST `validation_fraction` of `fit(train)` (ordered by
kickoff_utc) is held out for Optuna's objective and early stopping. This is entirely inside
the season range `EvaluationContext` already cleared for training — it never touches the
outer validation/final-test seasons.

Optuna objective: the PRIMARY criterion Optuna optimizes is validation log loss. RPS is
computed every trial and recorded as a user attribute for secondary comparison/reporting —
it is never used to override the primary objective or to pick the best trial.

Raw (pre-calibration) probabilities are exactly what `predict_proba` returns — calibration is
S9 scope; `self.raw_probs_` retains the last call's output for that stage to reuse.

SHAP importances are diagnostic only (`diagnostics["top_shap_features"]`); they are never used
to select or prune the feature set.
"""

import numpy as np

from src.evaluation.dataset import EvalRow
from src.evaluation.metrics import log_loss, rps
from src.features.registry import AVAIL_SUFFIX, produced_names

from .baselines import BaselineModel

FEATURES: tuple[str, ...] = tuple(produced_names())
FEATURE_NAMES: list[str] = list(FEATURES) + [f"{n}{AVAIL_SUFFIX}" for n in FEATURES]
MIN_TRAIN_ROWS = 20


def _design_matrix(rows: list[EvalRow]) -> np.ndarray:
    n = len(rows)
    raw = np.full((n, len(FEATURES)), np.nan)
    avail = np.zeros((n, len(FEATURES)))
    for i, r in enumerate(rows):
        for j, name in enumerate(FEATURES):
            v = r.features.get(name)
            if v is not None:
                raw[i, j] = v
            a = r.features.get(name + AVAIL_SUFFIX)
            if a is not None:
                avail[i, j] = a
    return np.concatenate([raw, avail], axis=1)


def _chronological_split(rows: list[EvalRow], validation_fraction: float) -> tuple[list[int], list[int]]:
    ordered = sorted(range(len(rows)), key=lambda i: (rows[i].kickoff_utc, rows[i].fixture_id))
    n_val = int(len(ordered) * validation_fraction) if len(ordered) >= 10 else 0
    val_idx = set(ordered[len(ordered) - n_val :]) if n_val else set()
    fit_idx = [i for i in ordered if i not in val_idx]
    val_idx_list = [i for i in ordered if i in val_idx]
    return fit_idx, val_idx_list


class GBMModel(BaselineModel):
    """Common scaffolding for the two multiclass boosters; subclasses implement the booster
    calls only (`_default_params`, `_suggest_params`, `_fit_booster`, `_predict_booster`)."""

    model_class = "ml"
    required_features = FEATURES

    def __init__(
        self,
        seed: int = 42,
        n_optuna_trials: int = 8,
        validation_fraction: float = 0.15,
        early_stopping_rounds: int = 20,
    ) -> None:
        super().__init__()
        self.seed = seed
        self.n_optuna_trials = n_optuna_trials
        self.validation_fraction = validation_fraction
        self.early_stopping_rounds = early_stopping_rounds
        self.booster_ = None
        self.best_params_: dict = {}
        self.raw_probs_: np.ndarray | None = None
        self._fitted = False

    # ---- subclass hooks -----------------------------------------------------------
    def _default_params(self) -> dict:
        raise NotImplementedError

    def _suggest_params(self, trial) -> dict:
        raise NotImplementedError

    def _fit_booster(self, X_fit, y_fit, X_val, y_val, params: dict):
        raise NotImplementedError

    def _predict_booster(self, booster, X: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    def dump(self) -> bytes:
        raise NotImplementedError

    def load(self, data: bytes) -> None:
        raise NotImplementedError

    # ---- Optuna tuning (chronological internal split only) ------------------------
    def _tune(self, X_fit, y_fit, X_val, y_val) -> tuple[dict, dict]:
        base = self._default_params()
        if len(X_val) == 0 or self.n_optuna_trials <= 0:
            return base, {"best_log_loss": None, "best_rps": None, "n_trials_run": 0}

        import optuna

        optuna.logging.set_verbosity(optuna.logging.WARNING)
        study = optuna.create_study(
            direction="minimize", sampler=optuna.samplers.TPESampler(seed=self.seed)
        )

        def objective(trial):
            params = {**base, **self._suggest_params(trial)}
            booster = self._fit_booster(X_fit, y_fit, X_val, y_val, params)
            p = self._predict_booster(booster, X_val)
            ll = log_loss(p, y_val)
            trial.set_user_attr("rps", rps(p, y_val))  # secondary; never overrides log loss
            return ll

        study.optimize(objective, n_trials=self.n_optuna_trials, show_progress_bar=False)
        best = study.best_trial
        return {**base, **best.params}, {
            "best_log_loss": best.value,
            "best_rps": best.user_attrs.get("rps"),
            "n_trials_run": len(study.trials),
        }

    def _shap_summary(self, X: np.ndarray, top_n: int = 10) -> dict:
        try:
            import shap

            sample = X[: min(len(X), 300)]
            explainer = shap.TreeExplainer(self.booster_)
            sv = explainer.shap_values(sample)
            if isinstance(sv, list):  # legacy per-class list of (n, features)
                arr = np.mean([np.abs(s) for s in sv], axis=0)
            else:
                arr = np.abs(np.asarray(sv))
                if arr.ndim == 3:  # (n, features, classes) -> average over samples and classes
                    feat_axis = int(np.argmax([d == len(FEATURE_NAMES) for d in arr.shape]))
                    other_axes = tuple(i for i in range(3) if i != feat_axis)
                    arr = arr.mean(axis=other_axes)
                else:
                    arr = arr.mean(axis=0)
            order = np.argsort(-arr)[:top_n]
            return {FEATURE_NAMES[i]: round(float(arr[i]), 6) for i in order}
        except Exception as e:  # SHAP is diagnostic-only; it must never block a fit
            return {"unavailable": str(e)}

    # ---- BaselineModel interface ----------------------------------------------------
    def fit(self, train: list[EvalRow]) -> "GBMModel":
        rows = list(train)
        if len(rows) < MIN_TRAIN_ROWS:
            raise ValueError(f"{self.model_id}: needs at least {MIN_TRAIN_ROWS} rows, got {len(rows)}")
        X_all = _design_matrix(rows)
        y_all = np.array([r.outcome for r in rows])
        fit_idx, val_idx = _chronological_split(rows, self.validation_fraction)
        X_fit, y_fit = X_all[fit_idx], y_all[fit_idx]
        X_val, y_val = X_all[val_idx], y_all[val_idx]

        self.best_params_, tuning = self._tune(X_fit, y_fit, X_val, y_val)
        self.booster_ = self._fit_booster(X_fit, y_fit, X_val, y_val, self.best_params_)
        self._fitted = True

        kickoffs = [r.kickoff_utc for r in rows]
        self.diagnostics = {
            "training_rows": len(rows),
            "internal_fit_rows": len(fit_idx),
            "internal_val_rows": len(val_idx),
            "training_window": [min(kickoffs).isoformat(), max(kickoffs).isoformat()],
            "hyperparameters": {k: v for k, v in self.best_params_.items()},
            "n_optuna_trials": self.n_optuna_trials,
            "optuna_best_log_loss": tuning["best_log_loss"],
            "optuna_best_rps_secondary": tuning["best_rps"],
            "seed": self.seed,
            "n_features": len(FEATURE_NAMES),
            "top_shap_features": self._shap_summary(X_val if len(X_val) else X_fit),
        }
        return self

    def predict_proba(self, rows: list[EvalRow]) -> np.ndarray:
        if not self._fitted:
            raise RuntimeError(f"{self.model_id}.predict_proba called before fit()")
        X = _design_matrix(rows)
        p = self._predict_booster(self.booster_, X)
        self.raw_probs_ = p
        return p


class XGBModel(GBMModel):
    model_id = "xgboost"
    model_version = "1.0.0"

    def _default_params(self) -> dict:
        return {
            "objective": "multi:softprob",
            "num_class": 3,
            "eval_metric": "mlogloss",
            "seed": self.seed,
            "max_depth": 4,
            "eta": 0.1,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "min_child_weight": 1.0,
            "lambda": 1.0,
        }

    def _suggest_params(self, trial) -> dict:
        return {
            "max_depth": trial.suggest_int("max_depth", 2, 6),
            "eta": trial.suggest_float("eta", 0.01, 0.3, log=True),
            "subsample": trial.suggest_float("subsample", 0.6, 1.0),
            "colsample_bytree": trial.suggest_float("colsample_bytree", 0.6, 1.0),
            "min_child_weight": trial.suggest_float("min_child_weight", 0.5, 10.0, log=True),
            "lambda": trial.suggest_float("lambda", 0.1, 10.0, log=True),
        }

    def _fit_booster(self, X_fit, y_fit, X_val, y_val, params: dict):
        import xgboost as xgb

        dtrain = xgb.DMatrix(X_fit, label=y_fit, feature_names=FEATURE_NAMES, missing=np.nan)
        if len(X_val):
            dval = xgb.DMatrix(X_val, label=y_val, feature_names=FEATURE_NAMES, missing=np.nan)
            return xgb.train(
                params,
                dtrain,
                num_boost_round=500,
                evals=[(dval, "val")],
                early_stopping_rounds=self.early_stopping_rounds,
                verbose_eval=False,
            )
        return xgb.train(params, dtrain, num_boost_round=100, verbose_eval=False)

    def _predict_booster(self, booster, X: np.ndarray) -> np.ndarray:
        import xgboost as xgb

        dm = xgb.DMatrix(X, feature_names=FEATURE_NAMES, missing=np.nan)
        best_it = getattr(booster, "best_iteration", None)
        if best_it is not None:
            return booster.predict(dm, iteration_range=(0, best_it + 1))
        return booster.predict(dm)

    def dump(self) -> bytes:
        return bytes(self.booster_.save_raw("json"))

    def load(self, data: bytes) -> None:
        import xgboost as xgb

        b = xgb.Booster()
        b.load_model(bytearray(data))
        self.booster_ = b
        self._fitted = True


class LGBMModel(GBMModel):
    model_id = "lightgbm"
    model_version = "1.0.0"

    def _default_params(self) -> dict:
        return {
            "objective": "multiclass",
            "num_class": 3,
            "metric": "multi_logloss",
            "seed": self.seed,
            "verbosity": -1,
            "num_leaves": 15,
            "learning_rate": 0.1,
            "feature_fraction": 0.8,
            "bagging_fraction": 0.8,
            "bagging_freq": 1,
            "min_child_samples": 10,
        }

    def _suggest_params(self, trial) -> dict:
        return {
            "num_leaves": trial.suggest_int("num_leaves", 7, 63),
            "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
            "feature_fraction": trial.suggest_float("feature_fraction", 0.6, 1.0),
            "bagging_fraction": trial.suggest_float("bagging_fraction", 0.6, 1.0),
            "min_child_samples": trial.suggest_int("min_child_samples", 5, 50),
        }

    def _fit_booster(self, X_fit, y_fit, X_val, y_val, params: dict):
        import lightgbm as lgb

        train_set = lgb.Dataset(X_fit, label=y_fit, feature_name=FEATURE_NAMES)
        if len(X_val):
            val_set = lgb.Dataset(X_val, label=y_val, feature_name=FEATURE_NAMES, reference=train_set)
            return lgb.train(
                params,
                train_set,
                num_boost_round=500,
                valid_sets=[val_set],
                callbacks=[
                    lgb.early_stopping(self.early_stopping_rounds, verbose=False),
                    lgb.log_evaluation(0),
                ],
            )
        return lgb.train(params, train_set, num_boost_round=100)

    def _predict_booster(self, booster, X: np.ndarray) -> np.ndarray:
        best_it = getattr(booster, "best_iteration", None)
        if best_it:
            return booster.predict(X, num_iteration=best_it)
        return booster.predict(X)

    def dump(self) -> bytes:
        return self.booster_.model_to_string().encode("utf-8")

    def load(self, data: bytes) -> None:
        import lightgbm as lgb

        self.booster_ = lgb.Booster(model_str=data.decode("utf-8"))
        self._fitted = True


REGISTRY: dict[str, type[BaselineModel]] = {
    XGBModel.model_id: XGBModel,
    LGBMModel.model_id: LGBMModel,
}
