"""League-model programme, Sprint 1 (ADR 0063): licence gate, canonical identities, as-of contract, coverage decisions.

Tests marked TEST_ONLY use minimal synthetic data for logic; they are NOT verification of any real data. The real-data
checks read the repository's cached source files (skipped when the cache is absent, as in CI) and the committed audit outputs."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.data.teams import Alias, TeamDirectory
from src.league_models import asof, coverage, identity
from src.league_models.competitions import expected_matches, format_version, load_config
from src.league_models.licenses import (
    LicenseGateError,
    SourceLicense,
    gate_reasons,
    load_manifest,
    manifest_hash,
    training_gate,
)
from src.snapshot.stages import SnapshotStage

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "league_models"
CFG = load_config()
MANIFEST = load_manifest()


def utc(*a: int) -> datetime:
    return datetime(*a, tzinfo=UTC)


# ------------------------------------------------------------------ competitions and licences
def test_nine_competitions_with_canonical_ids_and_formats():
    assert len(CFG.competitions) == 9
    assert {c.repo_key for c in CFG.competitions.values()} == {
        "EPL", "SERIEA", "LALIGA", "BUNDESLIGA", "LIGUE1", "SUPERLIG", "UCL", "UEL", "UECL",
    }  # fmt: skip
    assert CFG.by_repo_key("UCL").canonical_id == "UEFA_UCL"
    assert format_version(CFG, "UEFA_UCL", 2023) == "ucl-groups-32"
    assert format_version(CFG, "UEFA_UCL", 2024) == "ucl-league-36"
    assert format_version(CFG, "UEFA_UEL", 2019) == "uel-groups-48"
    assert expected_matches(CFG, "ENG1", 2024) == 380 and expected_matches(CFG, "GER1", 2024) == 306
    assert expected_matches(CFG, "FRA1", 2022) == 380 and expected_matches(CFG, "FRA1", 2023) == 306
    assert expected_matches(CFG, "TUR1", 2024) is None and expected_matches(CFG, "UEFA_UCL", 2024) is None


def test_manifest_has_every_field_every_source_and_real_evidence_files():
    assert {"openfootball_domestic", "openfootball_cups", "football_data_co_uk", "the_odds_api"} <= set(
        MANIFEST
    )
    for lic in MANIFEST.values():
        assert (ROOT / lic.license_evidence_path).is_file(), lic.provider_id
        assert lic.legal_review_required is True  # nothing here is legal advice or a cleared right
    data = MANIFEST["openfootball_domestic"].model_dump()
    del data["commercial_ml"]
    with pytest.raises(ValidationError):  # a missing right is a loading error, never a silent UNKNOWN
        SourceLicense(**data)
    assert manifest_hash(MANIFEST) == manifest_hash(load_manifest())  # deterministic


def test_training_gate_is_fail_closed():
    training_gate(MANIFEST, ["openfootball_domestic", "openfootball_cups"], "production_ml")
    with pytest.raises(LicenseGateError, match="football_data_co_uk"):
        training_gate(MANIFEST, ["openfootball_domestic", "football_data_co_uk"], "production_ml")
    with pytest.raises(LicenseGateError, match="BLOCKED"):  # BLOCKED also stops offline research
        training_gate(MANIFEST, ["football_data_co_uk"], "research_ml")
    with pytest.raises(LicenseGateError, match="UNKNOWN"):  # an UNKNOWN right is never permission
        training_gate(MANIFEST, ["football_data_org"], "production_ml")
    training_gate(MANIFEST, ["football_data_org", "api_football"], "research_ml")  # research is looser
    with pytest.raises(LicenseGateError, match="not in the licence manifest"):
        training_gate(MANIFEST, ["some_new_provider"], "research_ml")
    assert gate_reasons(MANIFEST["the_odds_api"], "production_ml")  # CONDITIONAL, not PERMITTED
    assert gate_reasons(MANIFEST["api_football"], "display")  # no publication licence
    with pytest.raises(ValueError):
        gate_reasons(MANIFEST["the_odds_api"], "no_such_purpose")


# ------------------------------------------------------------------ as-of contract
def _obs(**over):
    base = {
        "field_group": "historical_results",
        "prediction_as_of_utc": utc(2026, 10, 9, 15),
        "fixture_kickoff_utc": utc(2026, 10, 10, 15),
        "source_event_time_utc": utc(2026, 10, 1, 17),
        "source_available_at_utc": utc(2026, 10, 1, 20),
        "available_at_source": "inferred",
        "ingest_time_utc": utc(2026, 10, 2),
        "source_version": "test-v1",
        "content_hash": "a" * 64,
        "raw_snapshot_pointer": "TEST_ONLY/none",
    }
    return asof.SourceObservation(**{**base, **over})


def test_observation_rejects_malformed_times_hashes_and_unknown_groups_TEST_ONLY():
    _obs()
    for bad in (
        {"prediction_as_of_utc": datetime(2026, 10, 9, 15)},  # naive
        {"content_hash": "xyz"},
        {"field_group": "tea_leaves"},
        {"source_available_at_utc": utc(2026, 9, 30)},  # known before the event happened
        {"prediction_as_of_utc": utc(2026, 10, 11)},  # as_of after kick-off
    ):
        with pytest.raises(ValidationError):
            _obs(**bad)


def test_two_time_checks_and_stage_matrix_catch_leaks_TEST_ONLY():
    assert asof.check_observation(_obs()) == []
    late_event = _obs(
        source_event_time_utc=utc(2026, 10, 9, 16), source_available_at_utc=utc(2026, 10, 9, 17)
    )
    assert {v.code for v in asof.check_observation(late_event)} == {
        "EVENT_AFTER_AS_OF",
        "AVAILABLE_AFTER_AS_OF",
    }
    seen_late = _obs(
        source_available_at_utc=utc(2026, 10, 9, 15, 30)
    )  # happened earlier, we only learned it later
    assert [v.code for v in asof.check_observation(seen_late)] == ["AVAILABLE_AFTER_AS_OF"]
    lineups = _obs(field_group="lineups_confirmed")
    assert [v.code for v in asof.check_observation(lineups, SnapshotStage.T_24H)] == ["STAGE_FORBIDDEN"]
    assert asof.check_observation(lineups, SnapshotStage.T_30M) == []
    for stage in (SnapshotStage.T_24H, SnapshotStage.T_90M, SnapshotStage.T_30M):
        assert asof.stage_availability("odds_closing", stage) is asof.Availability.FORBIDDEN
    assert asof.stage_availability("odds_closing", SnapshotStage.KICKOFF) is asof.Availability.CONDITIONAL
    assert all(
        asof.stage_availability("live_score_state", s) is asof.Availability.FORBIDDEN for s in SnapshotStage
    )
    assert _obs().backtest_limited and asof.quality_label(_obs()) == "BACKTEST_LIMITED"
    assert asof.quality_label(_obs(available_at_source="observed")) == "AS_OF_VERIFIED"


def test_history_never_contains_future_results_or_the_fixture_itself_TEST_ONLY():
    ko = utc(2026, 10, 10, 15)
    lag = 3
    matches = [
        asof.HistoryMatch("old", ko - timedelta(days=7), asof.inferred_available_at(ko - timedelta(days=7), lag)),
        asof.HistoryMatch("same_day_earlier", ko - timedelta(hours=5), asof.inferred_available_at(ko - timedelta(hours=5), lag)),
        asof.HistoryMatch("me", ko, asof.inferred_available_at(ko, lag)),
        asof.HistoryMatch("later", ko + timedelta(days=1), asof.inferred_available_at(ko + timedelta(days=1), lag)),
    ]  # fmt: skip
    as_of = asof.stage_as_of(ko, SnapshotStage.T_30M)
    keys = {m.key for m in asof.eligible_history(matches, as_of, own_key="me")}
    assert keys == {
        "old",
        "same_day_earlier",
    }  # the 5-hour-earlier match finished (kick-off + 3h) before t-30m
    close = utc(2026, 10, 10, 12, 30)  # kick-off of a match whose result is NOT yet known at t-30m
    matches.append(asof.HistoryMatch("too_fresh", close, asof.inferred_available_at(close, lag)))
    assert "too_fresh" not in {m.key for m in asof.eligible_history(matches, as_of, own_key="me")}
    audit = asof.temporal_audit(matches, brute_every=1)
    assert (
        audit.violations
        == audit.own_result_leaks
        == audit.future_kickoff_in_history
        == audit.disagreements
        == 0
    )
    assert audit.checks == len(matches) * 4 and audit.brute_checks == audit.checks


def test_temporal_audit_flags_impossible_availability_TEST_ONLY():
    ko = utc(2026, 10, 10, 15)
    corrupt = [asof.HistoryMatch("x", ko, ko - timedelta(hours=1))]  # result "known" before the match
    assert asof.temporal_audit(corrupt).data_anomalies == 1


def test_training_row_lineage_refuses_a_source_known_after_as_of_TEST_ONLY():
    ok = asof.TrainingRowLineage(
        row_id="r1",
        prediction_as_of_utc=utc(2026, 10, 9, 15),
        max_source_available_at_utc=utc(2026, 10, 9, 14),
        row_hash=asof.row_hash({"a": 1}),
        quality_label="BACKTEST_LIMITED",
    )
    assert ok.row_hash == asof.row_hash({"a": 1}) != asof.row_hash({"a": 2})
    with pytest.raises(ValidationError, match="leakage"):
        asof.TrainingRowLineage(
            row_id="r2",
            prediction_as_of_utc=utc(2026, 10, 9, 15),
            max_source_available_at_utc=utc(2026, 10, 9, 16),
            row_hash=asof.row_hash({"a": 1}),
            quality_label="BACKTEST_LIMITED",
        )


# ------------------------------------------------------------------ identity and coverage logic (synthetic files)
def _directory() -> TeamDirectory:
    d = TeamDirectory()
    for tid, name in (("ENG_alpha", "Alpha FC"), ("ENG_beta", "Beta FC"), ("ENG_gamma", "Gamma FC")):
        d.teams[tid] = {"team_id": tid, "canonical_name": name, "country": "ENG"}
    d.teams["ESP_alpha"] = {
        "team_id": "ESP_alpha",
        "canonical_name": "Alpha FC",
        "country": "ESP",
    }  # same name, other club
    d.aliases.append(Alias("openfootball", "Alpha", "ENG_alpha"))
    return d


def _of_json(matches: list[tuple]) -> bytes:
    rows = []
    for day, time, home, away, score in matches:
        row = {"date": day, "team1": home, "team2": away, "round": "Matchday 1"}
        if time:
            row["time"] = time
        if score:
            row["score"] = {"ft": list(score)}
        rows.append(row)
    return json.dumps({"name": "TEST_ONLY", "matches": rows}).encode()


def test_identity_resolves_by_country_reports_unknown_clubs_and_never_merges_TEST_ONLY():
    comp = CFG.competitions["ENG1"]
    raw = _of_json(
        [
            ("2025-08-16", "15:00", "Alpha FC", "Beta FC", (1, 0)),
            ("2025-08-17", "15:00", "Mystery Town", "Gamma FC", (2, 2)),
        ]
    )
    rows, bad = identity.domestic_rows(comp, 2025, raw, _directory(), "TEST_ONLY.json")
    assert [r.canonical_fixture_id for r in rows] == ["ENG_alpha__ENG_beta__2025-08-16"]  # not ESP_alpha
    assert rows[0].status == "FINISHED" and rows[0].final_score == (1, 0) and rows[0].kickoff_time_known
    assert [(u.raw_name, u.side) for u in bad] == [
        ("Mystery Town", "home")
    ]  # excluded, never guessed or registered
    assert "ENG_mystery_town" not in _directory().teams


def test_collision_audit_and_idempotent_upsert_TEST_ONLY(tmp_path):
    comp = CFG.competitions["ENG1"]
    raw = _of_json(
        [
            ("2025-08-16", "15:00", "Alpha FC", "Beta FC", (1, 0)),
            ("2025-08-23", None, "Beta FC", "Gamma FC", None),
        ]
    )
    rows, _ = identity.domestic_rows(comp, 2025, raw, _directory(), "TEST_ONLY.json")
    assert not any(identity.collision_audit(rows).values())
    assert [r.kickoff_time_known for r in rows] == [True, False] and rows[1].status == "SCHEDULED"
    dup = rows + [rows[0]]
    audit = identity.collision_audit(dup)
    assert audit["duplicate_canonical_fixture_ids"] == {rows[0].canonical_fixture_id: 2}
    assert audit[
        "team_with_two_fixtures_on_one_day"
    ]  # the duplicate also puts a team in two fixtures that day
    path = tmp_path / "map.jsonl"
    dry = identity.upsert_mapping(path, rows)
    assert dry["added"] == 2 and dry["applied"] is False and not path.exists()
    assert identity.upsert_mapping(path, rows, apply=True)["added"] == 2
    again = identity.upsert_mapping(path, rows, apply=True)
    assert (again["added"], again["changed"], again["removed"], again["unchanged"]) == (0, 0, 0, 2)
    assert identity.mapping_hash(rows) == identity.mapping_hash(list(reversed(rows)))  # order independent


def _cache_file(root: Path, year: int, code: str, matches: list[tuple]) -> None:
    folder = root / "artifacts" / "markets" / "_cache" / "openfootball" / f"{year}-{str(year + 1)[2:]}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{code}.json").write_bytes(_of_json(matches))


def _row(root: Path, as_of: datetime, comp="ENG1", season="2025-26", manifest=MANIFEST):
    result = coverage.collect(root, CFG, _directory_all(), manifest, as_of)
    return next(c for c in result.coverage if c.canonical_league_id == comp and c.season == season)


def _directory_all() -> TeamDirectory:
    return _directory()


def test_season_decisions_cover_complete_running_gapped_blocked_and_missing_TEST_ONLY(tmp_path):
    played = [(f"2025-08-{16 + i}", "15:00", "Alpha FC", "Beta FC", (1, 0)) for i in range(10)]
    _cache_file(tmp_path, 2025, "en.1", played)
    done = _row(tmp_path, utc(2026, 7, 1))
    assert done.training_status == "TRAINABLE_COMPLETE" and done.season_over and done.result_coverage == 1.0
    assert (
        "BACKTEST_LIMITED" in done.flags and done.available_at_coverage == 0.0
    )  # results have no observed time
    assert (
        done.shot_coverage == done.xg_coverage == done.lineups_coverage == done.odds_coverage == 0.0
    )  # source has none

    running = played[:6] + [(f"2025-09-{10 + i}", "15:00", "Alpha FC", "Gamma FC", None) for i in range(4)]
    running.append(
        ("2025-12-01", "15:00", "Beta FC", "Gamma FC", None)
    )  # a fixture still in the future: the season is running
    _cache_file(tmp_path, 2025, "en.1", running)
    lag = _row(
        tmp_path, utc(2025, 9, 20)
    )  # four fixtures kicked off but the source has not published their scores yet
    assert lag.training_status == "TRAINABLE_PARTIAL_CURRENT" and not lag.season_over
    assert lag.unpublished_tail_count == 4 and lag.inner_missing_count == 0
    assert any(f.startswith("RESULTS_LAG_TAIL_4") for f in lag.flags)

    hole = played[:4] + [("2025-08-20", "15:00", "Beta FC", "Gamma FC", None)] + played[5:6]
    _cache_file(tmp_path, 2025, "en.1", hole)
    gapped = _row(tmp_path, utc(2026, 7, 1))  # a hole INSIDE the published range of a finished season
    assert gapped.inner_missing_count == 1 and gapped.training_status == "BLOCKED_INCOMPLETE_SOURCE"
    assert gapped.training_reasons and "limit 5%" in gapped.training_reasons[0]

    tail = played[:3] + [(f"2025-09-{10 + i}", "15:00", "Alpha FC", "Gamma FC", None) for i in range(7)]
    _cache_file(tmp_path, 2025, "en.1", tail)
    curtailed = _row(
        tmp_path, utc(2026, 7, 1)
    )  # a finished season whose tail never got scores: gap or cancellation
    assert curtailed.season_over and curtailed.training_status == "BLOCKED_INCOMPLETE_SOURCE"

    missing = _row(tmp_path, utc(2026, 7, 1), comp="ITA1")
    assert missing.training_status == "BLOCKED_NO_DATA" and missing.source_file is None
    assert _row(tmp_path, utc(2026, 7, 1), comp="UEFA_UECL", season="2019-20").training_status == (
        "NOT_APPLICABLE_NOT_YET_EXISTING"
    )


def test_a_blocked_licence_blocks_the_season_even_when_the_data_is_perfect_TEST_ONLY(tmp_path):
    _cache_file(tmp_path, 2025, "en.1", [("2025-08-16", "15:00", "Alpha FC", "Beta FC", (1, 0))])
    unsafe = dict(MANIFEST)
    unsafe["openfootball_domestic"] = MANIFEST["openfootball_domestic"].model_copy(
        update={"plan_status": "BLOCKED"}
    )
    row = _row(tmp_path, utc(2026, 7, 1), manifest=unsafe)
    assert (
        row.training_status == "BLOCKED_LICENSE"
        and "plan_status is BLOCKED, not PERMITTED" in row.training_reasons
    )
    unknown = dict(MANIFEST)
    unknown["openfootball_domestic"] = MANIFEST["openfootball_domestic"].model_copy(
        update={"commercial_ml": "UNKNOWN"}
    )
    assert _row(tmp_path, utc(2026, 7, 1), manifest=unknown).training_status == "BLOCKED_LICENSE"


def test_blocked_research_csvs_are_listed_but_never_opened(tmp_path):
    folder = tmp_path / "data" / "raw" / "football_data"
    folder.mkdir(parents=True)
    (folder / "E0_2425.csv").write_text("TEST_ONLY,not,real\n", encoding="utf-8")
    result = coverage.collect(tmp_path, CFG, _directory(), MANIFEST, utc(2026, 7, 1))
    assert [e["path"] for e in result.excluded_files] == ["data/raw/football_data/E0_2425.csv"]
    assert result.excluded_files[0]["reason"].startswith("plan_status BLOCKED")
    assert not result.source_files and not result.identity_rows  # the CSV contributed nothing


# ------------------------------------------------------------------ real data (cache) and committed outputs
CACHE = ROOT / "artifacts" / "markets" / "_cache" / "openfootball"


@pytest.mark.skipif(
    not CACHE.is_dir(), reason="source cache not present (it is fetched at run time, not committed)"
)
def test_real_cached_data_audit_invariants():
    directory = TeamDirectory.load(ROOT / "configs" / "team_aliases.yaml")
    result = coverage.collect(ROOT, CFG, directory, MANIFEST, utc(2026, 10, 20))
    assert {c.canonical_league_id for c in result.coverage} == set(CFG.competitions)
    assert not any(identity.collision_audit(result.identity_rows).values())
    for c in result.coverage:
        if c.training_status.startswith("TRAINABLE"):
            assert (
                c.license_status == "PERMITTED" and c.source_status == "OK" and "BACKTEST_LIMITED" in c.flags
            )
            assert c.identity_coverage >= CFG.min_identity_coverage
    audit = asof.temporal_audit(
        coverage.history_matches(result.identity_rows, CFG.result_lag_hours), brute_every=60
    )
    assert audit.violations == audit.own_result_leaks == audit.future_kickoff_in_history == 0
    assert audit.data_anomalies == audit.disagreements == 0 and audit.checks == audit.fixtures * 4


def test_committed_audit_outputs_are_consistent():
    """The files the audit command wrote (docs/league_models) agree with the manifest and the plan's rules."""
    rows = json.loads((OUT / "league_coverage.json").read_text(encoding="utf-8"))
    assert {r["canonical_league_id"] for r in rows} == set(CFG.competitions)
    seasons = {r["season"] for r in rows}
    assert len(rows) == 9 * len(seasons)  # one row per competition and season, none dropped
    for r in rows:
        if r["training_status"].startswith("TRAINABLE"):
            assert MANIFEST[r["provider_id"]].plan_status == "PERMITTED"
            assert r["source_status"] == "OK" and r["fixture_count"] > 0 and "BACKTEST_LIMITED" in r["flags"]
        else:
            assert not r["training_status"].startswith("TRAINABLE")
        assert r["provider_id"] != "football_data_co_uk"
    temporal = json.loads((OUT / "temporal_audit.json").read_text(encoding="utf-8"))
    assert (
        temporal["violations"] == temporal["own_result_leaks"] == temporal["future_kickoff_in_history"] == 0
    )
    assert (
        temporal["data_anomalies"] == temporal["disagreements"] == 0
        and temporal["checks"] == temporal["fixtures"] * 4
    )
    ident = json.loads((OUT / "canonical_identity_summary.json").read_text(encoding="utf-8"))
    assert not any(ident["collisions"].values())
    snap = json.loads((OUT / "snapshot_manifest.json").read_text(encoding="utf-8"))
    assert (
        all(len(f["sha256"]) == 64 for f in snap["files"])
        and snap["identity_mapping_sha256"] == ident["mapping_sha256"]
    )
    lic = json.loads((OUT / "data_source_license_manifest.json").read_text(encoding="utf-8"))
    assert lic["manifest_sha256"] == manifest_hash(
        MANIFEST
    )  # the committed report was made from the current manifest


# ------------------------------------------------------------------ cups, universe tiers and the audit command (synthetic)
CUP_TEXT = """= UEFA Champions League 2024/25

▪ League, Matchday 1
  Tue Sep 17 2024
    18:45  Alpha FC (ENG)    v Beta FC (ENG)     2-1 (1-0)
           Beta FC (ENG)     v Gamma FC (ENG)    3-1
  Wed Sep 18
           Alpha FC (ENG)    v Mystery Town (ENG)
           Gamma FC (ENG)    v Alpha FC (ENG)
"""


def test_cup_rows_use_the_90_minute_score_and_exclude_unknown_clubs_TEST_ONLY():
    comp = CFG.competitions["UEFA_UCL"]
    rows, bad = identity.cup_rows(comp, 2024, CUP_TEXT.encode(), _directory(), "TEST_ONLY.txt")
    assert [(r.canonical_fixture_id, r.status, r.final_score) for r in rows] == [
        ("ENG_alpha__ENG_beta__2024-09-17", "FINISHED", (2, 1)),
        ("ENG_beta__ENG_gamma__2024-09-17", "FINISHED", (3, 1)),
        ("ENG_gamma__ENG_alpha__2024-09-18", "SCHEDULED", None),
    ]
    assert rows[0].stage == "League, Matchday 1" and rows[0].kickoff_time_known and rows[1].kickoff_time_known
    assert not rows[2].kickoff_time_known  # the 18 Sep fixture has no time of its own
    assert [u.raw_name for u in bad] == ["Mystery Town"] and rows[0].canonical_league_id == "UEFA_UCL"


def _season(completed: int, comp_id="ENG1", status="TRAINABLE_COMPLETE", season="2024-25"):
    return coverage.SeasonCoverage(
        comp_id, "X", season, "openfootball_domestic", "domestic", "OK", "", None, None,
        completed_count=completed, training_status=status,
    )  # fmt: skip


def test_universe_tiers_follow_the_plans_volume_table_TEST_ONLY():
    summary = {
        s["canonical_league_id"]: s
        for s in coverage.universe_summary(
            [
                _season(300, "TUR1"),
                _season(1000, "ESP1"),
                _season(1600, "ENG1"),
                _season(100, "UEFA_UEL"),
                _season(380, "ENG1", status="BLOCKED_NO_DATA", season="2021-22"),
            ]
        )
    }
    assert summary["TUR1"]["plan_volume_tier"].startswith("UNDER_500")
    assert summary["ESP1"]["plan_volume_tier"].startswith("500_TO_1500")
    assert summary["ENG1"]["plan_volume_tier"].startswith("OVER_1500")
    assert summary["ENG1"]["blocked_seasons"] == {"2021-22": "BLOCKED_NO_DATA"}
    assert summary["ENG1"]["completed_matches_in_trainable_seasons"] == 1600  # a blocked season never counts
    assert "cross-league bridge" in summary["UEFA_UEL"]["note"]


def _audit_root(tmp_path: Path) -> Path:
    (tmp_path / "configs").mkdir()
    teams = [
        {"team_id": t, "canonical_name": n, "country": "ENG"}
        for t, n in (("ENG_alpha", "Alpha FC"), ("ENG_beta", "Beta FC"))
    ]
    import yaml

    (tmp_path / "configs" / "team_aliases.yaml").write_text(
        yaml.safe_dump({"teams": teams, "aliases": []}), encoding="utf-8"
    )
    _cache_file(tmp_path, 2025, "en.1", [("2025-08-16", "15:00", "Alpha FC", "Beta FC", (1, 0))])
    return tmp_path


def test_audit_command_writes_every_output_deterministically_TEST_ONLY(tmp_path, capsys):
    from src.league_models import audit

    root = _audit_root(tmp_path)
    argv = ["--root", str(root), "--out", "out", "--as-of", "2026-07-01T00:00:00Z"]
    assert audit.main(argv) == 0 and not (root / "out").exists()  # without --write nothing is written
    dry = json.loads(capsys.readouterr().out)
    assert dry["summary"]["seasons_audited"] == 9 * 8 and dry["summary"]["temporal_audit"]["violations"] == 0
    assert audit.main(argv + ["--write"]) == 0
    first = {p.name: p.read_bytes() for p in (root / "out").iterdir()}
    ident_out = json.loads(first["canonical_identity_summary.json"])
    assert (
        ident_out["mapping_file"] == "artifacts/league_models/identity_mapping.jsonl"
    )  # never an absolute path
    assert {
        "league_coverage.csv",
        "league_coverage.json",
        "snapshot_manifest.json",
        "temporal_audit.json",
    } <= set(first)
    assert len(first) == 8
    capsys.readouterr()
    assert audit.main(argv + ["--write"]) == 0
    assert {p.name: p.read_bytes() for p in (root / "out").iterdir()} == first  # idempotent, byte for byte
    again = json.loads(capsys.readouterr().out)["summary"]
    assert (
        again["identity_mapping_upsert"]["added"] == 0 and again["identity_mapping_upsert"]["unchanged"] == 1
    )
    assert (
        again["training_status_counts"]["TRAINABLE_COMPLETE"] == 1
    )  # only the one synthetic Premier League season
    snap = json.loads((root / "out" / "snapshot_manifest.json").read_text(encoding="utf-8"))
    assert [f["path"] for f in snap["files"]] == ["artifacts/markets/_cache/openfootball/2025-26/en.1.json"]
    with pytest.raises(SystemExit):
        audit.main(["--as-of", "2026-07-01T00:00:00"])  # a naive time is refused


def test_license_report_lists_gate_decisions_per_purpose_TEST_ONLY():
    from src.league_models import audit

    report = audit.license_report(MANIFEST, ROOT)
    assert report["sources"]["openfootball_domestic"]["gate"]["production_ml"] == "ALLOWED"
    assert report["sources"]["football_data_co_uk"]["gate"]["research_ml"] == ["plan_status is BLOCKED"]
    assert report["sources"]["api_football"]["evidence_file_exists"] is True
    assert report["by_plan_status"]["BLOCKED"] == ["football_data_co_uk"]
