"""S5 Poisson / Dixon-Coles: scoreline normalization, identifiability, fit-on-train-only."""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from src.evaluation.dataset import EvalRow
from src.models import DixonColesModel, PoissonModel, build_models

T0 = datetime(2023, 8, 1, tzinfo=UTC)


def row(i, home, away, hg, ag, day=0):
    outcome = 0 if hg > ag else (1 if hg == ag else 2)
    return EvalRow(f"f{i}", "EPL", "2023-24", T0 + timedelta(days=day), home, away, outcome,
                    home_goals=hg, away_goals=ag)  # fmt: skip


def synthetic_league(n_rounds=6, seed=0):
    """Strong/weak teams with a deterministic RNG so attack/defense strengths are recoverable."""
    rng = np.random.default_rng(seed)
    teams = [f"T{i}" for i in range(6)]
    true_attack = {t: rng.uniform(-0.4, 0.4) for t in teams}
    rows, i = [], 0
    for rnd in range(n_rounds):
        shuffled = teams[:]
        rng.shuffle(shuffled)
        for h, a in zip(shuffled[::2], shuffled[1::2], strict=True):
            lam_h = np.exp(0.3 + true_attack[h] - true_attack[a])
            lam_a = np.exp(true_attack[a] - true_attack[h])
            hg = rng.poisson(lam_h)
            ag = rng.poisson(lam_a)
            rows.append(row(i, h, a, int(hg), int(ag), day=rnd))
            i += 1
    return rows


def test_scoreline_matrix_sums_to_one_and_is_non_negative():
    m = PoissonModel(max_goals=8).fit(synthetic_league())
    mat = m.scoreline_matrix(1.4, 1.1)
    assert mat.shape == (9, 9)
    assert np.isclose(mat.sum(), 1.0)
    assert (mat >= 0).all()


def test_1x2_probabilities_are_a_valid_simplex():
    train = synthetic_league()
    m = PoissonModel().fit(train)
    p = m.predict_proba(train[:5])
    assert p.shape == (5, 3)
    assert np.allclose(p.sum(axis=1), 1.0)
    assert (p >= 0).all()


def test_attack_is_recentered_for_identifiability():
    m = PoissonModel().fit(synthetic_league())
    assert abs(np.mean(list(m.attack.values()))) < 1e-6


def test_fit_requires_goal_counts_and_never_fabricates_them():
    no_goals = [
        EvalRow("f1", "EPL", "2023-24", T0, "H", "A", 0),
        EvalRow("f2", "EPL", "2023-24", T0, "A", "H", 2),
    ]
    with pytest.raises(ValueError, match="goal counts"):
        PoissonModel().fit(no_goals)


def test_unseen_team_falls_back_to_league_average_and_is_counted():
    train = synthetic_league()
    m = PoissonModel().fit(train)
    unseen = row(999, "GHOST_HOME", "GHOST_AWAY", 1, 1, day=99)
    p = m.predict_proba([unseen])
    assert np.isclose(p.sum(), 1.0)
    assert m.diagnostics["unseen_team_rows"] == 2


def test_predict_before_fit_raises():
    with pytest.raises(RuntimeError):
        PoissonModel().predict_proba([row(1, "H", "A", 1, 0)])


def test_dixon_coles_reduces_to_poisson_when_rho_is_zero():
    m = DixonColesModel()
    m.rho = 0.0
    tau = m._tau(np.array([0, 0, 1, 1, 2]), np.array([0, 1, 0, 1, 2]), np.array([1.2]), np.array([0.9]))
    assert np.allclose(tau, 1.0)


def test_dixon_coles_corrects_only_low_scorelines():
    m = DixonColesModel()
    m.rho = -0.1
    lam_h, lam_a = np.array([1.2]), np.array([0.9])
    low = m._tau(np.array([0]), np.array([0]), lam_h, lam_a)
    high = m._tau(np.array([3]), np.array([3]), lam_h, lam_a)
    assert not np.isclose(low[0], 1.0)
    assert np.isclose(high[0], 1.0)


def test_dixon_coles_fits_a_bounded_rho_and_still_normalizes():
    train = synthetic_league(n_rounds=10, seed=1)
    m = DixonColesModel(rho_grid=(-0.2, 0.2, 0.01)).fit(train)
    assert -0.2 <= m.rho <= 0.2
    p = m.predict_proba(train[:5])
    assert np.allclose(p.sum(axis=1), 1.0)
    assert "rho" in m.diagnostics


def test_model_card_metadata_and_config_recorded():
    m = PoissonModel(max_goals=6, ipf_sweeps=10).fit(synthetic_league())
    assert m.diagnostics["max_goals"] == 6
    assert m.diagnostics["training_rows"] > 0
    assert m.model_id == "poisson" and m.model_class == "statistical"
    dc = DixonColesModel().fit(synthetic_league())
    assert dc.model_id == "dixon_coles"


def test_build_models_wires_poisson_config():
    from src.config import PoissonConfig

    cfg = PoissonConfig(max_goals=5, ipf_sweeps=7)
    [poisson, dc] = build_models(["poisson", "dixon_coles"], poisson_config=cfg)
    assert poisson.max_goals == 5 and poisson.ipf_sweeps == 7
    assert dc.max_goals == 5 and dc.ipf_sweeps == 7
