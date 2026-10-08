"""Dashboard (S17, ADR 0034). The predictions come from a REAL captured forecast run (Arsenal v Leeds,
real Gemini/Groq/OpenAI calls, 2026-10-02); nothing is generated. An empty root must say 'no data',
never invent rows."""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from src.dashboard.build import main
from src.dashboard.render import render_html
from src.dashboard.viewmodel import build_viewmodel

ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT / "tests" / "fixtures" / "real_provider_captures" / "forecast_arsenal_leeds_predictions.jsonl"
NOW = datetime(2026, 10, 5, tzinfo=UTC)  # before the 2026-10-10 kickoff


def make_root(tmp_path: Path, with_run: bool = True) -> Path:
    shutil.copytree(ROOT / "configs", tmp_path / "configs")
    if with_run:
        run = tmp_path / "artifacts" / "llm_runs" / "forecast_fdorg-560593_20261002T080540Z"
        run.mkdir(parents=True)
        shutil.copy(CAPTURE, run / "predictions.jsonl")
        (run / "snapshot.json").write_text(
            json.dumps(
                {
                    "fixture_id": "fdorg-560593", "home_team_id": "ENG_arsenal",
                    "away_team_id": "ENG_leeds_united", "league_id": "EPL",
                    "kickoff_utc": "2026-10-10T11:30:00+00:00",
                }  # fmt: skip
            ),
            encoding="utf-8",
        )
    return tmp_path


def test_empty_root_reports_no_data_instead_of_inventing_rows(tmp_path):
    vm = build_viewmodel(make_root(tmp_path, with_run=False), NOW)
    assert vm["matches"] == [] and vm["predictions"] == [] and vm["live"] == []
    assert vm["models"]["available"] is False
    html = render_html(vm)
    assert "no prediction yet" in html and "no live match recorded" in html


def test_real_forecast_run_shows_full_traceability(tmp_path):
    vm = build_viewmodel(make_root(tmp_path), NOW)
    (m,) = vm["matches"]
    assert (m["home"], m["away"], m["upcoming"]) == ("Arsenal", "Leeds United", True)
    assert m["injuries"] == "UNKNOWN" and m["lineups"] == "UNKNOWN"  # unknown stays unknown
    preds = vm["predictions"]
    assert preds and all(p["model_class"] == "LLM_REAL" for p in preds)
    for p in preds:
        assert abs(sum(p["p"]) - 1) < 1e-6
        assert all(p[k] for k in ("prediction_id", "data_version", "feature_version", "generated_at", "provider"))
    html = render_html(vm)
    for p in preds:
        assert p["prediction_id"] in html and p["data_version"] in html and p["model_id"] in html


def test_a_finished_match_is_not_listed_as_upcoming(tmp_path):
    vm = build_viewmodel(make_root(tmp_path), datetime(2026, 10, 11, tzinfo=UTC))
    assert vm["matches"][0]["upcoming"] is False
    assert "Upcoming matches (0)" in render_html(vm)


def test_html_is_self_contained_and_escapes_values(tmp_path):
    vm = build_viewmodel(make_root(tmp_path), NOW)
    vm["matches"][0]["home"] = "<script>alert(1)</script>"
    html = render_html(vm)
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "http://" not in html.replace("http://www.w3.org", "") and "https://" not in html
    assert "prefers-color-scheme:dark" in html


def test_cli_writes_the_dashboard(tmp_path, capsys):
    root = make_root(tmp_path)
    assert main(["--root", str(root)]) == 0
    out = root / "artifacts" / "dashboard" / "index.html"
    assert out.exists() and "Football forecasting dashboard" in out.read_text(encoding="utf-8")
