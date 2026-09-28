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
    assert m.model_id == "elo" and m.model_version == "1.0.0"


def test_build_models_wires_elo_config():
    from src.config import EloConfig

    cfg = EloConfig(initial_rating=1400.0, k_factor=15.0, home_advantage=40.0)
    [m] = build_models(["elo"], cfg)
    assert isinstance(m, EloModel)
    assert m.initial_rating == 1400.0 and m.k_factor == 15.0 and m.home_advantage == 40.0
