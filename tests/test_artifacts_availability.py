import json
from datetime import date

import pytest
from conftest import build_all

from src.config import load_config
from src.data.dataset import resolve_dataset
from src.data.pipeline import run_pipeline
from src.evaluation import run_baselines as rb
from src.evaluation.context import EvalMode, make_context
from src.evaluation.dataset import load_rows
from src.features import registry as reg
from src.features.artifact import StaleArtifactError, artifact_dir, load_features
from src.features.availability import FeatureAvailabilityError, build_report, enforce
from src.features.builder import BuildError, build_features
from src.provenance import ProvenanceError
from src.runmode import RunMode

FV = "fv2"


def lineage_path(root, ref):
    return artifact_dir(root, ref.data_version, FV) / "feature_lineage.json"


def edit_lineage(root, ref, **changes):
    p = lineage_path(root, ref)
    data = json.loads(p.read_text())
    data.update(changes)
    p.write_text(json.dumps(data))


# ------------------------------------------------------------------------- lineage
def test_lineage_records_full_provenance(built):
    ref = resolve_dataset(built / "data/processed")
    lin = json.loads(lineage_path(built, ref).read_text())
    for key in (
        "data_version",
        "dataset_content_hash",
        "feature_version",
        "builder_version",
        "registry_hash",
        "source_files",
        "source_checksums",
        "created_at_utc",
        "git_sha",
        "config_hash",
        "parquet_sha256",
        "content_hash",
        "python_version",
        "dependency_lock_hash",
        "run_mode",
        "leakage_audit",
    ):
        assert lin[key] not in (None, "", []), key
    assert lin["data_version"] == ref.data_version and len(lin["git_sha"]) == 40
    assert lin["source_files"] == [f["file"] for f in ref.meta["raw_files"]]
    assert "xg_avg_10" in lin["experimental_not_produced"]
    table = load_features(built, ref, FV)
    assert len(table.rows) == 36 and table.lineage["data_version"] == ref.data_version
    first = next(iter(table.rows.values()))
    assert not any("xg" in k for k in first) and "home_rest_days_capped" in first


def test_feature_snapshots_are_bound_to_the_data_version(built):
    from src.features.builder import build_snapshots, load_matches

    ref = resolve_dataset(built / "data/processed")
    cfg = load_config("features", built / "configs")
    snaps = build_snapshots(load_matches(ref), ref.data_version, cfg)
    assert {s.data_version for s in snaps} == {ref.data_version}


# ------------------------------------------------------------------ stale artifacts
def test_stale_artifact_when_dataset_changes_without_rebuilding_features(built):
    ref_old = resolve_dataset(built / "data/processed")
    raw = built / "data/raw/football_data/TST_2223.csv"
    from test_data_provenance import rewrite_raw

    rewrite_raw(built, "TST_2223.csv", raw.read_text().replace(",2,0,H,", ",3,0,H,", 1))
    run_pipeline(built, RunMode.STRICT, as_of=date(2026, 9, 25))
    ref_new = resolve_dataset(built / "data/processed")
    assert ref_new.data_version != ref_old.data_version
    with pytest.raises(StaleArtifactError, match="no feature artifact"):
        load_features(built, ref_new, FV)  # features exist only for the OLD data version
    load_features(built, ref_old, FV)  # the old pair still loads consistently


@pytest.mark.parametrize(
    "change,message",
    [
        (dict(data_version="dv-000000000000"), "data_version"),
        (dict(dataset_content_hash="0" * 64), "content hash"),
        (dict(feature_version="fv1"), "feature_version"),
        (dict(registry_hash="0" * 16), "registry changed"),
        (dict(parquet_sha256="0" * 64), "checksum"),
        (dict(rows=999), "row count"),
    ],
)
def test_tampered_or_mismatched_lineage_fails_loudly(built, change, message):
    ref = resolve_dataset(built / "data/processed")
    edit_lineage(built, ref, **change)
    with pytest.raises(StaleArtifactError, match=message):
        load_features(built, ref, FV)


def test_corrupted_parquet_is_detected(built):
    ref = resolve_dataset(built / "data/processed")
    parquet = artifact_dir(built, ref.data_version, FV) / "features.parquet"
    parquet.write_bytes(parquet.read_bytes() + b"x")
    with pytest.raises(StaleArtifactError, match="checksum"):
        load_features(built, ref, FV)


def test_feature_definition_change_without_version_bump_fails_to_load(built, monkeypatch):
    ref = resolve_dataset(built / "data/processed")
    load_features(built, ref, FV)
    changed = reg.REGISTRY[0].model_copy(update={"aggregation": "something else"})
    monkeypatch.setattr(reg, "REGISTRY", [changed, *reg.REGISTRY[1:]])
    with pytest.raises(StaleArtifactError, match="registry changed"):
        load_features(built, ref, FV)


def test_corrupted_feature_version_in_config_fails_the_baseline_run(built):
    (built / "configs/model.yaml").write_text(
        (built / "configs/model.yaml").read_text().replace("fv2", "fv1")
    )
    with pytest.raises(StaleArtifactError, match="no feature artifact"):
        rb.run_baselines(built, RunMode.DEVELOPMENT)
    with pytest.raises(BuildError, match="feature_version"):
        build_features(built, RunMode.DEVELOPMENT, audit_samples=5)


def test_builder_refuses_when_no_dataset_exists(project):
    from src.data.dataset import DatasetError

    with pytest.raises(DatasetError):
        build_features(project, RunMode.DEVELOPMENT, audit_samples=5)


def test_strict_builder_refuses_a_dirty_tree(project):
    build_all(project, "development")
    (project / "stray.txt").write_text("x")
    with pytest.raises(ProvenanceError, match="clean working tree"):
        build_features(project, RunMode.STRICT, audit_samples=5)


# ------------------------------------------------------------- availability report
def rows_for(root):
    ref = resolve_dataset(root / "data/processed")
    cfg = load_config("evaluation", root / "configs")
    feats = load_features(root, ref, FV)
    return (
        load_rows(ref, make_context(EvalMode.VALIDATION, cfg), list(cfg.validation_seasons), feats),
        feats,
        ref,
        cfg,
    )


REQUIRED = ["home_form_points_5", "away_form_points_5"]


def test_report_on_real_features_lists_every_declared_reason(built):
    rows, *_ = rows_for(built)
    rep = build_report(rows, REQUIRED)
    assert rep.total_fixtures == 12 and rep.unexpected_missing_rows == 0
    assert rep.available_feature_rows + rep.fallback_rows + rep.missing_feature_rows == 12
    assert rep.fallback_rate == pytest.approx((rep.fallback_rows + rep.missing_feature_rows) / 12)
    assert set(rep.reasons) <= {"dataset_start", "new_team", "insufficient_history"}
    assert sum(rep.missing_by_feature.values()) == sum(rep.missing_by_team.values())
    assert set(rep.missing_by_season) <= {"2022-23"}


def test_intentionally_missing_feature_row_is_reported_and_blocks_strict(built):
    from dataclasses import replace

    rows, *_ = rows_for(built)
    broken = [replace(rows[0], features={}, unavailable_reasons={}), *rows[1:]]
    rep = build_report(broken, REQUIRED)
    assert rep.missing_feature_rows == 1 and rep.unexpected_missing_rows >= 1
    assert rep.reasons["missing_record"] == 1
    with pytest.raises(FeatureAvailabilityError, match="unexpected"):
        enforce(rep, RunMode.STRICT, 1.0, "recent_form_naive")
    enforce(rep, RunMode.DEVELOPMENT, 1.0, "recent_form_naive")  # development tolerates it (still reported)
    with pytest.raises(FeatureAvailabilityError, match="exceeds"):
        enforce(rep, RunMode.RESEARCH, 0.0, "recent_form_naive")


def test_missing_by_team_attributes_each_feature_to_its_side():
    from datetime import UTC, datetime

    from src.evaluation.dataset import EvalRow

    ko = datetime(2024, 1, 1, tzinfo=UTC)
    rows = [
        EvalRow("f1", "L", "2023-24", ko, "H1", "A1", 0, {"home_form_points_5": None, "away_form_points_5": 3.0},
                {"home_form_points_5": "new_team"}),
        EvalRow("f2", "L", "2023-24", ko, "H2", "A2", 0, {"home_form_points_5": 1.0, "away_form_points_5": None},
                {"away_form_points_5": "insufficient_history"}),
        EvalRow("f3", "L", "2023-24", ko, "H3", "A3", 0, {"home_form_points_5": None, "away_form_points_5": None}, {}),
    ]  # fmt: skip
    rep = build_report(rows, REQUIRED)
    assert rep.missing_by_team == {"H1": 1, "A2": 1, "H3": 1, "A3": 1}
    assert rep.reasons == {"new_team": 1, "insufficient_history": 1, "undeclared": 2}
    assert rep.unexpected_missing_rows == 1  # f3 has no declared reason
    assert rep.missing_by_feature == {"home_form_points_5": 2, "away_form_points_5": 2}
