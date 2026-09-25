"""Golden dataset (regression) and reproducibility tests.

If a golden test fails, the change altered normalized data / features / predictions / metrics.
Unexpected -> regression, fix the code. Intentional -> run scripts/update_golden.py AND write an ADR.
"""

import json
import shutil

import pytest
from conftest import make_project
from golden_util import GOLDEN_DIR, normalized_fixtures_csv, run_chain

from src.data.dataset import resolve_dataset
from src.evaluation.metrics import log_loss
from src.features.artifact import artifact_dir

UPDATE_HINT = "regression? if intentional: python scripts/update_golden.py and add an ADR"


@pytest.fixture(scope="module")
def chain(tmp_path_factory):
    root = make_project(tmp_path_factory.mktemp("golden") / "proj")
    return root, run_chain(root)


def test_golden_summary_matches(chain):
    _, summary = chain
    expected = json.loads((GOLDEN_DIR / "golden.json").read_text(encoding="utf-8"))
    for key in ("data_version", "dataset_content_hash", "table_hashes", "features_content_hash",
                "features_rows", "n_predictions"):  # fmt: skip
        assert summary[key] == expected[key], f"{key} changed - {UPDATE_HINT}"
    assert summary["predictions_sha256"] == expected["predictions_sha256"], UPDATE_HINT
    assert summary["report_sha256"] == expected["report_sha256"], UPDATE_HINT
    assert summary["report_json_sha256"] == expected["report_json_sha256"], UPDATE_HINT
    assert summary["metrics_sha256"] == expected["metrics_sha256"], UPDATE_HINT


def test_golden_metrics_match_with_tolerance(chain):
    _, summary = chain
    expected = json.loads((GOLDEN_DIR / "golden.json").read_text(encoding="utf-8"))["metrics"]
    assert summary["metrics"].keys() == expected.keys()
    for model, metrics in expected.items():
        for name, value in metrics.items():
            assert summary["metrics"][model][name] == pytest.approx(value, abs=1e-8), (
                model,
                name,
                UPDATE_HINT,
            )


def test_golden_normalized_dataset_matches_readable_snapshot(chain):
    root, _ = chain
    expected = (GOLDEN_DIR / "normalized_fixtures.csv").read_text(encoding="utf-8")
    assert normalized_fixtures_csv(root) == expected, UPDATE_HINT


def test_golden_sanity_values_are_independently_correct(chain):
    """Not a snapshot: a few facts checked by hand so a wrongly-updated golden file cannot hide a bug."""
    lines = (GOLDEN_DIR / "normalized_fixtures.csv").read_text(encoding="utf-8").splitlines()[1:]
    assert len(lines) == 36
    first = lines[0].split(",")
    assert first[1] == "2021-08-13T19:00:00+00:00"  # 20:00 BST -> 19:00 UTC
    assert (
        first[8] == "2021-08-13T22:00:00+00:00" and first[9] == "inferred"
    )  # kickoff + 3h, labelled inferred
    m = json.loads((GOLDEN_DIR / "golden.json").read_text(encoding="utf-8"))["metrics"]
    # always_home = [1,0,0] with the 1e-15 clip: log loss = -ln(1e-15) * share of non-home results
    assert m["always_home"]["log_loss"] == pytest.approx(
        34.538776395 * (1 - m["always_home"]["accuracy"]), rel=1e-6
    )
    assert m["always_home"]["accuracy"] == pytest.approx(8 / 12) and m["always_home"]["n"] == 12
    assert log_loss([[1 / 3] * 3], [0]) == pytest.approx(1.0986122887)


def test_predictions_are_immutable_records_with_full_provenance(chain):
    root, _ = chain
    run_dirs = list((root / "artifacts" / "runs").iterdir())
    assert len(run_dirs) == 1
    from src.schemas import PredictionRecord

    lines = (run_dirs[0] / "predictions.jsonl").read_text().strip().splitlines()
    recs = [PredictionRecord.from_json(ln) for ln in lines]  # verifies stored identity hashes
    assert len(recs) == 48 and {r.status.value for r in recs} == {"evaluated"}
    ref = resolve_dataset(root / "data" / "processed")
    assert {r.data_version for r in recs} == {ref.data_version} and {r.feature_version for r in recs} == {
        "fv2"
    }
    assert all(r.information_cutoff <= r.kickoff_utc and r.generated_at <= r.kickoff_utc for r in recs)
    assert len({r.prediction_id for r in recs}) == 48  # content-derived ids are unique here
    exp = json.loads(next((run_dirs[0] / "experiments").glob("market_implied.json")).read_text())
    for key in (
        "git_sha",
        "git_dirty",
        "python_version",
        "platform",
        "dependency_lock_hash",
        "data_version",
        "feature_version",
        "config_hash",
        "model_name",
        "model_version",
        "seed",
        "split_id",
        "train_rows",
        "validation_rows",
        "final_test_rows",
        "metrics",
        "created_at_utc",
        "experiment_id",
    ):
        assert key in exp and exp[key] not in (None, ""), key
    assert exp["git_sha"] != "unknown" and len(exp["git_sha"]) == 40 and exp["git_dirty"] is False
    assert exp["final_test_rows"] == 0 and exp["run_mode"] == "strict"


# ------------------------------------------------------------------- reproducibility
def test_full_reproducibility_after_deleting_all_generated_artifacts(tmp_path):
    root = make_project(tmp_path / "proj")
    first = run_chain(root)
    first_csv = normalized_fixtures_csv(root)
    for generated in ("data/processed", "data/features", "artifacts"):
        shutil.rmtree(root / generated)  # delete everything the pipeline generated
    second = run_chain(root)
    assert second == first  # normalized data, features, predictions, metrics, reports: all identical
    assert normalized_fixtures_csv(root) == first_csv
    ref = resolve_dataset(root / "data" / "processed")
    parquet = artifact_dir(root, ref.data_version, "fv2") / "features.parquet"
    assert parquet.exists()


def test_independent_machines_produce_the_same_hashes(tmp_path):
    a = run_chain(make_project(tmp_path / "a"))
    b = run_chain(make_project(tmp_path / "b"))  # different directory, different git SHA/timestamps
    assert a == b


def test_reproducibility_holds_across_run_modes(tmp_path):
    strict = run_chain(make_project(tmp_path / "s"), "strict")
    research = run_chain(make_project(tmp_path / "r"), "research")
    assert strict == research  # the mode changes what is enforced, never the numbers
