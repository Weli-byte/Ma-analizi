"""Command-line entry points of the Sprint 6 layer and the Sprint 4b stage experiment, exercised on a scratch root (TEST_ONLY data where synthetic)."""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import yaml

from src.ingestion import openfootball_txt
from src.league_models import stage_experiment
from src.league_models.rollout import __main__ as cli
from src.league_models.rollout import records
from tests.test_league_models_s3 import _synthetic_audit
from tests.test_openfootball_txt_and_overlay import NEW_LAYOUT

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


def test_the_rollout_cli_reports_status_scorecard_dashboard_and_monitoring_on_a_scratch_root(tmp_path, capsys):
    d = tmp_path / "artifacts" / "league_models" / "rollout"
    kick = datetime(2026, 10, 23, 11, 30, tzinfo=UTC)
    store = records.ProspectiveStore(d / "prospective.jsonl")
    store.put(records.ProspectiveRecord("fdorg-1", "ITA1", kick.isoformat(), NOW.isoformat(), "t-24h", "v1", "global", "global", 0.5, 0.3, 0.2, 0.5, 0.5, "snap",
                                        ("openfootball_domestic", "football_data_org"), {"lineups": "UNKNOWN"}), NOW)  # fmt: skip
    for cmd in ("status", "scorecard", "dashboard", "monitor"):
        rc = cli.main([cmd, "--root", str(tmp_path), "--now", NOW.isoformat()])
        assert rc == 0, cmd
    out = capsys.readouterr().out
    assert "GLOBAL ONLY" in out and "records_total" in out and "UNCHANGED" in out
    assert (d / "dashboard.html").read_text(encoding="utf-8").count("Football data provided by the Football-Data.org API") == 1
    assert (d / "scorecard.json").is_file() and (d / "alerts.jsonl").is_file() and not (d / "KILL_SWITCH").exists()


def test_the_shadow_cli_without_a_key_is_not_configured_and_writes_nothing(tmp_path, monkeypatch, capsys):
    (tmp_path / "configs").mkdir()
    shutil.copy(ROOT / "configs" / "ingestion.yaml", tmp_path / "configs" / "ingestion.yaml")
    shutil.copy(ROOT / "configs" / "model.yaml", tmp_path / "configs" / "model.yaml")
    monkeypatch.delenv("FOOTBALL_DATA_ORG_API_KEY", raising=False)
    monkeypatch.setattr("src.cli_utils.load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(cli, "run_shadow", lambda root, now, cfg=None: {"status": "NOT_CONFIGURED", "reason": "no key", "per_fixture_ms": {}})
    assert cli.main(["shadow", "--root", str(tmp_path), "--now", NOW.isoformat()]) == 2
    assert "NOT_CONFIGURED" in capsys.readouterr().out


def test_the_txt_fetch_command_writes_to_the_operational_cache(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(openfootball_txt, "logged_urlopen", lambda *a, **k: NEW_LAYOUT.encode("utf-8"))
    assert openfootball_txt.main(["--league", "TUR1", "--season", "2026", "--root", str(tmp_path)]) == 0
    assert "operational_cache" in capsys.readouterr().out


def test_the_stage_experiment_runs_end_to_end_on_a_synthetic_audit(tmp_path):
    bench = (ROOT / "configs" / "league_models" / "benchmark.yaml").read_text(encoding="utf-8").replace("min_train_matches: 500", "min_train_matches: 50")
    (tmp_path / "bench.yaml").write_text(bench, encoding="utf-8")
    ml = yaml.safe_load((ROOT / "configs" / "league_models" / "ml_models.yaml").read_text(encoding="utf-8"))
    ml["tuning"].update({"max_trials": 2, "n_startup_trials": 1, "max_rounds": 25, "early_stopping_rounds": 5})
    ml["features"].update({"min_prior_matches": 20})
    (tmp_path / "ml.yaml").write_text(yaml.safe_dump(ml), encoding="utf-8")
    out = stage_experiment.run(tmp_path, NOW, tmp_path / "docs", audit=_synthetic_audit(), bench_config_path=tmp_path / "bench.yaml", ml_config_path=ml_path(tmp_path))
    assert out["uefa_evaluation_rows"] > 0 and set(out["results"]) == {"1x2", "over_under", "btts"} and "not read" in out["holdout"]
    assert all(v["verdict"] in ("SUPPORTED", "NOT_SUPPORTED", "INCONCLUSIVE", "TOO_FEW_ROWS") for g in out["results"].values() for v in g.values())
    assert json.loads((tmp_path / "docs" / "s4b_stage_experiment.json").read_text(encoding="utf-8"))["schema_version"] == "lm-s4b-stage-v1"


def ml_path(root: Path) -> Path:
    return root / "ml.yaml"
