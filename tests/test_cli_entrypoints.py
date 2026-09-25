"""Every important main() path is exercised end-to-end on the golden fixture."""

import json

import pytest
from conftest import make_project

from src.data import checksums, download
from src.data import pipeline as pipeline_cli
from src.evaluation import final as final_cli
from src.evaluation import run_baselines as baselines_cli
from src.features import builder as builder_cli

GOOD = (
    b"Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR\nTST,13/08/2024,15:00,Alpha FC,Beta Utd,2,0,H\n"
)


def test_full_cli_chain(project, capsys):
    root = ["--root", str(project)]
    assert pipeline_cli.main([*root, "--mode", "strict", "--as-of", "2026-09-25"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert (
        out["data_version"].startswith("dv-")
        and out["accepted_fixtures"] == 36
        and not out["failed_error_checks"]
    )
    assert builder_cli.main([*root, "--mode", "strict", "--audit-samples", "20"]) == 0
    assert "36 snapshots" in capsys.readouterr().out
    assert baselines_cli.main([*root, "--mode", "strict"]) == 0
    text = capsys.readouterr().out
    assert "Baseline benchmark" in text and "reference_market_baseline" in text and "[95% CI]" in text
    assert final_cli.main([*root]) == 0  # the final path itself (clean tree, final mode)
    assert "final results written" in capsys.readouterr().out
    assert final_cli.main([*root]) == 2  # spent: refused
    assert "FINAL EVALUATION REFUSED" in capsys.readouterr().err


def test_cli_failures_return_nonzero_with_actionable_messages(project, capsys):
    root = ["--root", str(project)]
    assert builder_cli.main([*root, "--mode", "development"]) == 2  # no dataset yet
    assert "FEATURE BUILD FAILED" in capsys.readouterr().err
    assert baselines_cli.main([*root, "--mode", "development"]) == 2
    assert "BASELINE RUN FAILED" in capsys.readouterr().err
    (project / "data/raw/football_data/TST_2223.csv").write_text("<html>blocked</html>")
    assert pipeline_cli.main([*root, "--mode", "development"]) == 2
    err = capsys.readouterr().err
    assert "PIPELINE FAILED" in err and "HTML" in err


def test_strict_cli_refuses_dirty_tree(project, capsys):
    root = ["--root", str(project)]
    assert pipeline_cli.main([*root, "--mode", "development", "--as-of", "2026-09-25"]) == 0
    capsys.readouterr()
    (project / "stray.txt").write_text("x")
    assert builder_cli.main([*root, "--mode", "strict"]) == 2
    assert "clean working tree" in capsys.readouterr().err


def test_checksums_cli_pin_verify_and_drift(project, capsys):
    root = ["--root", str(project)]
    assert checksums.main([*root, "verify"]) == 0
    assert "verified" in capsys.readouterr().out
    exp = project / "data/expected_checksums.json"
    data = json.loads(exp.read_text())
    del data["files"]["TST_2324.csv"]
    exp.write_text(json.dumps(data))
    assert checksums.main([*root, "verify"]) == 1  # unknown checksum is reported
    assert checksums.main([*root, "pin", "--file", "TST_2324.csv"]) == 0
    pinned = json.loads(exp.read_text())["files"]["TST_2324.csv"]
    assert pinned["status"] == "pinned_observed" and "not independently verified" in pinned["basis"]
    raw = project / "data/raw/football_data/TST_2324.csv"
    raw.write_bytes(raw.read_bytes().replace(b"Alpha FC", b"Alpha  FC", 1))
    assert checksums.main([*root, "verify"]) == 2
    assert "CHECKSUM MISMATCH" in capsys.readouterr().err
    assert checksums.main([*root, "pin", "--file", "TST_2324.csv"]) == 2  # refuses to re-pin a drifted file


def test_download_cli_success_failure_and_manual_registration(project, monkeypatch, capsys):
    root = ["--root", str(project)]
    monkeypatch.setattr(download, "default_fetch", lambda url, timeout: GOOD)
    assert download.main([*root, "--seasons", "2024-25"]) == 0
    assert "ok TST_2425.csv origin=official" in capsys.readouterr().out
    assert (project / "data/raw/football_data/TST_2425.csv").read_bytes() == GOOD

    def fail(url, timeout):
        raise download.FetchError("no network", retryable=False)

    monkeypatch.setattr(download, "default_fetch", fail)
    assert download.main([*root, "--seasons", "2025-26"]) == 1
    assert "FAILED" in capsys.readouterr().err
    raw = project / "data/raw/football_data/TST_2526.csv"
    raw.write_bytes(GOOD)
    assert download.main([*root, "--register-manual", "TST_2526.csv", "--source-url", "https://x/y"]) == 0
    side = json.loads((project / "data/provenance/football_data/TST_2526.csv.json").read_text())
    assert side["origin"] == "manual" and side["source_url"] == "https://x/y"


def test_pipeline_download_flag_failure_keeps_dataset_untouched(project, monkeypatch, capsys):
    root = ["--root", str(project)]
    assert pipeline_cli.main([*root, "--mode", "development", "--as-of", "2026-09-25"]) == 0
    before = (project / "data/processed/CURRENT.json").read_text()
    capsys.readouterr()

    def fail(url, timeout):
        raise download.FetchError("offline", retryable=False)

    monkeypatch.setattr(download, "default_fetch", fail)
    assert pipeline_cli.main([*root, "--mode", "development", "--download"]) == 2
    assert "download failed" in capsys.readouterr().err
    assert (project / "data/processed/CURRENT.json").read_text() == before


def test_python_m_entrypoints_import_without_side_effects():
    import importlib

    for name in (
        "src.data.pipeline",
        "src.data.download",
        "src.data.checksums",
        "src.data.team_resolution",
        "src.features.builder",
        "src.evaluation.run_baselines",
        "src.evaluation.final",
    ):
        mod = importlib.import_module(name)
        assert callable(mod.main)
    assert make_project  # fixture helper importable in the same namespace


@pytest.mark.parametrize(
    "module", ["src.data.pipeline", "src.features.builder", "src.evaluation.run_baselines"]
)
def test_python_dash_m_runs_without_runpy_warnings(module, tmp_path):
    import subprocess
    import sys

    res = subprocess.run([sys.executable, "-W", "error", "-m", module, "--help"], capture_output=True, text=True,
                         cwd=tmp_path, env={**__import__("os").environ, "PYTHONPATH": str(__import__("pathlib").Path(__file__).resolve().parents[1])})  # fmt: skip
    assert res.returncode == 0, res.stderr
    assert "RuntimeWarning" not in res.stderr
