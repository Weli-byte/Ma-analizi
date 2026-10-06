"""S0-S7 hardening Phase 7: Elo hyperparameter tuning via temporal (walk-forward) validation."""

from datetime import UTC, datetime, timedelta

import pytest

from src.config import EloConfig, EvaluationConfig
from src.evaluation.dataset import EvalRow
from src.models.elo_tuning import FoldEvalResult, render_report, tune_elo

T0 = datetime(2019, 8, 1, tzinfo=UTC)


class _FakeFeatures:
    rows: dict = {}
    reasons: dict = {}


def _eval_cfg(**over):
    base = dict(
        split_strategy="expanding",
        min_train_seasons=2,
        rolling_window_seasons=2,
        metrics=["log_loss"],
        calibration_bins=5,
        bootstrap_samples=0,
        bootstrap_seed=1,
        max_fallback_rate={"development": 1.0, "research": 1.0, "strict": 1.0, "final": 1.0},
        train_seasons=["2019-20", "2020-21"],
        validation_seasons=["2021-22", "2022-23"],
        final_test_seasons=["2023-24"],
    )
    base.update(over)
    return EvaluationConfig(**base)


class _FakeRef:
    data_version = "dv-test"


def _rows_for(season_index: int, n: int = 20):
    base = T0 + timedelta(days=365 * season_index)
    teams = [f"T{i}" for i in range(6)]
    rows = []
    for i in range(n):
        h, a = teams[i % 6], teams[(i + 1) % 6]
        rows.append(EvalRow(f"s{season_index}f{i}", "EPL", f"season{season_index}",
                             base + timedelta(days=i), h, a, i % 3))  # fmt: skip
    return rows


SEASON_ROWS = {
    "2019-20": _rows_for(0),
    "2020-21": _rows_for(1),
    "2021-22": _rows_for(2),
    "2022-23": _rows_for(3),
}


def _fake_load_rows(ref, ctx, seasons, feats):
    out = []
    for s in seasons:
        out += SEASON_ROWS[s]
    return out


@pytest.fixture(autouse=True)
def _patch_load_rows(monkeypatch):
    import src.models.elo_tuning as mod

    monkeypatch.setattr(mod, "load_rows", _fake_load_rows)


def test_tune_elo_never_touches_final_test_seasons():
    cfg = _eval_cfg()
    rep = tune_elo(cfg, EloConfig(tuning={"n_trials": 0}), _FakeRef(), _FakeFeatures())
    all_test_seasons = {r.test_season for r in rep.baseline_folds} | {r.test_season for r in rep.tuned_folds}
    assert "2023-24" not in all_test_seasons  # the final-test season never appears as a fold


def test_tune_elo_zero_trials_uses_baseline_as_tuned():
    cfg = _eval_cfg()
    elo_cfg = EloConfig(k_factor=17.0, home_advantage=55.0, tuning={"n_trials": 0})
    rep = tune_elo(cfg, elo_cfg, _FakeRef(), _FakeFeatures())
    assert rep.tuned_params == rep.baseline_params
    assert rep.n_trials_run == 0


def test_tune_elo_runs_identical_folds_for_baseline_and_tuned():
    cfg = _eval_cfg()
    elo_cfg = EloConfig(tuning={"n_trials": 3, "seed": 1})
    rep = tune_elo(cfg, elo_cfg, _FakeRef(), _FakeFeatures())
    assert [f.test_season for f in rep.baseline_folds] == [f.test_season for f in rep.tuned_folds]
    assert rep.n_trials_run == 3


def test_tune_elo_selection_period_matches_folds():
    cfg = _eval_cfg()
    rep = tune_elo(cfg, EloConfig(tuning={"n_trials": 0}), _FakeRef(), _FakeFeatures())
    assert rep.selection_period == ("2019-20", "2022-23")


def test_tune_elo_rejects_unsupported_method_or_objective():
    cfg = _eval_cfg()
    with pytest.raises(ValueError, match="method"):
        tune_elo(cfg, EloConfig(tuning={"method": "grid", "n_trials": 0}), _FakeRef(), _FakeFeatures())
    with pytest.raises(ValueError, match="objective"):
        tune_elo(
            cfg, EloConfig(tuning={"objective": "accuracy", "n_trials": 0}), _FakeRef(), _FakeFeatures()
        )


def test_report_renders_per_fold_rows_never_only_the_mean():
    cfg = _eval_cfg()
    rep = tune_elo(cfg, EloConfig(tuning={"n_trials": 0}), _FakeRef(), _FakeFeatures())
    text = render_report(rep)
    assert "Per-fold (baseline)" in text and "Per-fold (tuned)" in text
    for f in rep.baseline_folds:
        assert f.test_season in text


def test_fold_eval_result_is_a_plain_record():
    r = FoldEvalResult(0, ("2019-20",), "2020-21", 1.05, 12)
    assert r.log_loss == 1.05 and r.n == 12
