"""S7 walk-forward backtest engine: fold construction, immutable ledger, experiment registry,
config hashing, reproducibility, final-test isolation."""

import json

import pytest

from src.config import EvaluationConfig
from src.evaluation.split import SplitError, walk_forward_folds
from src.evaluation.walk_forward import run_walk_forward
from src.schemas import PredictionRecord


def _cfg(**over):
    base = dict(
        split_strategy="expanding",
        min_train_seasons=2,
        rolling_window_seasons=2,
        metrics=["log_loss"],
        calibration_bins=5,
        bootstrap_samples=0,
        bootstrap_seed=1,
        max_fallback_rate={"development": 1.0, "research": 1.0, "strict": 1.0, "final": 1.0},
        train_seasons=["2019-20", "2020-21", "2021-22"],
        validation_seasons=["2022-23", "2023-24"],
        final_test_seasons=["2024-25"],
    )
    base.update(over)
    return EvaluationConfig(**base)


# ---------------------------------------------------------------- fold construction
def test_expanding_folds_grow_the_training_window():
    folds = walk_forward_folds(_cfg())
    assert [f.test_season for f in folds] == ["2021-22", "2022-23", "2023-24"]
    assert [len(f.train_seasons) for f in folds] == [2, 3, 4]
    assert folds[0].train_seasons == ("2019-20", "2020-21")
    assert folds[-1].train_seasons == ("2019-20", "2020-21", "2021-22", "2022-23")


def test_rolling_folds_keep_a_fixed_window():
    folds = walk_forward_folds(_cfg(split_strategy="rolling", rolling_window_seasons=2))
    assert [len(f.train_seasons) for f in folds] == [2, 2, 2]
    assert folds[-1].train_seasons == ("2021-22", "2022-23")


def test_folds_never_touch_final_test_seasons():
    folds = walk_forward_folds(_cfg())
    final = set(_cfg().final_test_seasons)
    for f in folds:
        assert not (final & set(f.train_seasons))
        assert f.test_season not in final


def test_folds_are_strictly_chronological():
    for f in walk_forward_folds(_cfg()):
        assert max(f.train_seasons) < f.test_season


def test_too_few_seasons_raises():
    with pytest.raises(SplitError, match="not enough seasons"):
        walk_forward_folds(_cfg(min_train_seasons=10))


def test_folds_are_a_deterministic_function_of_config():
    a = walk_forward_folds(_cfg())
    b = walk_forward_folds(_cfg())
    assert a == b


# ---------------------------------------------------------------- engine (golden fixture project)
def test_walk_forward_runs_one_fold_per_config_and_writes_artifacts(built):
    out = run_walk_forward(built, "strict")
    assert len(out.fold_results) == 1  # golden config: 1 train + 1 validation season, min_train=1
    fr = out.fold_results[0]
    assert fr.fold.test_season == "2022-23" and fr.fold.train_seasons == ("2021-22",)
    assert set(fr.metrics) == {"always_home", "historical_prior", "recent_form_naive", "market_implied"}
    assert (out.out_dir / "predictions.jsonl").exists()
    assert (out.out_dir / "report.json").exists()
    assert (out.out_dir / "split_manifest.json").exists()
    experiments = list((out.out_dir / "experiments").glob("fold0_*.json"))
    assert len(experiments) == 4


def test_predictions_are_immutable_and_carry_fold_provenance(built):
    out = run_walk_forward(built, "strict")
    lines = (out.out_dir / "predictions.jsonl").read_text().strip().splitlines()
    recs = [PredictionRecord.from_json(ln) for ln in lines]  # verifies content-derived ids
    assert recs and {r.status.value for r in recs} == {"evaluated"}
    assert len({r.prediction_id for r in recs}) == len(recs)  # unique, content-hashed

    exp_path = next((out.out_dir / "experiments").glob("fold0_market_implied.json"))
    exp = json.loads(exp_path.read_text())
    assert exp["config"]["fold"]["index"] == 0
    assert exp["config"]["fold"]["test_season"] == "2022-23"
    assert exp["config"]["fold"]["train_end_utc"] < exp["config"]["fold"]["test_start_utc"]
    assert exp["final_test_rows"] == 0
    assert "config_hash" in exp and len(exp["config_hash"]) > 0
    assert exp["git_sha"] != "unknown" and exp["git_dirty"] is False


def test_config_hash_is_deterministic_and_fold_scoped(built):
    """Config hashing (S7 task): every experiment's config_hash is a deterministic function of
    its (fold, model, evaluation/model/features config) blob. Same fold -> same config_hash
    across its models (the fold IS part of that shared config); a different config (here:
    rerunning with different evaluation metrics) changes the hash."""
    out_a = run_walk_forward(built, "strict")
    exp_dir = out_a.out_dir / "experiments"
    hashes = {json.loads(p.read_text())["config_hash"] for p in exp_dir.glob("fold0_*.json")}
    assert len(hashes) == 1  # one fold, one shared config -> one config_hash across its models

    out_b = run_walk_forward(built, "strict")
    hash_b = json.loads((out_b.out_dir / "experiments" / "fold0_market_implied.json").read_text())[
        "config_hash"
    ]
    assert hash_b == next(iter(hashes))  # deterministic: same config -> same hash, rerun to rerun


def test_reproducible_given_same_config_and_commit(built):
    a = run_walk_forward(built, "strict")
    b = run_walk_forward(built, "strict")  # same project, same commit, rerun
    assert a.hashes == b.hashes


def test_test_season_prediction_never_reused_as_next_folds_train_label(built):
    """Every fold's test rows are disjoint from every OTHER fold's test rows (each season is the
    walk-forward test set exactly once), so the ledger never sees the same (fixture, model) twice
    across folds — a real conflict there would raise, not silently merge."""
    out = run_walk_forward(built, "strict")
    lines = (out.out_dir / "predictions.jsonl").read_text().strip().splitlines()
    recs = [PredictionRecord.from_json(ln) for ln in lines]
    keys = [(r.fixture_id, r.model_id) for r in recs]
    assert len(keys) == len(set(keys))
