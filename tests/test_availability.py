"""Phase I (ADR 0028): real injury/availability data, UNKNOWN lineups, cutoff rule, capabilities.

The injury data is a trimmed REAL_PROVIDER_CAPTURE of the actual Fantasy Premier League response
(`tests/fixtures/real_provider_captures/fpl_bootstrap_trimmed.json`); the live parser is also
exercised against the real endpoint in `tests/integration/test_fpl_live.py`."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.data.teams import TeamDirectory
from src.ingestion.capabilities import capability_report
from src.ingestion.fpl import STATUS_MAP, parse_bootstrap
from src.ingestion.interfaces import AvailabilityStatus, Capability
from src.llm.snapshot import CutoffViolation, audit_snapshot, build_snapshot
from src.snapshot.availability import failed_block, injuries_block, unknown_block

ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT / "tests" / "fixtures" / "real_provider_captures" / "fpl_bootstrap_trimmed.json"
OBSERVED = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def players():
    directory = TeamDirectory.load(ROOT / "configs" / "team_aliases.yaml")
    raw = CAPTURE.read_bytes()
    assert json.loads(raw)["label"] == "REAL_PROVIDER_CAPTURE"
    return parse_bootstrap(raw, directory, OBSERVED, "capture-sha")


def test_real_capture_parses_into_availability_records_with_provenance(players):
    assert len(players) > 50  # plain-available players are not listed
    assert all(p.status != AvailabilityStatus.AVAILABLE for p in players)
    assert all(p.source == "fpl" and p.observed_at == OBSERVED for p in players)
    assert all(p.provenance["endpoint"].startswith("https://fantasy.premierleague.com") for p in players)
    assert all(p.confidence is None for p in players)  # FPL exposes a %, not a confidence: never invented
    assert all(p.team_id and p.team_id.startswith("ENG_") for p in players)  # every club resolved
    assert any(p.effective_at is not None for p in players) and set(STATUS_MAP.values()) >= {
        p.status for p in players
    }


def test_cutoff_rule_uses_only_entries_dated_at_or_before_the_cutoff(players):
    from collections import Counter

    team = Counter(p.team_id for p in players if p.effective_at).most_common(1)[0][0]
    mine = sorted((p for p in players if p.team_id == team and p.effective_at), key=lambda p: p.effective_at)
    cutoff = mine[len(mine) // 2].effective_at  # an entry dated exactly at the cutoff is allowed
    block = injuries_block(players, (team, "ENG_nobody"), cutoff, OBSERVED, "fpl", "sha")
    expected_used = [p for p in players if p.team_id == team and p.effective_at and p.effective_at <= cutoff]
    expected_excluded = [
        p for p in players if p.team_id == team and (p.effective_at is None or p.effective_at > cutoff)
    ]
    assert block["status"] == "OBSERVED" and len(block["players"]) == len(expected_used)
    assert block["excluded_post_cutoff"] == len(expected_excluded) and expected_excluded
    assert all(datetime.fromisoformat(p["as_of"]) <= cutoff for p in block["players"])
    assert block["cutoff_rule"].endswith("<= information_cutoff") and "absence" in block["note"]


def test_unknown_and_failed_are_distinct_from_observed():
    assert unknown_block("no_provider") == {"status": "UNKNOWN", "reason": "no_provider"}
    f = failed_block("fpl", "URLError: timed out")
    assert f["status"] == "FAILED" and f["source"] == "fpl" and "timed out" in f["reason"]


class Row:
    fixture_id, league_id, season = "fx", "EPL", "2026-27"
    home_id, away_id = "ENG_arsenal", "ENG_leeds_united"
    kickoff_utc = datetime(2026, 10, 10, 11, 30, tzinfo=UTC)
    features = {"home_form_points_5": 10.0}
    odds: dict = {}

    def __init__(self, availability):
        self.availability = availability


def test_llm_snapshot_carries_only_cutoff_safe_injury_entries(players):
    cutoff = datetime(2026, 10, 9, 11, 30, tzinfo=UTC)
    block = injuries_block(players, ("ENG_arsenal", "ENG_leeds_united"), cutoff, OBSERVED, "fpl", "sha")
    snap = build_snapshot(Row({"injuries": block}), cutoff)
    sent = snap["permitted_current_information"]["injuries"]
    assert sent["source"] == "fpl" and "observed_at" not in json.dumps(sent)  # fetch time is not sent
    assert all(datetime.fromisoformat(p["as_of"]) <= cutoff for p in sent["players"])
    audit_snapshot(snap, Row.kickoff_utc, cutoff)  # clean snapshot passes the pre-call gate


def test_unknown_or_failed_injuries_are_not_sent_as_data():
    for block in (unknown_block("no_provider"), failed_block("fpl", "down"), None):
        snap = build_snapshot(Row({"injuries": block} if block else {}), Row.kickoff_utc)
        assert "injuries" not in snap["permitted_current_information"]


def test_audit_refuses_an_injury_entry_dated_after_the_cutoff(players):
    cutoff = datetime(2026, 10, 9, 11, 30, tzinfo=UTC)
    block = injuries_block(players, ("ENG_arsenal", "ENG_leeds_united"), cutoff, OBSERVED, "fpl", "sha")
    snap = build_snapshot(Row({"injuries": block}), cutoff)
    snap["permitted_current_information"].setdefault("injuries", {"players": []})["players"].append(
        {"player": "Leaked", "as_of": (cutoff + timedelta(minutes=1)).isoformat()}
    )
    with pytest.raises(CutoffViolation):
        audit_snapshot(snap, Row.kickoff_utc, cutoff)


def test_stage_snapshot_records_injuries_status_and_keeps_lineups_unknown(players):
    from src.features.history import MatchHistory
    from src.snapshot.engine import build_stage_snapshot
    from src.snapshot.stages import SnapshotStage, cutoff_for_stage

    cutoff = cutoff_for_stage(Row.kickoff_utc, SnapshotStage.T_24H)
    block = injuries_block(players, (Row.home_id, Row.away_id), cutoff, OBSERVED, "fpl", "sha")
    kw = dict(data_version="dv-000000000000", feature_version="fv2")
    plain = build_stage_snapshot(Row({}), MatchHistory([]), SnapshotStage.T_24H, **kw)
    with_inj = build_stage_snapshot(Row({}), MatchHistory([]), SnapshotStage.T_24H, injuries=block, **kw)
    assert plain.availability["injuries"]["status"] == "UNKNOWN"
    assert with_inj.availability["injuries"]["status"] == "OBSERVED"
    assert plain.availability["lineups"] == with_inj.availability["lineups"] == {
        "status": "UNKNOWN", "reason": "no_provider",
    }  # fmt: skip
    assert plain.snapshot_hash != with_inj.snapshot_hash  # observed data is part of the content hash


def test_capability_report_is_truthful_about_what_real_data_exists():
    rep = capability_report()
    assert rep[Capability.FIXTURES.value]["supported_by"] == ["football-data-org"]
    assert rep[Capability.INJURIES.value]["supported_by"] == ["fpl", "api-football"]
    assert rep[Capability.INJURIES.value]["license_status"] == ["RESEARCH_ONLY"]
    assert rep[Capability.EVENTS.value]["supported_by"] == ["openligadb"]  # goals only (Bundesliga)
    assert rep[Capability.ODDS.value]["supported_by"] == ["espn", "the-odds-api"]
    assert rep[Capability.LINEUPS.value]["supported_by"] == ["espn"]
    for cap in (Capability.STATISTICS, Capability.XG):
        assert rep[cap.value]["status"] == "NONE" and rep[cap.value]["supported_by"] == []


# ------------------------------------------------------------------------------ lineups (ADR 0031)
LINEUP_CAPTURE = json.loads(
    (ROOT / "tests" / "fixtures" / "real_provider_captures" / "espn_rosters.json").read_text(encoding="utf-8")
)


def test_real_finished_match_lineups_parse_to_two_elevens_with_formation():
    from src.ingestion.lineups import parse_lineups

    cap = LINEUP_CAPTURE["events"]["finished_401879301"]
    block = parse_lineups(cap["summary"], OBSERVED, cap["sha256"], "401879301")
    assert block["status"] == "OBSERVED" and block["event_id"] == "401879301" and block["source"] == "espn"
    for side, formation in (("home", "4-2-3-1"), ("away", "4-1-4-1")):
        assert len(block[side]["starters"]) == 11 and block[side]["formation"] == formation
        assert block[side]["starters"][0]["position"] == "G"  # formation place 1 = goalkeeper
        assert len(block[side]["substitutes"]) == 9
    blob = json.dumps(block)
    assert "subbed" not in blob and "stats" not in blob  # in-match fields are never carried
    assert "announcement" in block["note"]


def test_real_upcoming_match_without_a_lineup_is_unknown_not_a_partial_lineup():
    from src.ingestion.lineups import parse_lineups

    cap = LINEUP_CAPTURE["events"]["upcoming_401879268"]
    block = parse_lineups(cap["summary"], OBSERVED, cap["sha256"], "401879268")
    assert block == {
        "status": "UNKNOWN", "reason": "not_announced_yet", "source": "espn",
        "observed_at": OBSERVED.isoformat(), "event_id": "401879268",
    }  # fmt: skip


def test_a_lineup_with_fewer_than_eleven_starters_on_a_side_is_not_used():
    import copy

    from src.ingestion.lineups import parse_lineups

    summary = copy.deepcopy(LINEUP_CAPTURE["events"]["finished_401879301"]["summary"])
    summary["rosters"][1]["roster"][0]["starter"] = False  # away side now has 10 starters
    assert parse_lineups(summary, OBSERVED)["status"] == "UNKNOWN"


def test_lineups_reach_the_llm_snapshot_only_when_observed_before_the_cutoff():
    from src.ingestion.lineups import parse_lineups

    cap = LINEUP_CAPTURE["events"]["finished_401879301"]
    cutoff = datetime(2026, 10, 9, 11, 0, tzinfo=UTC)
    before = parse_lineups(cap["summary"], cutoff - timedelta(seconds=30), "sha", "e")
    after = parse_lineups(cap["summary"], cutoff + timedelta(seconds=30), "sha", "e")
    sent = build_snapshot(Row({"lineups": before}), cutoff)["permitted_current_information"]
    assert sent["lineups"]["home"]["formation"] == "4-2-3-1" and "observed_at" not in sent["lineups"]
    assert "lineups" not in build_snapshot(Row({"lineups": after}), cutoff)["permitted_current_information"]
    unknown = {"status": "UNKNOWN", "reason": "not_announced_yet"}
    assert "lineups" not in build_snapshot(Row({"lineups": unknown}), cutoff)["permitted_current_information"]


def test_stage_snapshot_uses_the_run_time_as_information_cutoff_and_records_lineups():
    from src.features.history import MatchHistory
    from src.ingestion.lineups import parse_lineups
    from src.snapshot.engine import build_stage_snapshot
    from src.snapshot.stages import SnapshotStage, cutoff_for_stage

    nominal = cutoff_for_stage(Row.kickoff_utc, SnapshotStage.T_90M)
    run_time = nominal + timedelta(minutes=3)
    block = parse_lineups(
        LINEUP_CAPTURE["events"]["finished_401879301"]["summary"], run_time - timedelta(seconds=2)
    )
    kw = {"data_version": "dv-000000000000", "feature_version": "fv2"}
    snap = build_stage_snapshot(
        Row({}), MatchHistory([]), SnapshotStage.T_90M, lineups=block, information_cutoff=run_time, **kw
    )
    assert snap.information_cutoff == run_time and snap.stage_cutoff == nominal
    assert snap.availability["lineups"]["status"] == "OBSERVED"
    assert json.loads(json.dumps(snap.to_dict()))["stage_cutoff"] == nominal.isoformat()
    default = build_stage_snapshot(Row({}), MatchHistory([]), SnapshotStage.T_90M, **kw)
    assert default.information_cutoff == nominal  # no run-time override: the nominal stage cutoff
