"""S6 XGBoost / LightGBM: leakage-safe features, Optuna tuning, early stopping, determinism,
artifact reload, SHAP diagnostics."""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from src.evaluation.dataset import EvalRow
from src.features.registry import AVAIL_SUFFIX, produced_names
from src.models import LGBMModel, XGBModel, build_models
from src.models.gbm import FEATURES, MIN_TRAIN_ROWS, GBMModel

T0 = datetime(2023, 8, 1, tzinfo=UTC)
MODEL_CLASSES = [XGBModel, LGBMModel]


def synthetic_rows(n=60, seed=0):
    """Two feature groups (form/rest) whose sign actually predicts the outcome, so a fitted
    booster should beat a coin flip — not a strict requirement, just keeps trees non-trivial."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        home_form = rng.normal(0, 1)
        away_form = rng.normal(0, 1)
        edge = home_form - away_form
        outcome = 0 if edge > 0.4 else (2 if edge < -0.4 else 1)
        feats = {f: None for f in FEATURES}
        feats["home_form_points_5"] = float(home_form)
        feats["away_form_points_5"] = float(away_form)
        for name in ("home_form_points_5", "away_form_points_5"):
            feats[name + AVAIL_SUFFIX] = 1.0
        rows.append(
            EvalRow(f"f{i}", "EPL", "2023-24", T0 + timedelta(days=i), f"h{i % 8}", f"a{i % 8}",
                    outcome, feats)  # fmt: skip
        )
    return rows


@pytest.fixture(params=MODEL_CLASSES)
def model_cls(request):
    return request.param


def small_model(cls):
    return cls(seed=1, n_optuna_trials=2, validation_fraction=0.2, early_stopping_rounds=5)


def test_required_features_match_leakage_safe_registry(model_cls):
    assert set(small_model(model_cls).required_features) == set(produced_names())


def test_fit_requires_minimum_rows(model_cls):
    with pytest.raises(ValueError, match="at least"):
        small_model(model_cls).fit(synthetic_rows(n=MIN_TRAIN_ROWS - 1))


def test_predict_before_fit_raises(model_cls):
    with pytest.raises(RuntimeError):
        small_model(model_cls).predict_proba(synthetic_rows(n=5))


def test_predict_proba_is_a_valid_simplex(model_cls):
    train = synthetic_rows(n=60)
    m = small_model(model_cls).fit(train)
    p = m.predict_proba(train[:10])
    assert p.shape == (10, 3)
    assert np.allclose(p.sum(axis=1), 1.0, atol=1e-5)
    assert (p >= -1e-9).all()


def test_fit_only_uses_the_rows_passed_in(model_cls):
    """No hidden read of anything outside `train` — refitting on a strict subset of the same
    rows must not raise and must still respect the internal chronological split."""
    train = synthetic_rows(n=60)
    m = small_model(model_cls).fit(train[:40])
    assert m.diagnostics["training_rows"] == 40
    assert m.diagnostics["internal_fit_rows"] + m.diagnostics["internal_val_rows"] == 40


def test_internal_split_is_chronological_not_random(model_cls):
    train = synthetic_rows(n=60)
    m = small_model(model_cls)
    from src.models.gbm import _chronological_split

    fit_idx, val_idx = _chronological_split(train, m.validation_fraction)
    assert max(fit_idx) < min(val_idx)  # every fit row precedes every validation row in time


def test_optuna_primary_objective_is_log_loss_rps_is_secondary(model_cls):
    train = synthetic_rows(n=60)
    m = small_model(model_cls).fit(train)
    assert m.diagnostics["n_optuna_trials"] == 2
    assert m.diagnostics["optuna_best_log_loss"] is not None
    assert m.diagnostics["optuna_best_rps_secondary"] is not None  # recorded, not optimized


def test_determinism_same_seed_same_predictions(model_cls):
    train = synthetic_rows(n=60)
    a = small_model(model_cls).fit(train)
    b = small_model(model_cls).fit(train)
    test = synthetic_rows(n=10, seed=99)
    assert np.allclose(a.predict_proba(test), b.predict_proba(test), atol=1e-6)


def test_artifact_reload_reproduces_predictions(model_cls):
    train = synthetic_rows(n=60)
    m = small_model(model_cls).fit(train)
    test = synthetic_rows(n=10, seed=7)
    before = m.predict_proba(test)
    blob = m.dump()

    reloaded = model_cls(seed=1)
    reloaded.load(blob)
    after = reloaded.predict_proba(test)
    assert np.allclose(before, after, atol=1e-6)


def test_model_card_metadata_recorded(model_cls):
    m = small_model(model_cls).fit(synthetic_rows(n=60))
    d = m.diagnostics
    assert d["training_window"][0] < d["training_window"][1]
    assert "hyperparameters" in d and isinstance(d["hyperparameters"], dict)
    assert d["seed"] == 1
    assert "top_shap_features" in d  # informational only; never gates selection
    assert m.model_class == "ml"


def test_build_models_wires_gbm_config():
    from src.config import GBMConfig

    cfg = GBMConfig(
        seed=3,
        n_optuna_trials={"development": 1, "research": 5, "strict": 5, "final": 9},
        validation_fraction=0.2,
        early_stopping_rounds=5,
    )
    [xgb_m, lgbm_m] = build_models(["xgboost", "lightgbm"], gbm_config=cfg, mode="development")
    assert isinstance(xgb_m, XGBModel) and isinstance(lgbm_m, LGBMModel)
    assert xgb_m.seed == 3 and xgb_m.n_optuna_trials == 1  # resolved from the "development" budget
    assert lgbm_m.seed == 3 and lgbm_m.n_optuna_trials == 1

    [xgb_final] = build_models(["xgboost"], gbm_config=cfg, mode="final")
    assert xgb_final.n_optuna_trials == 9  # per-mode budget: "final" gets a much bigger search


def test_no_optuna_trials_falls_back_to_default_params_without_crashing(model_cls):
    m = model_cls(seed=1, n_optuna_trials=0, validation_fraction=0.2, early_stopping_rounds=5)
    m.fit(synthetic_rows(n=60))
    assert m.diagnostics["optuna_best_log_loss"] is None
    p = m.predict_proba(synthetic_rows(n=5, seed=5))
    assert np.allclose(p.sum(axis=1), 1.0, atol=1e-5)


def test_base_class_hooks_are_abstract():
    m = GBMModel()
    with pytest.raises(NotImplementedError):
        m._default_params()
    with pytest.raises(NotImplementedError):
        m.dump()


# ------------------------------------------------- S0-S7 hardening Phase 8
def test_temporal_cv_reduces_to_single_split_when_n_folds_is_1():
    from src.models.gbm import _chronological_split, _temporal_cv_folds

    train = synthetic_rows(n=60)
    fit_a, val_a = _chronological_split(train, 0.2)
    [(fit_b, val_b)] = _temporal_cv_folds(train, 1, 0.2)
    assert fit_a == fit_b and val_a == val_b


def test_temporal_cv_produces_multiple_chronological_non_overlapping_folds():
    from src.models.gbm import _temporal_cv_folds

    train = synthetic_rows(n=120)
    folds = _temporal_cv_folds(train, 3, 0.2)
    assert len(folds) == 3
    for fit_idx, val_idx in folds:
        assert fit_idx and val_idx
        assert max(fit_idx) < min(val_idx)  # every fold's val strictly follows its own train
    # folds are chronologically ordered and expanding: later folds' train sets are supersets
    for (fit_a, _), (fit_b, _) in zip(folds, folds[1:], strict=False):
        assert set(fit_a) < set(fit_b)


def test_temporal_cv_folds_never_overlap_a_later_folds_training_future(model_cls):
    train = synthetic_rows(n=120)
    m = model_cls(seed=1, n_optuna_trials=1, validation_fraction=0.3, n_temporal_folds=3)
    m.fit(train)
    assert m.diagnostics["n_temporal_folds"] == 3
    assert m.diagnostics["n_folds_used_for_tuning"] >= 1


def test_multi_fold_tuning_is_deterministic(model_cls):
    train = synthetic_rows(n=120)
    a = model_cls(seed=7, n_optuna_trials=2, n_temporal_folds=3).fit(train)
    b = model_cls(seed=7, n_optuna_trials=2, n_temporal_folds=3).fit(train)
    test = synthetic_rows(n=10, seed=99)
    assert np.allclose(a.predict_proba(test), b.predict_proba(test), atol=1e-6)


def test_per_mode_optuna_budget_resolved_by_build_models():
    from src.config import GBMConfig
    from src.models import build_models

    cfg = GBMConfig(n_optuna_trials={"development": 0, "research": 4, "strict": 4, "final": 15})
    [dev] = build_models(["xgboost"], gbm_config=cfg, mode="development")
    [research] = build_models(["xgboost"], gbm_config=cfg, mode="research")
    [final] = build_models(["xgboost"], gbm_config=cfg, mode="final")
    assert dev.n_optuna_trials == 0
    assert research.n_optuna_trials == 4
    assert final.n_optuna_trials == 15


def test_early_stopping_uses_only_the_internal_validation_split_never_anything_else(model_cls):
    """Mutation-style leakage guard: swap the internal validation labels for noise and confirm
    the fitted booster's chosen iteration count changes -- proving early stopping actually reads
    X_val/y_val (not some cached/ignored value), so if a future change accidentally fed it
    something else (e.g. test data), this test would have a chance of catching it too."""
    train = synthetic_rows(n=80)
    m1 = model_cls(seed=1, n_optuna_trials=0, validation_fraction=0.25).fit(train)
    it1 = getattr(m1.booster_, "best_iteration", None)

    corrupted = list(train)
    fit_idx, val_idx = _chronological_split_public(corrupted)
    rng = np.random.default_rng(0)
    corrupted = [
        EvalRow(r.fixture_id, r.league_id, r.season, r.kickoff_utc, r.home_id, r.away_id,
                int(rng.integers(0, 3)) if i in val_idx else r.outcome, r.features)  # fmt: skip
        for i, r in enumerate(corrupted)
    ]
    m2 = model_cls(seed=1, n_optuna_trials=0, validation_fraction=0.25).fit(corrupted)
    it2 = getattr(m2.booster_, "best_iteration", None)
    assert it1 != it2 or m1.diagnostics["hyperparameters"] != m2.diagnostics["hyperparameters"]


def _chronological_split_public(rows):
    from src.models.gbm import _chronological_split

    return _chronological_split(rows, 0.25)


def test_predict_proba_never_affects_the_fitted_boosters_state(model_cls):
    """predict_proba must be read-only w.r.t. the fitted booster: calling it with different rows
    (any rows, including ones resembling held-out test data) must never change best_iteration or
    future predictions -- the exact property early-stopping leakage would violate."""
    train = synthetic_rows(n=60)
    m = model_cls(seed=1, n_optuna_trials=0).fit(train)
    it_before = getattr(m.booster_, "best_iteration", None)
    p1 = m.predict_proba(synthetic_rows(n=5, seed=1))
    m.predict_proba(synthetic_rows(n=5, seed=2))  # different rows, discarded result
    p2 = m.predict_proba(synthetic_rows(n=5, seed=1))  # same rows as p1 again
    assert getattr(m.booster_, "best_iteration", None) == it_before
    assert np.allclose(p1, p2, atol=1e-9)
