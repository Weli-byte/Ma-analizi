"""Cross-machine reproduction check of Sprint 4c / 4d (ADR 0079): the comparison function itself, on synthetic files. The real comparison runs in the league-benchmark workflow."""

import json
from pathlib import Path

from src.league_models import s4c_benchmark

ROOT = Path(__file__).resolve().parents[1]


def write(d: Path, files: dict) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    for name, obj in files.items():
        (d / name).write_text(json.dumps(obj), encoding="utf-8")
    return d


BASE = {"s4c_metrics.json": {"1x2": {"ALL": {"log_loss": 1.0066, "n": 100}}}, "s4c_run_manifest.json": {"folds": ["2024-25"], "s4c_config_fingerprint": "aaa", "holdout": "not read"}}
NAMES = tuple(BASE)


def test_float_noise_within_the_tolerance_passes_and_a_real_difference_fails(tmp_path):
    committed = write(tmp_path / "c", BASE)
    noisy = json.loads(json.dumps(BASE))
    noisy["s4c_metrics.json"]["1x2"]["ALL"]["log_loss"] = 1.00662
    assert s4c_benchmark.compare_to_committed(write(tmp_path / "f1", noisy), committed, 1e-3, files=NAMES)["ok"]
    changed = json.loads(json.dumps(BASE))
    changed["s4c_metrics.json"]["1x2"]["ALL"]["log_loss"] = 1.02
    rep = s4c_benchmark.compare_to_committed(write(tmp_path / "f2", changed), committed, 1e-3, files=NAMES)
    assert not rep["ok"] and rep["differences"]["s4c_metrics.json"][0]["path"].endswith("log_loss")


def test_text_and_structure_must_match_exactly_even_with_a_loose_tolerance(tmp_path):
    committed = write(tmp_path / "c", {"s4c_decisions.json": {"cells": {"ENG1|1x2": {"supported": ["P_dc_sep vs B_dc_league"]}}}})
    other = write(tmp_path / "f", {"s4c_decisions.json": {"cells": {"ENG1|1x2": {"supported": ["H_dc_hier vs B_dc_league"]}}}})
    assert not s4c_benchmark.compare_to_committed(other, committed, 10.0, files=("s4c_decisions.json",))["ok"]            # a different decision is never float noise
    gone = write(tmp_path / "g", {"s4c_decisions.json": {"cells": {}}})
    assert not s4c_benchmark.compare_to_committed(gone, committed, 10.0, files=("s4c_decisions.json",))["ok"]


def test_the_config_fingerprints_are_ignored_because_later_sprints_add_sections(tmp_path):
    committed = write(tmp_path / "c", BASE)
    fresh = json.loads(json.dumps(BASE))
    fresh["s4c_run_manifest.json"]["s4c_config_fingerprint"] = "bbb"
    rep = s4c_benchmark.compare_to_committed(write(tmp_path / "f", fresh), committed, 1e-3, files=NAMES)
    assert rep["ok"] and "s4c_config_fingerprint" in rep["ignored_keys"]
    fresh["s4c_run_manifest.json"]["folds"] = ["2023-24"]
    assert not s4c_benchmark.compare_to_committed(write(tmp_path / "g", fresh), committed, 1e-3, files=NAMES)["ok"]       # everything else in the manifest still counts


def test_the_committed_files_exist_and_the_workflow_runs_both_comparisons():
    for name in s4c_benchmark.COMPARED_FILES:
        assert (ROOT / "docs" / "league_models" / name).is_file(), name
    assert (ROOT / "docs" / "league_models" / "s4d_extension.json").is_file()
    wf = (ROOT / ".github" / "workflows" / "league-benchmark.yml").read_text(encoding="utf-8")
    assert "s4c_benchmark --as-of 2026-10-20T00:00:00Z --compare-committed docs/league_models" in wf and "s4d_experiment --as-of 2026-10-20T00:00:00Z --compare-committed docs/league_models" in wf
    assert 'docs/league_models/s4c_*.json' in wf and 'docs/league_models/s4d_*.json' in wf


def test_a_parameter_digest_is_not_compared_because_a_hash_cannot_have_a_tolerance(tmp_path):
    a = {"s4c_nested_selection.json": [{"nested": {"H_dc_hier": {"per_target": {"1x2": {"chosen": "l2_team=8.0", "parameter_digest": "aaa"}}}}}]}
    b = {"s4c_nested_selection.json": [{"nested": {"H_dc_hier": {"per_target": {"1x2": {"chosen": "l2_team=8.0", "parameter_digest": "bbb"}}}}}]}
    names = ("s4c_nested_selection.json",)
    rep = s4c_benchmark.compare_to_committed(write(tmp_path / "f", b), write(tmp_path / "c", a), 1e-3, files=names)
    assert rep["ok"] and "parameter_digest" in rep["ignored_keys"]
    other = {"s4c_nested_selection.json": [{"nested": {"H_dc_hier": {"per_target": {"1x2": {"chosen": "l2_team=32.0", "parameter_digest": "bbb"}}}}}]}
    assert not s4c_benchmark.compare_to_committed(write(tmp_path / "g", other), tmp_path / "c", 1e-3, files=names)["ok"]       # the chosen candidate still has to agree
