import json
from pathlib import Path

import duckdb
import pytest

from src.data.clean import CleanRow, Rejected, clean_row
from src.data.leagues import LEAGUES, season_from_code
from src.data.manifest import build_manifest, parse_filename
from src.data.pipeline import run_pipeline
from src.data.teams import TeamRegistry, normalize_name

ALIASES = Path(__file__).resolve().parents[1] / "configs" / "team_aliases.yaml"
HEADER = (
    "Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HS,AS,B365H,B365D,B365A,B365CH,B365CD,B365CA"
)
GOOD = [
    "E0,12/08/2023,20:00,Man City,Arsenal,2,1,H,15,8,1.5,4.0,6.0,1.4,4.5,7.0",
    "E0,13/08/2023,,Man United,Tottenham,1,1,D,,,2.0,3.5,3.8,,,",
    "E0,14/08/2023,15:00,Liverpool,Chelsea,0,3,A,10,12,2.1,3.6,3.5,2.0,3.7,3.9",
]


def write_raw(raw: Path, name: str, rows: list[str]) -> None:
    raw.mkdir(parents=True, exist_ok=True)
    (raw / name).write_text("\n".join([HEADER, *rows]) + "\n", encoding="utf-8")


@pytest.fixture
def teams():
    return TeamRegistry.from_alias_file(ALIASES)


def row(line: str) -> dict:
    return dict(zip(HEADER.split(","), line.split(","), strict=False))


def clean(line, teams):
    return clean_row(row(line), LEAGUES["EPL"], "2023-24", teams)


def test_season_and_filename():
    assert season_from_code("2324") == "2023-24"
    assert parse_filename("SP1_1920.csv") == ("LALIGA", "2019-20")
    with pytest.raises(ValueError):
        parse_filename("foo.csv")
    with pytest.raises(ValueError):
        season_from_code("2325")


def test_normalize_and_mapping(teams):
    assert normalize_name("Nott'm Forest") == "nottm forest"
    a = teams.resolve("Man City", "ENG")
    assert a == teams.resolve("Manchester City", "ENG") == "ENG_manchester_city"


def test_ambiguous_team_goes_to_review_queue(teams):
    teams.resolve("Arsenal", "ENG")
    assert teams.resolve("Arsenall", "ENG") is None
    assert teams.review_queue["ENG:arsenall"]["suggestion"] == "Arsenal"


def test_clean_good_row(teams):
    r = clean(GOOD[0], teams)
    assert isinstance(r, CleanRow)
    assert r.fixture.outcome.value == "H" and r.kickoff_time_known
    assert r.fixture.kickoff_utc.hour == 19  # 20:00 BST -> 19:00 UTC
    assert len(r.odds) == 6 and {o["snapshot_kind"] for o in r.odds} == {
        "pre_match_unspecified",
        "closing",
    }


def test_missing_values_not_zeroed(teams):
    r = clean(GOOD[1], teams)
    assert not r.kickoff_time_known
    assert r.fixture.kickoff_utc.hour == 0
    assert all(s["shots"] is None for s in r.stats)
    assert {o["snapshot_kind"] for o in r.odds} == {"pre_match_unspecified"}


@pytest.mark.parametrize(
    "line,reason",
    [
        ("E0,12/08/2023,,Man City,Arsenal,-1,1,A,,,,,,,,", "invalid_score"),
        ("E0,12/08/2023,,Man City,Arsenal,,,,,,,,,,,", "missing_score"),
        ("E0,12/08/2023,,Man City,Arsenal,2,1,A,,,,,,,,", "result_mismatch"),
        ("E0,32/13/2023,,Man City,Arsenal,2,1,H,,,,,,,,", "invalid_date"),
        ("E0,12/08/2023,,Arsenal,Arsenal,1,1,D,,,,,,,,", "schema_violation"),
        ("E0,12/08/2023,,,Arsenal,1,1,D,,,,,,,,", "missing_team"),
        (",,,,,,,,,,,,,,,", "blank_row"),
    ],
)
def test_invalid_rows_rejected(teams, line, reason):
    r = clean(line, teams)
    assert isinstance(r, Rejected) and r.reason == reason


def test_invalid_odds_nulled_and_counted(teams):
    r = clean("E0,12/08/2023,,Man City,Arsenal,2,1,H,,,0.9,4.0,6.0,,,", teams)
    assert isinstance(r, CleanRow) and r.odds == [] and r.invalid["odds_le_1"] == 1


def test_manifest_idempotent(tmp_path):
    raw = tmp_path / "raw"
    write_raw(raw, "E0_2324.csv", GOOD)
    m1 = build_manifest(raw, raw / "manifest.json")
    m2 = build_manifest(raw, raw / "manifest.json")
    assert m1 == m2 and len(m1[0].checksum_sha256) == 64
    assert (m1[0].source, m1[0].league_id, m1[0].season) == (
        "football-data.co.uk",
        "EPL",
        "2023-24",
    )
    write_raw(raw, "E0_2324.csv", GOOD[:2])  # changed content -> new checksum
    assert build_manifest(raw, raw / "manifest.json")[0].checksum_sha256 != m1[0].checksum_sha256


def snapshot(path: Path) -> dict:
    con = duckdb.connect(str(path), read_only=True)
    out = {
        t: con.execute(f"SELECT * FROM {t} ORDER BY ALL").fetchall()
        for t in ("fixtures", "results", "team_match_stats", "odds_snapshots", "teams")
    }
    con.close()
    return out


def test_pipeline_idempotent_and_dedup(tmp_path):
    raw, out, rep = tmp_path / "raw", tmp_path / "out", tmp_path / "rep"
    dup = GOOD + [GOOD[0], "E0,15/08/2023,,Man City,Nonexistent FC,1,0,H,,,,,,,,"]
    write_raw(raw, "E0_2324.csv", dup)
    r1 = run_pipeline(raw, out, "dv1", rep, ALIASES)
    s1 = snapshot(out / "dv1" / "football.duckdb")
    r2 = run_pipeline(raw, out, "dv1", rep, ALIASES)
    s2 = snapshot(out / "dv1" / "football.duckdb")
    assert s1 == s2 and r1 == r2  # idempotent
    assert r1["summary"]["accepted_fixtures"] == 4  # 3 good + Nonexistent FC (new team, ok)
    assert r1["summary"]["duplicates"] == 1
    ids = [f[0] for f in s1["fixtures"]]
    assert len(ids) == len(set(ids))
    assert (out / "dv1" / "fixtures.parquet").exists()
    assert not r1["summary"]["meets_dod_5_seasons_2_leagues"]
    assert json.loads((rep / "data_quality_dv1.json").read_text())["dataset_version"] == "dv1"
    assert (rep / "data_quality_dv1.md").exists()


def test_pipeline_unmatched_team_reported(tmp_path):
    raw, out, rep = tmp_path / "raw", tmp_path / "out", tmp_path / "rep"
    write_raw(raw, "E0_2324.csv", [GOOD[0], "E0,15/08/2023,,Man Cityy,Arsenal,1,0,H,,,,,,,,"])
    r = run_pipeline(raw, out, "dv1", rep, ALIASES)
    assert r["summary"]["unmatched_team_rows"] == 1
    assert r["unmatched_teams"][0]["suggestion"] == "Manchester City"
