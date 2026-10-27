"""Injuries shown as information only (ADR 0057), on a real trimmed API-Football response."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.data.teams import TeamDirectory
from src.markets import injuries
from src.web import pages

ROOT = Path(__file__).resolve().parents[1]
RAW = (
    ROOT / "tests/fixtures/real_provider_captures/api_football_injuries_2026-10-14_trimmed.json"
).read_bytes()
NOW = datetime(2026, 10, 14, 8, 30, tzinfo=UTC)


def directory():
    return TeamDirectory.load(ROOT / "configs" / "team_aliases.yaml")


def test_parse_keeps_resolved_clubs_with_status_and_reason_and_drops_the_rest():
    body = injuries.parse(RAW, directory(), NOW)
    assert body["rows"] and body["source"] == "api-football" and len(body["raw_sha256"]) == 64
    feyenoord = [r for r in body["rows"] if r["team_id"] == "NED_feyenoord"]
    assert any(r["player"] == "T. Beelen" and r["reason"] == "Broken Leg" and r["status"] == "INJURED" for r in feyenoord)  # fmt: skip
    assert all(r["kickoff_utc"].startswith("2026-10-14") for r in body["rows"])
    assert body["unresolved_clubs"] >= 0


def test_a_plan_error_payload_raises_instead_of_meaning_no_injuries():
    bad = json.dumps(
        {"errors": {"plan": "Free plans do not have access to this season"}, "response": []}
    ).encode()
    with pytest.raises(RuntimeError, match="plan"):
        injuries.parse(bad, directory(), NOW)


def test_collect_stores_immutable_observations_skips_unchanged_and_reports_failures(tmp_path, monkeypatch):
    import shutil

    shutil.copytree(ROOT / "configs", tmp_path / "configs")
    calls = []

    def fake_fetch(key, day, timeout=30.0):
        calls.append(day)
        if day.day == 15:
            raise RuntimeError("api-football: URLError")
        return RAW

    monkeypatch.setattr(injuries, "fetch", fake_fetch)
    first = injuries.collect(tmp_path, "k", NOW)
    assert "entries kept" in first[0] and "FAILED" in first[1]
    files = list((tmp_path / "artifacts" / "injuries" / "2026-10-14").glob("inj-*.json"))
    assert len(files) == 1
    again = injuries.collect(tmp_path, "k", NOW.replace(hour=12))
    assert "unchanged" in again[0]
    assert len(list((tmp_path / "artifacts" / "injuries" / "2026-10-14").glob("inj-*.json"))) == 1


def test_latest_for_returns_one_row_per_player_and_none_without_an_observation(tmp_path):
    kickoff = datetime(2026, 10, 14, 16, 45, tzinfo=UTC)
    assert injuries.latest_for(tmp_path, "NED_feyenoord", "ITA_como", kickoff) is None
    day = tmp_path / "artifacts" / "injuries" / "2026-10-14"
    day.mkdir(parents=True)
    row = {"team_id": "NED_feyenoord", "player": "T. Beelen", "status": "INJURED", "reason": "Broken Leg", "kickoff_utc": kickoff.isoformat()}  # fmt: skip
    (day / "inj-1.json").write_text(json.dumps({"observed_at": NOW.isoformat(), "rows": [row, dict(row)]}), encoding="utf-8")  # fmt: skip
    got = injuries.latest_for(tmp_path, "NED_feyenoord", "ITA_como", kickoff)
    assert len(got["home"]) == 1 and got["away"] == []


def test_card_says_plainly_when_nothing_was_observed_and_never_claims_nobody_is_out():
    i = {"home": "Feyenoord", "away": "Como"}
    none = pages.injuries_card(i, None)
    assert "henüz alınmadı" in none and "Eksikler" in none
    seen = pages.injuries_card(
        i,
        {
            "observed_at": "2026-10-14T08:30:00+00:00",
            "home": [{"player": "T. Beelen", "status": "INJURED", "reason": "Broken Leg"}],
            "away": [],
        },
    )
    assert "T. Beelen" in seen and "Sakat" in seen and "Kaynakta kayıtlı eksik yok" in seen
    assert "tahmin olasılıklarına girmez" in seen


def test_cli_needs_a_key(monkeypatch, capsys):
    monkeypatch.delenv("API_FOOTBALL_KEY", raising=False)
    monkeypatch.setattr(injuries, "load_dotenv", lambda: None)
    monkeypatch.setattr("src.cli_utils.load_dotenv", lambda *a, **k: None)  # configure_output loads .env too
    assert injuries.main() == 2
    assert "NOT_CONFIGURED" in capsys.readouterr().out


def test_a_paused_collection_makes_no_request_even_with_a_key(monkeypatch, capsys):
    from src.markets import injuries

    monkeypatch.setenv("API_FOOTBALL_KEY", "never-used")
    monkeypatch.setenv("INJURIES_COLLECT", "off")
    monkeypatch.setattr(injuries, "collect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("a request was made")))
    assert injuries.main([]) == 0
    assert "PAUSED" in capsys.readouterr().out
