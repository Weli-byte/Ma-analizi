import hashlib
import json
from datetime import UTC, date, datetime

import pytest
from test_data_provenance import rewrite_raw

from src.data.dataset import DatasetError, open_db, resolve_dataset
from src.data.manifest import ChecksumMismatch
from src.data.pipeline import STAGES, PipelineError, run_pipeline
from src.runmode import RunMode

AS_OF = date(2026, 9, 25)


def edit_raw(project, name, fn):
    path = project / "data/raw/football_data" / name
    rewrite_raw(project, name, fn(path.read_text(encoding="utf-8")))


def run(project, mode=RunMode.STRICT, **kw):
    kw.setdefault("as_of", AS_OF)
    return run_pipeline(project, mode, **kw)


def pointer(project):
    return json.loads((project / "data/processed/CURRENT.json").read_text())


def tree_hash(path):
    h = hashlib.sha256()
    for p in sorted(path.rglob("*")):
        if p.is_file():
            h.update(p.name.encode() + p.read_bytes())
    return h.hexdigest()


# ------------------------------------------------------------------------ happy path
def test_strict_pipeline_builds_versioned_dataset_and_pointer(project):
    res = run(project)
    assert res.data_version.startswith("dv-") and not res.unchanged
    assert pointer(project)["data_version"] == res.data_version
    ref = resolve_dataset(project / "data/processed")
    assert ref.data_version == res.data_version
    meta = ref.meta
    assert meta["timezone_conversion_version"].startswith("tzconv-") and meta["as_of"] == "2026-09-25"
    assert {f["origin"] for f in meta["raw_files"]} == {"manual"}
    con = open_db(ref.db_path)
    assert con.execute("SELECT count(*) FROM fixtures").fetchone()[0] == 36
    assert con.execute("SELECT count(*) FROM results").fetchone()[0] == 36
    first = con.execute(
        "SELECT kickoff_utc, result_available_at_utc, result_available_at_source FROM fixtures "
        "WHERE fixture_id LIKE 'DEMO_2021-22_20210813%'"
    ).fetchone()
    # 13/08/2021 20:00 BST == 19:00 UTC; result availability inferred = kickoff + 3h
    assert first[0] == datetime(2021, 8, 13, 19, 0, tzinfo=UTC)
    assert first[1] == datetime(2021, 8, 13, 22, 0, tzinfo=UTC) and first[2] == "inferred"
    assert con.execute("SELECT count(*) FROM fixtures WHERE NOT kickoff_time_known").fetchone()[0] > 0
    assert {r[0] for r in con.execute("SELECT DISTINCT season_status FROM fixtures").fetchall()} == {
        "historical_complete"
    }
    con.close()


def test_odds_semantics_are_explicit(project):
    con = open_db(run(project).path / "football.duckdb")
    rows = con.execute(
        "SELECT DISTINCT snapshot_type, market_source_type, aggregate_kind, timestamp_quality, "
        "bookmaker IS NULL FROM odds_snapshots"
    ).fetchall()
    assert ("closing", "aggregate", "avg", "unknown", True) in rows
    assert ("pre_match", "bookmaker", None, "unknown", False) in rows
    assert not con.execute("SELECT count(*) FROM odds_snapshots WHERE bookmaker IN ('Avg','Max')").fetchone()[
        0
    ]
    assert not con.execute("SELECT count(*) FROM odds_snapshots WHERE timestamp_utc IS NOT NULL").fetchone()[
        0
    ]
    assert not con.execute(
        "SELECT count(*) FROM odds_snapshots WHERE timestamp_quality = 'exact'"
    ).fetchone()[0]
    con.close()


def test_rerun_is_idempotent_and_reproduces_content_hash(project):
    a = run(project)
    hash_a = resolve_dataset(project / "data/processed").meta["content_hash"]
    b = run(project)
    assert b.unchanged and b.data_version == a.data_version
    assert resolve_dataset(project / "data/processed").meta["content_hash"] == hash_a
    assert pointer(project)["content_hash"] == hash_a
    assert list((project / "data/processed").glob(".tmp-*")) == []


def test_raw_change_creates_new_version_and_keeps_the_old_one(project):
    a = run(project)
    edit_raw(
        project,
        "TST_2122.csv",
        lambda t: t.replace(",2,0,H,", ",3,0,H,", 1),
    )
    b = run(project)
    assert b.data_version != a.data_version
    assert (project / "data/processed" / a.data_version).is_dir()  # old version retained
    assert pointer(project)["data_version"] == b.data_version


# ---------------------------------------------------------------------- run modes
def test_missing_provenance_fails_research_and_strict_but_not_development(project):
    (project / "data/provenance/football_data/TST_2122.csv.json").unlink()
    with pytest.raises(PipelineError, match="incomplete provenance"):
        run(project, RunMode.RESEARCH)
    with pytest.raises(PipelineError, match="incomplete provenance"):
        run(project, RunMode.STRICT)
    assert run(project, RunMode.DEVELOPMENT).data_version


def test_unknown_checksum_fails_strict_only(project):
    exp = project / "data/expected_checksums.json"
    data = json.loads(exp.read_text())
    del data["files"]["TST_2223.csv"]
    exp.write_text(json.dumps(data))
    assert run(project, RunMode.RESEARCH).data_version
    with pytest.raises(PipelineError, match="checksum"):
        run(project, RunMode.STRICT)


def test_corrupted_raw_fails_checksum_and_keeps_current_dataset(project):
    good = run(project)
    before = tree_hash(good.path)
    raw = project / "data/raw/football_data/TST_2122.csv"
    raw.write_text(raw.read_text().replace(",2,0,H,", ",3,0,H,", 1), encoding="utf-8", newline="\n")
    with pytest.raises(ChecksumMismatch):
        run(project)
    assert pointer(project)["data_version"] == good.data_version and tree_hash(good.path) == before


# ------------------------------------------------------------- season categories
def test_partial_current_season_is_categorised_not_an_error(project):
    edit_raw(project, "TST_2324.csv", lambda t: "\n".join(t.splitlines()[:6]) + "\n")  # 5 of 12 matches
    res = run(project, as_of=date(2024, 2, 1))  # inside the 2023-24 window
    assert res.report["seasons"]["DEMO|2023-24"]["status"] == "current_partial"
    assert res.report["seasons"]["DEMO|2022-23"]["status"] == "historical_complete"
    assert not res.report["summary"]["errors"]


def test_missing_matches_in_a_finished_season_fail_the_quality_gate(project):
    edit_raw(project, "TST_2324.csv", lambda t: "\n".join(t.splitlines()[:-1]) + "\n")  # 11 of 12
    with pytest.raises(PipelineError, match="Q13"):
        run(project, as_of=AS_OF)  # season ended long ago -> incomplete_historical


def add_raw_file(project, name, text):
    """Register a brand-new raw file (content + sidecar + pin), as a legitimate download would."""
    path = project / "data/raw/football_data" / name
    path.write_text(text, encoding="utf-8", newline="\n")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    exp = project / "data/expected_checksums.json"
    data = json.loads(exp.read_text())
    data["files"][name] = {"sha256": digest, "status": "pinned_observed", "basis": "test"}
    exp.write_text(json.dumps(data))
    (project / "data/provenance/football_data" / f"{name}.json").write_text(
        json.dumps(
            {
                "filename": name,
                "source_id": "manual",
                "source_url": "u",
                "origin": "manual",
                "retrieved_at_utc": "2026-09-25T00:00:00+00:00",
                "sha256": digest,
                "note": "",
            }
        )
    )


def test_future_fixture_rows_are_scheduled_without_results(project):
    header = (project / "data/raw/football_data/TST_2324.csv").read_text().splitlines()[0]
    width = len(header.split(","))
    future = "TST,05/03/2027,15:00,Alpha FC,Beta Utd" + "," * (width - 5)  # no score yet
    add_raw_file(project, "TST_2627.csv", header + "\n" + future + "\n")
    cfg = project / "configs/data.yaml"
    cfg.write_text(cfg.read_text().replace('"2023-24"]', '"2023-24", "2026-27"]'))
    res = run(project, RunMode.DEVELOPMENT, as_of=AS_OF)
    con = open_db(res.path / "football.duckdb")
    rows = con.execute("SELECT status, season_status FROM fixtures WHERE season='2026-27'").fetchall()
    assert rows == [("scheduled", "future_fixture")]
    assert con.execute("SELECT count(*) FROM results WHERE fixture_id LIKE '%2026-27%'").fetchone()[0] == 0
    con.close()


# --------------------------------------------------------------------- team resolution
def test_unknown_team_is_never_auto_mapped_and_fails_the_gate(project):
    edit_raw(project, "TST_2122.csv", lambda t: t.replace("Gamma Town", "Gamma Twn", 1))
    with pytest.raises(PipelineError, match="Q10"):
        run(project, RunMode.RESEARCH)
    failed = json.loads((project / "data/processed/last_failed_quality_report.json").read_text())
    assert failed["unmatched_teams"][0]["raw_name"] == "Gamma Twn"
    assert failed["unmatched_teams"][0]["suggestions"][0]["team_id"] == "TST_gamma_town"  # suggestion only
    res = run(project, RunMode.DEVELOPMENT)  # development builds anyway; the row is rejected, not guessed
    assert res.report["rejected_by_reason"]["unmatched_team"] >= 1


# ---------------------------------------------------------------- atomicity / crash safety
@pytest.mark.parametrize("stage", STAGES)
def test_failure_injection_leaves_current_dataset_untouched(project, stage):
    good = run(project)
    before, ptr = tree_hash(good.path), pointer(project)
    edit_raw(project, "TST_2122.csv", lambda t: t.replace(",2,0,H,", ",3,0,H,", 1))  # would be a new version

    def boom(s):
        if s == stage:
            raise RuntimeError(f"injected failure at {s}")

    with pytest.raises(RuntimeError, match="injected"):
        run(project, stage_hook=boom)
    assert pointer(project) == ptr and tree_hash(good.path) == before
    assert resolve_dataset(project / "data/processed").data_version == good.data_version
    assert list((project / "data/processed").glob(".tmp-*")) == []  # no half-built directories left


def test_process_crash_leaves_old_dataset_and_next_run_cleans_up(project):
    good = run(project)
    before, ptr = tree_hash(good.path), pointer(project)
    edit_raw(project, "TST_2122.csv", lambda t: t.replace(",2,0,H,", ",3,0,H,", 1))

    def crash(s):
        if s == "commit":
            raise KeyboardInterrupt  # simulates the process being killed mid-run

    with pytest.raises(KeyboardInterrupt):
        run(project, stage_hook=crash)
    assert pointer(project) == ptr and tree_hash(good.path) == before
    stale = project / "data/processed/.tmp-999-deadbeef"  # what a hard kill would leave behind
    stale.mkdir()
    (stale / "junk").write_text("x")
    fresh = run(project)
    assert not stale.exists() and fresh.data_version != good.data_version


def test_dataset_pointer_errors_are_loud(project):
    with pytest.raises(DatasetError, match="no dataset pointer"):
        resolve_dataset(project / "data/processed")
    run(project)
    with pytest.raises(DatasetError, match="not found"):
        resolve_dataset(project / "data/processed", "dv-000000000000")


def test_same_version_with_different_content_is_refused(project):
    res = run(project)
    meta = res.path / "dataset_meta.json"
    d = json.loads(meta.read_text())
    d["content_hash"] = "0" * 64
    meta.write_text(json.dumps(d))
    with pytest.raises(PipelineError, match="different content"):
        run(project)
