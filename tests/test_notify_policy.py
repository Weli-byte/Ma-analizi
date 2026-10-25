"""What is worth a Telegram message, and the dead-man switch (ADR 0077). The network is monkeypatched through the fixture of test_notify."""

import json
from datetime import timedelta
from urllib.parse import unquote_plus

from src.league_models.rollout.__main__ import main
from src.mlops import notify
from tests.test_notify import NOW, _write_alerts, configured  # noqa: F401 - the fixture is used by name


def test_only_actionable_alerts_are_worth_a_message():
    for key in ("provider_error", "slow_inference", "artifact_store_missing", "metric_small_n:E_elo_bridge:1x2:t-24h", "drift_insufficient_rows", "license:football_data_org"):
        assert not notify.should_notify("warning", key) and not notify.should_notify("info", key)
    for key in ("scheduler_stale", "model_missing", "performance_regression:P_dc_sep:over_under:t-24h", "outcome_lag", "drift_critical", "no_shadow_run", "provider_not_configured", "fallback_rate"):
        assert notify.should_notify("warning", key)
    assert notify.should_notify("critical", "anything") and not notify.should_notify("info", "scheduler_stale")


def test_the_notify_command_stays_silent_about_routine_warnings(configured, tmp_path, capsys):  # noqa: F811
    d = tmp_path / "artifacts" / "league_models" / "rollout"
    fresh = (NOW - timedelta(minutes=5)).isoformat()
    rows = [{"at": fresh, "severity": "warning", "key": k, "message": "m"} for k in ("provider_error", "slow_inference", "artifact_store_missing", "scheduler_stale")]
    _write_alerts(d, rows)
    assert main(["notify", "--root", str(tmp_path), "--now", NOW.isoformat()]) == 0
    res = json.loads(capsys.readouterr().out)["results"]
    assert list(res) == ["alert:scheduler_stale:warning:2026-10-25"] and len(configured) == 1


def test_the_watchdog_announces_a_stalled_job_once_a_day_and_is_quiet_when_it_is_fresh(configured, tmp_path):  # noqa: F811
    stamp, state = tmp_path / "gate.json", tmp_path / "w.json"
    stamp.write_text(json.dumps({"generated_at_utc": (NOW - timedelta(hours=1)).isoformat()}), encoding="utf-8")
    assert notify.watchdog(stamp, 6, state, NOW, "Gölge işi") == "FRESH" and not configured
    stamp.write_text(json.dumps({"generated_at_utc": (NOW - timedelta(hours=9)).isoformat()}), encoding="utf-8")
    assert notify.watchdog(stamp, 6, state, NOW, "Gölge işi") == "STALE_SENT"
    assert "9.0 saattir" in unquote_plus(configured[0][1]) and "sınır 6 saat" in unquote_plus(configured[0][1])
    assert notify.watchdog(stamp, 6, state, NOW, "Gölge işi") == "STALE_ALREADY_SENT" and len(configured) == 1
    assert notify.watchdog(stamp, 6, state, NOW + timedelta(days=1), "Gölge işi") == "STALE_SENT"           # a new day, a new reminder
    assert notify.watchdog(tmp_path / "missing.json", 6, tmp_path / "w2.json", NOW, "Gölge işi") == "STALE_SENT"        # no file at all is also stalled
    stamp.write_text("{not json", encoding="utf-8")
    assert notify.watchdog(stamp, 6, tmp_path / "w3.json", NOW, "Gölge işi") == "STALE_SENT"


def test_the_watchdog_reads_the_generated_at_key_too(configured, tmp_path):  # noqa: F811
    stamp = tmp_path / "t.json"
    stamp.write_text(json.dumps({"generated_at": (NOW - timedelta(minutes=30)).isoformat()}), encoding="utf-8")
    assert notify.watchdog(stamp, 6, tmp_path / "s.json", NOW, "x") == "FRESH"


def test_the_watchdog_command_line_is_wired_and_reports_not_configured_without_secrets(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert notify.main(["--watchdog", str(tmp_path / "none.json"), "--state", str(tmp_path / "s.json")]) == 0
    assert capsys.readouterr().out.strip() == "STALE_NOT_CONFIGURED"


def test_the_markets_job_runs_the_watchdog_against_the_shadow_stamp():
    from pathlib import Path

    wf = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "markets-cloud.yml").read_text(encoding="utf-8")
    assert "src.mlops.notify --watchdog artifacts/markets/shadow_gate.json" in wf and "--max-hours 6" in wf
