"""S4 Elo: leakage-safe rating updates, idempotency, replay determinism, 1X2 mapping."""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from src.evaluation.dataset import EvalRow
from src.models import EloModel, build_models

T0 = datetime(2023, 8, 1, tzinfo=UTC)


def row(i, outcome, home="H", away="A", day=0):
    return EvalRow(f"f{i}", "EPL", "2023-24", T0 + timedelta(days=day), home, away, outcome)


def test_prediction_uses_only_pre_match_rating():
    """Home team wins repeatedly; the SECOND match's prediction must not reflect the first
    match's outcome having already been folded into the rating used to predict it."""
    m = EloModel(k_factor=32.0, home_advantage=0.0)
    r1 = row(1, 0, "H", "A", day=0)  # home win
    diff_before = m._pre_match_diff(r1)
    assert diff_before == 0.0  # both teams start at initial_rating
    m.fit([r1])
    assert m.ratings["H"] > m.ratings["A"]  # only updated AFTER the result


def test_idempotent_update_never_applies_twice():
    m = EloModel()
    r = row(1, 0, "H", "A")
    m.fit([r])
    rating_after_fit = dict(m.ratings)
    m._apply_result(r, m._pre_match_diff(r))  # same fixture again
    assert m.ratings == rating_after_fit
    assert len(m.history) == 1


def test_replay_is_deterministic():
    rows = [row(i, i % 3, f"H{i}", f"A{i}", day=i) for i in range(12)]
    a, b = EloModel(), EloModel()
    a.fit(rows)
    b.fit(rows)
    assert a.ratings == b.ratings
    assert [e.home_rating_post for e in a.history] == [e.home_rating_post for e in b.history]


def test_rating_history_is_timestamped_and_ordered():
    rows = [row(i, 0, f"H{i}", f"A{i}", day=i) for i in range(5)]
    m = EloModel().fit(rows)
    assert len(m.history) == 5
    timestamps = [e.kickoff_utc for e in m.history]
    assert timestamps == sorted(timestamps)


def test_home_advantage_and_k_factor_shift_ratings():
    strong_adv = EloModel(k_factor=20.0, home_advantage=200.0).fit([row(1, 1, "H", "A")])
    no_adv = EloModel(k_factor=20.0, home_advantage=0.0).fit([row(1, 1, "H", "A")])
    # a draw against a big home-advantage prior is a bigger surprise for the home team
    assert strong_adv.ratings["H"] < no_adv.ratings["H"]


def test_predict_proba_returns_valid_simplex_and_updates_state():
    train = [row(i, i % 3, f"H{i}", f"A{i}", day=i) for i in range(20)]
    m = EloModel().fit(train)
    updates_before = len(m.history)
    test_rows = [row(100 + i, i % 3, "TH", "TA", day=100 + i) for i in range(4)]
    p = m.predict_proba(test_rows)
    assert p.shape == (4, 3)
    assert np.allclose(p.sum(axis=1), 1.0)
    assert (p >= 0).all()
    assert len(m.history) == updates_before + 4  # replay advanced state on the eval rows too


def test_predict_proba_before_fit_raises():
    with pytest.raises(RuntimeError):
        EloModel().predict_proba([row(1, 0)])


def test_config_and_diagnostics_are_recorded():
    m = EloModel(initial_rating=1600.0, k_factor=24.0, home_advantage=50.0)
    m.fit([row(i, i % 3, f"H{i}", f"A{i}", day=i) for i in range(10)])
    assert m.diagnostics["k_factor"] == 24.0
    assert m.diagnostics["home_advantage"] == 50.0
    assert m.diagnostics["use_margin_of_victory"] is False
    assert m.model_id == "elo" and m.model_version == "1.1.0"


def test_build_models_wires_elo_config():
    from src.config import EloConfig

    cfg = EloConfig(initial_rating=1400.0, k_factor=15.0, home_advantage=40.0)
    [m] = build_models(["elo"], cfg)
    assert isinstance(m, EloModel)
    assert m.initial_rating == 1400.0 and m.k_factor == 15.0 and m.home_advantage == 40.0


# ------------------------------------------------- S0-S7 hardening Phase 6: scipy optimizer
def test_optimizer_converges_and_reports_diagnostics():
    rows = [row(i, i % 3, f"H{i}", f"A{i}", day=i) for i in range(30)]
    m = EloModel().fit(rows)
    d = m.optimizer_diagnostics
    assert d.optimizer == "scipy.optimize.minimize(BFGS)"
    assert d.success is True
    assert d.n_iter > 0
    assert d.tolerance == EloModel.OPT_TOLERANCE
    assert len(d.initial_params) == 3 and len(d.final_params) == 3
    assert "optimizer" in m.diagnostics and m.diagnostics["optimizer"]["success"] is True


def test_optimizer_is_deterministic():
    rows = [row(i, i % 3, f"H{i}", f"A{i}", day=i) for i in range(30)]
    a = EloModel().fit(rows)
    b = EloModel().fit(rows)
    assert a.optimizer_diagnostics.final_params == b.optimizer_diagnostics.final_params
    assert a.optimizer_diagnostics.n_iter == b.optimizer_diagnostics.n_iter


def test_never_silently_treats_unconverged_fit_as_success():
    """Convergence status is always inspectable, whatever it is -- fit() must not raise or hide
    a non-converged result, but it must be visible in diagnostics for a caller to check."""
    m = EloModel().fit([row(1, 1, "H", "A")])  # 1 row: a degenerate, likely-unconverged fit
    assert isinstance(m.optimizer_diagnostics.success, bool)  # never None/hidden
    assert m.diagnostics["optimizer"]["success"] == m.optimizer_diagnostics.success


# ------------------------------------------------------------- S0-S7 hardening Phase 6: decay
def test_no_decay_by_default():
    m = EloModel()
    assert m.decay_half_life_days is None
    rows = [row(1, 0, "H", "A", day=0), row(2, 0, "H", "A", day=1000)]
    m.fit(rows)  # large gap, no decay configured -> same as if gap were small
    undec = EloModel().fit(rows)
    assert m.ratings == undec.ratings


def test_decay_pulls_stale_ratings_toward_initial():
    m = EloModel(k_factor=32.0, home_advantage=0.0, decay_half_life_days=10.0)
    m.fit([row(1, 0, "H", "A", day=0)])  # H builds up a rating advantage
    rating_fresh = m._decayed_rating("H", T0 + timedelta(days=0))
    rating_after_long_gap = m._decayed_rating("H", T0 + timedelta(days=1000))
    assert rating_after_long_gap < rating_fresh
    assert abs(rating_after_long_gap - m.initial_rating) < abs(rating_fresh - m.initial_rating)


def test_decay_is_a_half_life_curve():
    m = EloModel(decay_half_life_days=30.0)
    m.ratings["H"] = 1700.0
    m._last_seen["H"] = T0
    halfway = m._decayed_rating("H", T0 + timedelta(days=30))
    assert halfway == pytest.approx(m.initial_rating + (1700.0 - m.initial_rating) * 0.5)


def test_decay_never_applies_to_a_team_with_no_history():
    m = EloModel(decay_half_life_days=10.0)
    assert m._decayed_rating("NEW_TEAM", T0) == m.initial_rating


def test_decay_is_leakage_safe_and_deterministic():
    rows = [row(i, i % 3, f"H{i % 4}", f"A{i % 4}", day=i * 10) for i in range(20)]
    a = EloModel(decay_half_life_days=90.0).fit(rows)
    b = EloModel(decay_half_life_days=90.0).fit(rows)
    assert a.ratings == b.ratings


# -------------------------------------------------- S0-S7 hardening Phase 6/9: MOV infra
def test_mov_infra_uses_named_features_and_stays_inert_without_them():
    m = EloModel(use_margin_of_victory=True)
    r = row(1, 0, "H", "A")  # no goal_difference/margin_of_victory_available in features
    m.fit([r])
    assert m.diagnostics["mov_unavailable_rows"] == 1  # counted, never silently ignored


def test_mov_activates_when_features_present():
    feats_available = {"goal_difference": 3.0, "margin_of_victory_available": True}
    r1 = EvalRow("f1", "EPL", "2023-24", T0, "H", "A", 0, feats_available)
    m_mov = EloModel(use_margin_of_victory=True, k_factor=32.0, home_advantage=0.0).fit([r1])
    m_plain = EloModel(use_margin_of_victory=False, k_factor=32.0, home_advantage=0.0).fit([r1])
    assert m_mov.diagnostics["mov_unavailable_rows"] == 0
    assert m_mov.ratings["H"] != m_plain.ratings["H"]  # MOV scaling actually changed the update
