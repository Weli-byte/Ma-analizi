"""Ingested finished results -> live match history (ADR 0032). Inputs are the REAL football-data.org
match payloads captured on 2026-10-02 (a finished Arsenal v Coventry City 3-0 and a scheduled
Arsenal v Leeds United), parsed by the production adapter code."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.data.teams import TeamDirectory
from src.features.history import MatchHistory, MatchRecord
from src.ingestion.football_data_org import raw_fixture
from src.ingestion.results import ingest_finished, ingested_matches, merge_history, read_store
from src.schemas import FixtureStatus

ROOT = Path(__file__).resolve().parents[1]
CAP = json.loads(
    (ROOT / "tests" / "fixtures" / "real_provider_captures" / "fdorg_matches.json").read_text(
        encoding="utf-8"
    )
)
DIRECTORY = TeamDirectory.load(ROOT / "configs" / "team_aliases.yaml")
FINISHED = raw_fixture(next(m for m in CAP["matches"] if m["status"] == "FINISHED"), "PL", "2026-27")
SCHEDULED = raw_fixture(next(m for m in CAP["matches"] if m["status"] == "TIMED"), "PL", "2026-27")
SEEN = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)


def test_real_payloads_convert_with_the_production_adapter():
    assert (FINISHED.status_raw, FINISHED.home_goals, FINISHED.away_goals) == ("FT", 3, 0)
    assert FINISHED.home_team_raw_name == "Arsenal FC" and FINISHED.provider_fixture_id == "560542"
    assert SCHEDULED.status_raw == "NS" and SCHEDULED.home_goals is None


def test_only_finished_resolved_matches_are_stored_once(tmp_path):
    counts = ingest_finished(tmp_path, [FINISHED, SCHEDULED], DIRECTORY, "ENG", "EPL", SEEN)
    assert counts == {"new": 1, "known": 0, "unresolved": 0, "not_finished": 1}
    again = ingest_finished(
        tmp_path, [FINISHED, SCHEDULED], DIRECTORY, "ENG", "EPL", SEEN + timedelta(days=1)
    )
    assert again["new"] == 0 and again["known"] == 1  # re-polling never duplicates
    (row,) = read_store(tmp_path)
    assert (row["home_id"], row["away_id"], row["home_goals"], row["first_seen_utc"]) == (
        "ENG_arsenal",
        "ENG_coventry_city",
        3,
        SEEN.isoformat(),
    )


def test_unresolved_team_names_are_counted_not_auto_registered(tmp_path):
    odd = raw_fixture({**CAP["matches"][1], "homeTeam": {"name": "Nowhere Rovers FC"}}, "PL", "2026-27")
    assert ingest_finished(tmp_path, [odd], DIRECTORY, "ENG", "EPL", SEEN)["unresolved"] == 1
    assert read_store(tmp_path) == []


def test_result_becomes_usable_only_from_the_moment_it_was_first_seen(tmp_path):
    ingest_finished(tmp_path, [FINISHED], DIRECTORY, "ENG", "EPL", SEEN)
    (m,) = ingested_matches(tmp_path)
    assert m.result_available_at_utc == SEEN and m.result_available_at_utc > m.kickoff_utc  # never backdated
    history = MatchHistory([m])
    before = history.eligible("ENG_arsenal", SEEN - timedelta(seconds=1))
    after = history.eligible("ENG_arsenal", SEEN + timedelta(seconds=1))
    assert before == [] and len(after) == 1 and (after[0].gf, after[0].ga) == (3, 0)


def test_merge_does_not_double_count_a_match_the_dataset_already_has(tmp_path):
    ingest_finished(tmp_path, [FINISHED], DIRECTORY, "ENG", "EPL", SEEN)
    ingested = ingested_matches(tmp_path)
    in_dataset = MatchRecord(
        "EPL_2026-27_x", "2026-27", FINISHED.kickoff_utc, "ENG_arsenal", "ENG_coventry_city", 3, 0,
        FINISHED.kickoff_utc + timedelta(hours=3), FixtureStatus.FINISHED,
    )  # fmt: skip
    assert merge_history([in_dataset], ingested) == [in_dataset]  # same teams, same day: kept once
    other = MatchRecord(
        "EPL_2026-27_y", "2026-27", FINISHED.kickoff_utc - timedelta(days=7), "ENG_chelsea", "ENG_fulham", 1, 1,
        FINISHED.kickoff_utc - timedelta(days=7, hours=-3), FixtureStatus.FINISHED,
    )  # fmt: skip
    merged = merge_history([other], ingested)
    assert [m.fixture_id for m in merged] == ["EPL_2026-27_y", "fdorg-560542"]  # chronological, both kept
