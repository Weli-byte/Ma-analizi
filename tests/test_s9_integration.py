"""S9 (ADR 0020): calibration/reliability/leaderboard wired into run_baselines.py."""

import json

import numpy as np
import pytest
from conftest import build_all, make_project

from src.evaluation.run_baselines import MIN_CALIBRATION_ROWS, _calibration_for_model, run_baselines
from src.runmode import RunMode


def test_calibration_skips_when_too_few_common_rows():
    rng = np.random.default_rng(0)
    p = rng.dirichlet([2, 2, 2], size=2 * MIN_CALIBRATION_ROWS - 2)
    y = rng.integers(0, 3, len(p))
    result = _calibration_for_model(p, y, ["log_loss"], bins=10)
    assert result["skipped"] is True and "reason" in result


def test_calibration_fits_on_first_half_reports_on_second_half():
    rng = np.random.default_rng(1)
    n = 100
    p = rng.dirichlet([2, 2, 2], size=n)
    y = rng.integers(0, 3, n)
    result = _calibration_for_model(p, y, ["log_loss", "accuracy"], bins=10)
    assert result["skipped"] is False
    assert result["calibration_fit_rows"] == n // 2
    assert result["report_rows"] == n - n // 2
    assert set(result["raw_metrics"]) == {"log_loss", "accuracy"}
    assert set(result["calibrated_metrics"]) == {"log_loss", "accuracy"}
    assert result["temperature"] > 0


def test_calibration_never_touches_the_fit_half(monkeypatch):
    """Changing values in the FIRST half (fit-only) must not change the reported metrics, since
    reporting only ever reads the second half."""
    rng = np.random.default_rng(2)
    n = 60
    p = rng.dirichlet([2, 2, 2], size=n)
    y = rng.integers(0, 3, n)
    baseline = _calibration_for_model(p, y, ["log_loss"], bins=10)

    p2 = p.copy()
    p2[: n // 2] = rng.dirichlet([1, 1, 1], size=n // 2)  # scramble only the fit half's INPUTS
    mutated = _calibration_for_model(p2, y, ["log_loss"], bins=10)
    assert mutated["raw_metrics"] == baseline["raw_metrics"]  # second half's raw values unchanged


@pytest.fixture(scope="module")
def s9_report(tmp_path_factory):
    root = make_project(tmp_path_factory.mktemp("s9") / "proj")
    build_all(root, mode="research")
    out = run_baselines(root, RunMode.RESEARCH)
    report = json.loads((out.out_dir / "report.json").read_text(encoding="utf-8"))
    md = (out.out_dir / "report.md").read_text(encoding="utf-8")
    return report, md


def test_report_json_has_s9_sections_for_every_model(s9_report):
    report, _ = s9_report
    model_ids = {r["model_id"] for r in report["report"]["results"]}
    assert set(report["calibration"]) == model_ids
    assert set(report["reliability"]) == model_ids
    assert set(report["confidence_histogram"]) == model_ids
    for model_id in model_ids:
        cal = report["calibration"][model_id]
        assert cal["skipped"] is True  # golden fixture is tiny (12 common rows < 20)
        assert "reason" in cal


def test_leaderboard_covers_every_model_with_no_winner_label(s9_report):
    report, md = s9_report
    assert {r["model_id"] for r in report["leaderboard"]["global"]} == {
        r["model_id"] for r in report["report"]["results"]
    }
    assert "No single overall winner" in md
    assert "### global" in md


def test_predictions_jsonl_is_unaffected_by_s9(s9_report):
    """ADR 0020: S9 adds report.json/report.md content only; predictions.jsonl (and its content
    hash, verified elsewhere by the golden tests) is untouched."""
    report, _ = s9_report
    assert "calibration" not in report["report"]  # not folded into the existing `report` key
    assert set(report) >= {"calibration", "reliability", "confidence_histogram", "leaderboard"}
