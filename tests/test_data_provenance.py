import json

import pytest

from src.config import DataConfig, load_config
from src.data.leagues import season_date_window, season_from_code, season_to_code
from src.data.manifest import ChecksumMismatch, build_manifest, parse_filename
from src.data.raw_validation import RawFileError, sha256_bytes, validate_raw_bytes
from src.data.versioning import compute_data_version, normalization_inputs_hash

HEADER = "Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n"
ROW = "TST,13/08/2021,15:00,Alpha FC,Beta Utd,2,0,H\n"


def league(project):
    return load_config("leagues", project / "configs").leagues["DEMO"]


def cfgs(project):
    return load_config("leagues", project / "configs"), load_config("data", project / "configs")


def manifest(project):
    leagues, data_cfg = cfgs(project)
    return build_manifest(
        project / data_cfg.raw_dir, project / data_cfg.provenance_dir,
        project / data_cfg.expected_checksums, leagues, data_cfg,
    )  # fmt: skip


def rewrite_raw(project, name, content):
    """Change a raw file AND keep its sidecar/pin consistent (simulates a legitimate new download)."""
    raw = project / "data/raw/football_data" / name
    data = content if isinstance(content, bytes) else content.encode("utf-8")
    raw.write_bytes(data.replace(b"\r\n", b"\n"))
    digest = sha256_bytes(raw.read_bytes())
    side = project / "data/provenance/football_data" / f"{name}.json"
    rec = json.loads(side.read_text())
    rec["sha256"] = digest
    side.write_text(json.dumps(rec))
    exp = project / "data/expected_checksums.json"
    data = json.loads(exp.read_text())
    data["files"][name]["sha256"] = digest
    exp.write_text(json.dumps(data))


# ------------------------------------------------------------------- raw validation
@pytest.mark.parametrize(
    "content,match",
    [
        (b"", "empty"),
        (b"<!DOCTYPE html><html><body>blocked</body></html>", "HTML"),
        (b"   \n<html lang='en'>x", "HTML"),
        (b"Foo,Bar\n1,2\n", "invalid header"),
        (HEADER.encode(), "header only"),
        ((HEADER + ROW + "TST,14/08/2021,15:00,A,B,1\n").encode(), "truncated"),
        ((HEADER + "TST,1,2\n" + ROW).encode(), "malformed"),
        ((HEADER + ROW.replace("TST", "E0")).encode(), "Div values"),
    ],
)
def test_corrupt_raw_files_are_rejected(project, content, match):
    with pytest.raises(RawFileError, match=match):
        validate_raw_bytes("X.csv", content, league(project))


def test_valid_raw_file_reports_schema_and_encoding(project):
    rep = validate_raw_bytes("X.csv", (HEADER + ROW).encode(), league(project))
    assert rep.n_rows == 1 and rep.encoding == "utf-8" and len(rep.schema_hash) == 16
    latin = validate_raw_bytes(
        "X.csv", (HEADER + ROW.replace("Alpha", "Alphá")).encode("latin-1"), league(project)
    )
    assert latin.encoding == "latin-1" and latin.warnings


def test_filename_and_season_helpers(project):
    leagues, _ = cfgs(project)
    assert parse_filename("TST_2223.csv", leagues) == ("DEMO", "2022-23")
    for bad in ("foo.csv", "TST_2224.csv", "XXX_2223.csv", "TST_2223.txt"):
        with pytest.raises(ValueError):
            parse_filename(bad, leagues)
    assert season_from_code("2324") == "2023-24" and season_to_code("2023-24") == "2324"
    with pytest.raises(ValueError):
        season_from_code("2325")
    assert season_date_window("2023-24", league(project)) == ((2023, 7), (2024, 8))


# --------------------------------------------------------------------------- manifest
def test_manifest_has_complete_honest_provenance(project):
    m = manifest(project)
    assert len(m.entries) == 3 and not m.problems and not m.ignored
    e = m.entries[0]
    for field in (
        "dataset_id",
        "source_url",
        "origin",
        "retrieved_at_utc",
        "sha256",
        "size_bytes",
        "season",
        "league",
        "filename",
        "schema_hash",
        "parser_version",
    ):
        assert getattr(e, field) not in (None, ""), field
    assert e.origin == "manual" and e.expected_checksum_status == "pinned_observed"
    assert json.loads(m.to_json())["entries"][0]["filename"] == e.filename


def test_missing_provenance_and_unknown_checksum_are_reported_never_invented(project):
    (project / "data/provenance/football_data/TST_2122.csv.json").unlink()
    exp = project / "data/expected_checksums.json"
    data = json.loads(exp.read_text())
    del data["files"]["TST_2223.csv"]
    exp.write_text(json.dumps(data))
    m = manifest(project)
    by = {e.filename: e for e in m.entries}
    assert by["TST_2122.csv"].origin == "unknown" and by["TST_2122.csv"].retrieved_at_utc is None
    assert by["TST_2223.csv"].expected_checksum_status == "unknown"
    assert any(p.startswith("provenance:") for p in m.problems)
    assert any(p.startswith("checksum:") for p in m.problems)


def test_corrupted_raw_file_fails_checksum(project):
    raw = project / "data/raw/football_data/TST_2122.csv"
    raw.write_text(raw.read_text().replace("Alpha FC", "Alpha  FC", 1), encoding="utf-8", newline="\n")
    with pytest.raises(ChecksumMismatch):
        manifest(project)


def test_html_or_empty_raw_file_fails_manifest(project):
    raw = project / "data/raw/football_data/TST_2223.csv"
    raw.write_text("<html>error</html>")
    with pytest.raises(RawFileError, match="HTML"):
        manifest(project)
    raw.write_text("")
    with pytest.raises(RawFileError, match="empty"):
        manifest(project)


def test_files_outside_configured_scope_are_ignored_not_ingested(project):
    (project / "data/raw/football_data/TST_2425.csv").write_text(HEADER + ROW)
    (project / "data/raw/football_data/notes.csv").write_text("x")
    m = manifest(project)
    assert len(m.entries) == 3
    assert len(m.ignored) == 2


def test_invalid_origin_in_sidecar_is_rejected(project):
    side = project / "data/provenance/football_data/TST_2122.csv.json"
    rec = json.loads(side.read_text())
    rec["origin"] = "trust-me"
    side.write_text(json.dumps(rec))
    with pytest.raises(RawFileError, match="origin"):
        manifest(project)


def test_sidecar_hash_mismatch_detects_post_download_edits(project):
    side = project / "data/provenance/football_data/TST_2122.csv.json"
    rec = json.loads(side.read_text())
    rec["sha256"] = "0" * 64
    side.write_text(json.dumps(rec))
    with pytest.raises(ChecksumMismatch, match="retrieval time"):
        manifest(project)


# ------------------------------------------------------------------------ data version
def test_data_version_is_content_derived(project):
    leagues, data_cfg = cfgs(project)
    alias = project / "configs/team_aliases.yaml"
    nh = normalization_inputs_hash(leagues, ["DEMO"], alias)
    v1 = compute_data_version(manifest(project).entries, nh)
    assert v1.startswith("dv-") and len(v1) == 15
    assert compute_data_version(manifest(project).entries, nh) == v1  # deterministic
    # raw change -> new version
    raw = (project / "data/raw/football_data/TST_2122.csv").read_text()
    rewrite_raw(project, "TST_2122.csv", raw.replace("Alpha FC", "Delta City", 1))
    assert compute_data_version(manifest(project).entries, nh) != v1
    # normalization input change (alias store) -> new version even if raw is identical
    alias.write_text(alias.read_text() + "\n# touched\n")
    assert normalization_inputs_hash(leagues, ["DEMO"], alias) != nh
    # league format change
    changed = leagues.model_copy(
        update={"leagues": {"DEMO": leagues.leagues["DEMO"].model_copy(update={"n_teams": 5})}}
    )
    assert normalization_inputs_hash(changed, ["DEMO"], alias) != nh
    assert isinstance(data_cfg, DataConfig)
