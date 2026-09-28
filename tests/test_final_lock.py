import json
import os

import pytest
from conftest import build_all, make_project

from src.config import load_config
from src.data.dataset import resolve_dataset
from src.evaluation import run_baselines as rb
from src.evaluation.context import (
    EvalMode,
    EvaluationContext,
    FinalTestAccessError,
    make_context,
)
from src.evaluation.dataset import load_rows
from src.evaluation.final import FinalAlreadyRun, run_final_evaluation, unlock_final
from src.evaluation.split import SplitError, build_split_manifest, walk_forward_folds
from src.features.artifact import load_features
from src.provenance import ProvenanceError
from src.runmode import RunMode

NON_FINAL_MODES = [m for m in EvalMode if m != EvalMode.FINAL]


@pytest.fixture
def eval_cfg(project):
    return load_config("evaluation", project / "configs")


# ------------------------------------------------------------------- the guard itself
@pytest.mark.parametrize("mode", NON_FINAL_MODES)
def test_no_ordinary_mode_can_read_final_test_seasons(eval_cfg, mode):
    ctx = make_context(mode, eval_cfg)
    with pytest.raises(FinalTestAccessError, match="final-test"):
        ctx.check_seasons(list(eval_cfg.final_test_seasons))
    with pytest.raises(FinalTestAccessError):
        ctx.check_seasons([*eval_cfg.validation_seasons, *eval_cfg.final_test_seasons])


def test_training_context_cannot_even_read_validation(eval_cfg):
    train = make_context(EvalMode.TRAIN, eval_cfg)
    train.check_seasons(eval_cfg.train_seasons)
    with pytest.raises(FinalTestAccessError, match="may only read"):
        train.check_seasons(eval_cfg.validation_seasons)
    for mode in (EvalMode.VALIDATION, EvalMode.TUNING, EvalMode.CALIBRATION, EvalMode.ENSEMBLE_FIT):
        make_context(mode, eval_cfg).check_seasons([*eval_cfg.train_seasons, *eval_cfg.validation_seasons])


def test_final_context_cannot_be_constructed_directly(eval_cfg):
    with pytest.raises(FinalTestAccessError):
        make_context(EvalMode.FINAL, eval_cfg)
    with pytest.raises(FinalTestAccessError):
        EvaluationContext(EvalMode.FINAL, eval_cfg)
    with pytest.raises(FinalTestAccessError):
        EvaluationContext(EvalMode.FINAL, eval_cfg, object())


def test_seasons_outside_every_split_are_never_readable(eval_cfg, project):
    for mode in NON_FINAL_MODES:
        with pytest.raises(FinalTestAccessError):
            make_context(mode, eval_cfg).check_seasons(["2026-27"])
    root = project
    ctx = unlock_final(eval_cfg, RunMode.FINAL, root)
    with pytest.raises(FinalTestAccessError, match="belong to no split|only read"):
        ctx.check_seasons(["2026-27"])


def test_loader_rejects_final_seasons_for_every_ordinary_context(built, eval_cfg):
    ref = resolve_dataset(built / "data/processed")
    feats = load_features(built, ref, "fv2")
    for mode in NON_FINAL_MODES:
        with pytest.raises(FinalTestAccessError):
            load_rows(ref, make_context(mode, eval_cfg), list(eval_cfg.final_test_seasons), feats)
    rows = load_rows(
        ref, make_context(EvalMode.VALIDATION, eval_cfg), list(eval_cfg.validation_seasons), feats
    )
    assert rows and {r.season for r in rows} == set(eval_cfg.validation_seasons)


# ---------------------------------------------------------------------- unlock rules
@pytest.mark.parametrize("mode", [RunMode.DEVELOPMENT, RunMode.RESEARCH, RunMode.STRICT])
def test_only_final_mode_unlocks(project, eval_cfg, mode):
    with pytest.raises(FinalTestAccessError, match="may not unlock"):
        unlock_final(eval_cfg, mode, project)


def test_final_mode_requires_clean_tree_and_logs_access(project, eval_cfg):
    (project / "stray.txt").write_text("dirty")
    with pytest.raises(ProvenanceError, match="clean working tree"):
        unlock_final(eval_cfg, RunMode.FINAL, project)
    (project / "stray.txt").unlink()
    ctx = unlock_final(eval_cfg, RunMode.FINAL, project)
    assert ctx.mode == EvalMode.FINAL
    ctx.check_seasons(list(eval_cfg.final_test_seasons))
    log = (project / "artifacts/final_access_log.jsonl").read_text().splitlines()
    assert len(log) == 1 and json.loads(log[0])["mode"] == "final"


# ----------------------------------------------- nothing else ever loads final seasons
def test_baseline_run_never_requests_final_seasons(built, monkeypatch, eval_cfg):
    requested = []
    real = rb.load_rows

    def spy(ref, ctx, seasons, feats=None):
        requested.append((ctx.mode, list(seasons)))
        return real(ref, ctx, seasons, feats)

    monkeypatch.setattr(rb, "load_rows", spy)
    rb.run_baselines(built, RunMode.RESEARCH)
    assert requested and all(not set(s) & set(eval_cfg.final_test_seasons) for _, s in requested)
    assert {m for m, _ in requested} == {EvalMode.TRAIN, EvalMode.VALIDATION}


# ----------------------------------------------------------------------- final path
def test_final_evaluation_runs_once_and_is_immutable(tmp_path):
    root = make_project(tmp_path / "p")
    build_all(root)
    out = run_final_evaluation(root, RunMode.FINAL)
    result = json.loads((out / "final_results.json").read_text())
    assert result["n_final_rows"] == 12 and set(result["results"]) >= {"always_home", "market_implied"}
    assert not os.access(out / "final_results.json", os.W_OK)  # read-only artifact
    with pytest.raises(FinalAlreadyRun, match="spent"):
        run_final_evaluation(root, RunMode.FINAL)
    assert (
        len((root / "artifacts/final_access_log.jsonl").read_text().splitlines()) == 1
    )  # refused before unlock


def test_final_evaluation_refuses_non_final_modes(built):
    with pytest.raises(FinalTestAccessError):
        run_final_evaluation(built, RunMode.STRICT)


# ------------------------------------------------------------------- split contract
def test_split_manifest_is_complete_deterministic_and_config_sensitive(built, eval_cfg):
    ref = resolve_dataset(built / "data/processed")
    a = build_split_manifest(eval_cfg, ref)
    assert a == build_split_manifest(eval_cfg, ref)
    assert a["dataset_id"] == ref.data_version and a["split_id"].startswith("split-")
    assert a["train_period"]["rows"] == 12 and a["validation_period"]["rows"] == 12
    assert a["final_test_period"]["rows"] == 12 and a["train_period"]["n_seasons"] == 1
    c = a["cutoffs"]
    assert (
        c["train_end_utc"] < c["validation_start_utc"] < c["validation_end_utc"] < c["final_test_start_utc"]
    )
    assert a["excluded_seasons"] == []
    changed = eval_cfg.model_copy(update={"split_strategy": "rolling"})
    assert build_split_manifest(changed, ref)["split_id"] != a["split_id"]


def test_split_manifest_fails_when_a_split_season_is_missing_from_the_dataset(built, eval_cfg):
    ref = resolve_dataset(built / "data/processed")
    cfg = eval_cfg.model_copy(update={"final_test_seasons": ["2030-31"]})
    with pytest.raises(SplitError, match="absent from dataset"):
        build_split_manifest(cfg, ref)


def test_walk_forward_folds_follow_the_configured_strategy(eval_cfg):
    base = dict(
        split_strategy="expanding", min_train_seasons=2, rolling_window_seasons=2,
        train_seasons=["2019-20", "2020-21", "2021-22"], validation_seasons=["2022-23", "2023-24"],
        final_test_seasons=["2024-25"],
    )  # fmt: skip
    expanding = walk_forward_folds(eval_cfg.model_copy(update=base))
    assert [f.test_season for f in expanding] == ["2021-22", "2022-23", "2023-24"]
    assert expanding[2].train_seasons == ("2019-20", "2020-21", "2021-22", "2022-23")
    rolling = walk_forward_folds(eval_cfg.model_copy(update={**base, "split_strategy": "rolling"}))
    assert rolling[2].train_seasons == ("2021-22", "2022-23")  # window of 2
    for f in [*expanding, *rolling]:
        assert max(f.train_seasons) < f.test_season and "2024-25" not in f.train_seasons + (f.test_season,)
    with pytest.raises(SplitError):
        walk_forward_folds(eval_cfg.model_copy(update={**base, "min_train_seasons": 5}))
