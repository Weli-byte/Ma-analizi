"""S5 Poisson / Dixon-Coles: scoreline normalization, identifiability, fit-on-train-only."""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from src.evaluation.dataset import EvalRow
from src.models import DixonColesJointMLE, DixonColesModel, PoissonModel, build_models

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
    m = DixonColesModel(rho_bounds=(-0.2, 0.2)).fit(train)
    assert -0.2 <= m.rho <= 0.2
    p = m.predict_proba(train[:5])
    assert np.allclose(p.sum(axis=1), 1.0)
    assert "rho" in m.diagnostics


def test_model_card_metadata_and_config_recorded():
    m = PoissonModel(max_goals=6, max_iterations=10).fit(synthetic_league())
    assert m.diagnostics["max_goals"] == 6
    assert m.diagnostics["training_rows"] > 0
    assert m.model_id == "poisson" and m.model_class == "statistical"
    dc = DixonColesModel().fit(synthetic_league())
    assert dc.model_id == "dixon_coles"


def test_build_models_wires_poisson_config():
    from src.config import PoissonConfig

    cfg = PoissonConfig(max_goals=5, max_iterations=7)
    [poisson, dc] = build_models(["poisson", "dixon_coles"], poisson_config=cfg)
    assert poisson.max_goals == 5 and poisson.max_iterations == 7
    assert dc.max_goals == 5 and dc.max_iterations == 7


# ---------------------------------------------- S0-S7 hardening Phase 7: IPF convergence
def test_ipf_reports_convergence_status_and_never_hides_it():
    m = PoissonModel().fit(synthetic_league())
    assert m.diagnostics["converged"] is True
    assert m.diagnostics["iterations_used"] <= m.diagnostics["max_iterations"]
    assert m.diagnostics["final_delta"] < m.diagnostics["convergence_tolerance"]


def test_ipf_converges_immediately_on_a_trivial_one_match_dataset():
    train = [row(1, "A", "B", 1, 0)]
    m = PoissonModel(max_iterations=200).fit(train)
    assert m.diagnostics["converged"] is True
    assert m.diagnostics["iterations_used"] < 200


def test_ipf_non_convergence_is_reported_not_silently_accepted():
    """A max_iterations of 1 is very unlikely to satisfy a tight tolerance on real data --
    diagnostics must say so, and fail_on_non_convergence must actually raise."""
    train = synthetic_league(n_rounds=10, seed=2)
    m = PoissonModel(max_iterations=1, convergence_tolerance=1e-12).fit(train)
    assert m.diagnostics["converged"] is False
    assert m.diagnostics["iterations_used"] == 1
    with pytest.raises(RuntimeError, match="did not converge"):
        PoissonModel(max_iterations=1, convergence_tolerance=1e-12, fail_on_non_convergence=True).fit(train)


def test_ipf_replay_is_deterministic():
    train = synthetic_league(n_rounds=8, seed=3)
    a = PoissonModel().fit(train)
    b = PoissonModel().fit(train)
    assert a.attack == b.attack and a.defense == b.defense
    assert a.diagnostics["iterations_used"] == b.diagnostics["iterations_used"]


# --------------------------------------------------------- S0-S7 hardening Phase 7: decay
def test_no_decay_by_default_matches_undecayed_fit():
    train = synthetic_league(n_rounds=6, seed=4)
    plain = PoissonModel().fit(train)
    explicit_none = PoissonModel(decay_half_life_days=None).fit(train)
    assert plain.attack == explicit_none.attack


def test_decay_changes_the_fit_and_is_deterministic():
    train = synthetic_league(n_rounds=10, seed=5)
    decayed_a = PoissonModel(decay_half_life_days=30.0).fit(train)
    decayed_b = PoissonModel(decay_half_life_days=30.0).fit(train)
    undecayed = PoissonModel().fit(train)
    assert decayed_a.attack == decayed_b.attack  # deterministic given the same data
    assert decayed_a.attack != undecayed.attack  # decay actually changed the fit


# ----------------------------------------------- S0-S7 hardening Phase 7: rho optimizer
def test_rho_optimizer_diagnostics_recorded():
    m = DixonColesModel().fit(synthetic_league(seed=6))
    d = m.diagnostics["rho_optimizer"]
    assert d["optimizer"] == "scipy.optimize.minimize_scalar(bounded)"
    assert "success" in d and "n_iter" in d


# ---------------------------------------------- S0-S7 hardening Phase 7: tail mass
def test_scoreline_tail_mass_is_measured():
    m = PoissonModel(max_goals=8).fit(synthetic_league())
    mat = m.scoreline_matrix(1.2, 0.9)
    assert mat.shape == (9, 9)
    assert 0.0 <= m.last_tail_mass <= 1.0
    assert m.last_captured_mass + m.last_tail_mass == pytest.approx(1.0)


def test_tail_mass_warns_when_max_goals_is_too_small_for_large_lambda():
    m = PoissonModel(max_goals=2, tail_mass_warn_threshold=0.01).fit(synthetic_league())
    huge = row(999, "GHOST_HOME", "GHOST_AWAY", 1, 1, day=99)
    # unseen teams -> lambda ~ exp(home_adv), still exercise the warning path defensively
    with pytest.warns(UserWarning, match="tail mass"):
        m.predict_proba([huge] * 1 + [row(1000, "GHOST3", "GHOST4", 1, 1, day=100)])


def test_tail_mass_diagnostic_present_after_predict():
    m = PoissonModel().fit(synthetic_league())
    m.predict_proba(synthetic_league(seed=7)[:5])
    assert "max_tail_mass" in m.diagnostics


# --------------------------------------------- S0-S7 hardening Phase 7: joint-MLE DC variant
def test_joint_mle_is_not_the_default_registry_pick():
    from src.models import REGISTRY, default_baselines

    assert DixonColesJointMLE.model_id in REGISTRY
    assert DixonColesJointMLE.model_id not in {m.model_id for m in default_baselines()}


def test_joint_mle_fits_and_normalizes():
    train = synthetic_league(n_rounds=10, seed=8)
    m = DixonColesJointMLE(max_iterations=50).fit(train)
    assert m.model_id == "dixon_coles_v2_joint_mle"
    p = m.predict_proba(train[:5])
    assert np.allclose(p.sum(axis=1), 1.0)
    assert (p >= 0).all()
    assert "joint_optimizer" in m.diagnostics


def test_joint_mle_is_deterministic():
    train = synthetic_league(n_rounds=8, seed=9)
    a = DixonColesJointMLE(max_iterations=50).fit(train)
    b = DixonColesJointMLE(max_iterations=50).fit(train)
    assert a.attack == b.attack and a.rho == b.rho


def test_joint_mle_never_reads_beyond_its_own_train_rows():
    train = synthetic_league(n_rounds=8, seed=10)  # 8 rounds x 3 matches = 24 rows
    m = DixonColesJointMLE(max_iterations=30).fit(train[:20])
    assert m.diagnostics["training_rows"] == 20
