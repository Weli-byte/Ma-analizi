"""Telegram notifications (ADR 0077). The network is monkeypatched: nothing is sent from the tests."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from src.league_models.rollout.__main__ import main
from src.mlops import notify

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


class Resp:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:SECRETTOKEN")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    sent = []

    def fake(req, timeout=0):
        sent.append((req.full_url, req.data.decode()))
        return Resp()

    monkeypatch.setattr(notify.urllib.request, "urlopen", fake)
    return sent


def test_without_credentials_every_call_is_a_no_op(monkeypatch, tmp_path):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert not notify.configured() and notify.send("x") == "NOT_CONFIGURED" and notify.notify_once(tmp_path / "s.json", "k", "x") == "NOT_CONFIGURED"
    assert not (tmp_path / "s.json").exists()                                      # nothing is remembered as sent


def test_a_message_is_sent_once_per_key_and_a_failure_is_retried_and_never_leaks_the_token(configured, monkeypatch, tmp_path):
    st = tmp_path / "s.json"
    assert notify.notify_once(st, "k1", "merhaba") == "SENT" and notify.notify_once(st, "k1", "merhaba") == "ALREADY_SENT" and len(configured) == 1
    assert "chat_id=42" in configured[0][1] and "merhaba" in configured[0][1]

    def boom(req, timeout=0):
        raise OSError(f"cannot reach {req.full_url}")                              # a urllib error message contains the URL, which contains the token

    monkeypatch.setattr(notify.urllib.request, "urlopen", boom)
    res = notify.notify_once(st, "k2", "x")
    assert res == "FAILED:OSError" and "SECRETTOKEN" not in res and "k2" not in json.loads(st.read_text(encoding="utf-8"))["sent"]   # not remembered: the next run tries again


def _write_alerts(d, rows):
    d.mkdir(parents=True, exist_ok=True)
    (d / "alerts.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_the_notify_command_announces_fresh_warnings_and_gate_changes_once(configured, tmp_path, capsys):
    d = tmp_path / "artifacts" / "league_models" / "rollout"
    _write_alerts(d, [
        {"at": (NOW - timedelta(minutes=10)).isoformat(), "severity": "warning", "key": "model_missing", "message": "a model is unavailable"},
        {"at": (NOW - timedelta(minutes=10)).isoformat(), "severity": "info", "key": "drift_insufficient_rows", "message": "x"},
        {"at": (NOW - timedelta(days=3)).isoformat(), "severity": "critical", "key": "old_one", "message": "old"},
    ])  # fmt: skip
    (d / "shadow_gate.json").write_text(json.dumps({"families": {"P_dc_sep": {"status": "SHADOW", "status_reason": "x"}, "H_dc_hier": {"status": "PROMOTION_CANDIDATE", "status_reason": "better"}}}), encoding="utf-8")
    args = ["notify", "--root", str(tmp_path), "--now", NOW.isoformat()]
    assert main(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["configured"] is True and len(out["results"]) == 2 and all(v == "SENT" for v in out["results"].values())      # the info alert, the 3-day-old alert and the SHADOW family are not announced
    assert main(args) == 0 and all(v == "ALREADY_SENT" for v in json.loads(capsys.readouterr().out)["results"].values())
    assert len(configured) == 2


def test_the_track_step_and_both_workflows_are_wired_to_the_notifier_without_a_secret_in_the_files():
    import src.markets.track as track

    src = open(track.__file__, encoding="utf-8").read()
    assert "notify_once" in src and "card:{st['status']}" in src                       # a card state change is announced once (the key holds the status)
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for wf in ("shadow-cloud.yml", "markets-cloud.yml", "notify-test.yml"):
        text = (root / ".github" / "workflows" / wf).read_text(encoding="utf-8")
        assert "secrets.TELEGRAM_BOT_TOKEN" in text and "secrets.TELEGRAM_CHAT_ID" in text
        assert "api.telegram.org" not in text and "bot1" not in text
    assert "gate notify" in (root / ".github" / "workflows" / "shadow-cloud.yml").read_text(encoding="utf-8")
