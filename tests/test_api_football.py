"""API-Football free plan (ADR 0033): what a REAL account returned on 2026-10-08. Injuries come from a
real capture of `/injuries?date=` (Malaga v Espanyol, 2026-10-09 19:00 UTC, plus other leagues). The
adapter also runs over a real loopback HTTP socket serving that capture."""

import json
import threading
from datetime import UTC, datetime, timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from src.data.teams import TeamDirectory
from src.ingestion.api_football import ApiFootballProvider, parse_injuries, status_for
from src.ingestion.interfaces import AvailabilityStatus, Capability
from src.ingestion.provider import ProviderError
from src.llm.snapshot import build_snapshot
from src.snapshot.availability import injuries_block, merge_blocks

ROOT = Path(__file__).resolve().parents[1]
CAP = json.loads(
    (ROOT / "tests" / "fixtures" / "real_provider_captures" / "apifootball_injuries.json").read_text(
        encoding="utf-8"
    )
)
DAY = CAP["responses"]["2026-10-09"]
DIRECTORY = TeamDirectory.load(ROOT / "configs" / "team_aliases.yaml")
OBSERVED = datetime.fromisoformat(CAP["captured_at_utc"])
RAW = json.dumps({"errors": [], "response": DAY["response"]}).encode()
TEAMS = ("ESP_malaga", "ESP_espanyol")


def players():
    return parse_injuries(RAW, DIRECTORY, OBSERVED)


def test_real_capture_parses_for_la_liga_and_ignores_other_leagues():
    ps = players()
    assert ps and {p.team_id for p in ps} == set(TEAMS)  # the Russian "Premier League" etc. are ignored
    assert all(p.source == "api-football" and p.effective_at is None and p.confidence is None for p in ps)
    assert all(p.provenance["fixture_date"].startswith("2026-10-09T19:00") for p in ps)
    kinds = {(p.detail, p.status) for p in ps}
    assert ("Knee Injury", AvailabilityStatus.INJURED) in kinds
    assert ("Loan agreement", AvailabilityStatus.UNAVAILABLE) in kinds  # not injured, not selectable
    assert any(p.status == AvailabilityStatus.DOUBTFUL for p in ps)  # "Questionable"


def test_status_mapping_rules():
    assert status_for("Questionable", "Calf Injury") == AvailabilityStatus.DOUBTFUL
    assert status_for("Missing Fixture", "Suspended") == AvailabilityStatus.SUSPENDED
    assert status_for("Missing Fixture", "Hamstring Injury") == AvailabilityStatus.INJURED
    assert status_for("Missing Fixture", "National duty") == AvailabilityStatus.UNAVAILABLE


def test_a_plan_or_error_payload_raises_instead_of_meaning_no_injuries():
    # the real error text the free plan returned for a date outside its window
    plan = "Free plans do not have access to this date, try from 2026-10-07 to 2026-10-09."
    raw = json.dumps({"errors": {"plan": plan}, "response": []}).encode()
    with pytest.raises(ProviderError, match="Free plans do not have access"):
        parse_injuries(raw, DIRECTORY, OBSERVED)


def test_undated_entries_use_the_fetch_time_for_the_cutoff_rule():
    ps = players()
    after = injuries_block(ps, TEAMS, OBSERVED + timedelta(seconds=1), OBSERVED, "api-football", "sha")
    before = injuries_block(ps, TEAMS, OBSERVED - timedelta(seconds=1), OBSERVED, "api-football", "sha")
    assert len(after["players"]) == len(ps) and after["excluded_post_cutoff"] == 0
    assert before["players"] == [] and before["excluded_post_cutoff"] == len(ps)  # fetched after that cutoff
    row = after["players"][0]
    assert row["effective_at"] is None and row["as_of"] == OBSERVED.isoformat()
    assert row["source"] == "api-football"


def test_merge_keeps_every_source_and_lists_a_failed_one():
    cut = OBSERVED + timedelta(seconds=1)
    af = injuries_block(players(), TEAMS, cut, OBSERVED, "api-football", "sha-af")
    failed = {"status": "FAILED", "source": "fpl", "reason": "timeout"}
    merged = merge_blocks([af, failed], TEAMS)
    assert merged["status"] == "OBSERVED" and merged["source"] == "api-football"
    assert merged["failed_sources"] == {"fpl": "timeout"}
    assert merged["sources"]["api-football"]["raw_response_sha256"] == "sha-af"
    assert merge_blocks([failed], TEAMS) == failed  # nothing observed: the failure is what is reported


class Row:
    fixture_id, league_id, season = "f", "LALIGA", "2026-27"
    home_id, away_id = TEAMS
    kickoff_utc = datetime(2026, 10, 9, 19, 0, tzinfo=UTC)
    features: dict = {}
    odds: dict = {}

    def __init__(self, availability):
        self.availability = availability


def test_llm_snapshot_receives_api_football_injuries_with_their_source():
    cut = OBSERVED + timedelta(seconds=1)
    block = merge_blocks([injuries_block(players(), TEAMS, cut, OBSERVED, "api-football", "sha")], TEAMS)
    sent = build_snapshot(Row({"injuries": block}), cut)["permitted_current_information"]["injuries"]
    assert sent["source"] == "api-football" and {p["source"] for p in sent["players"]} == {"api-football"}
    early = build_snapshot(Row({"injuries": block}), OBSERVED - timedelta(seconds=1))
    assert not early["permitted_current_information"].get("injuries", {}).get("players")


@pytest.fixture
def server(tmp_path, monkeypatch):
    web = tmp_path / "web"
    web.mkdir()
    (web / "injuries").write_text(RAW.decode("utf-8"), encoding="utf-8")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(web)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("API_FOOTBALL_BASE_URL", f"http://127.0.0.1:{srv.server_address[1]}")
    yield
    srv.shutdown()


def test_adapter_over_a_real_http_socket(server):
    prov = ApiFootballProvider("key-never-logged", DIRECTORY)
    ps = prov.injuries_for_date(datetime(2026, 10, 9).date(), OBSERVED)
    assert len(ps) == len(players()) and prov.last_raw_sha256
    from src.mlops.oplog import read_rows

    (row,) = read_rows("provider_calls.jsonl")
    assert row["provider"] == "api-football" and row["ok"]
    assert "key-never-logged" not in json.dumps(row)


def test_adapter_connection_failure_message_never_contains_the_key(monkeypatch):
    monkeypatch.setenv("API_FOOTBALL_BASE_URL", "http://127.0.0.1:9")
    with pytest.raises(ProviderError) as e:
        ApiFootballProvider("SECRET-KEY-123", DIRECTORY).injuries_for_date(datetime(2026, 10, 9).date())
    assert "SECRET-KEY-123" not in str(e.value)


def test_capability_is_current_injuries_only_and_verified_on_the_real_plan():
    from src.ingestion.capabilities import capability_report

    rep = capability_report()
    assert "api-football" in rep[Capability.INJURIES.value]["supported_by"]
    assert rep[Capability.INJURIES.value]["verified_on"]["api-football"] == "2026-10-08"
    assert (
        "api-football" not in rep[Capability.LINEUPS.value]["supported_by"]
    )  # free plan: no current lineups
