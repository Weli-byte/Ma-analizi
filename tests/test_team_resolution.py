from datetime import date

import pytest
from test_pipeline import edit_raw, run

from src.data import team_resolution as cli
from src.data.pipeline import PipelineError
from src.data.teams import Alias, TeamDirectory, normalize_name
from src.runmode import RunMode


def directory():
    d = TeamDirectory()
    d.teams = {
        "ENG_manchester_city": {"team_id": "ENG_manchester_city", "canonical_name": "Manchester City", "country": "ENG"},
        "ENG_manchester_united": {"team_id": "ENG_manchester_united", "canonical_name": "Manchester United", "country": "ENG"},
        "ESP_barcelona": {"team_id": "ESP_barcelona", "canonical_name": "Barcelona", "country": "ESP"},
    }  # fmt: skip
    d.aliases = [
        Alias("football-data", "Man City", "ENG_manchester_city", provenance="manual"),
        Alias("football-data", "Man United", "ENG_manchester_united", valid_from=date(2000, 1, 1)),
        Alias("other-source", "MCFC", "ENG_manchester_city"),
    ]
    return d


def test_normalization():
    assert normalize_name("Nott'm Forest") == "nottm forest"
    assert normalize_name("  Atlético  Madrid ") == "atletico madrid"


def test_exact_alias_canonical_name_and_source_scoping():
    d = directory()
    r = d.resolve("football-data", "Man City", "ENG")
    assert r.team_id == "ENG_manchester_city" and r.provenance == "manual"
    assert d.resolve("football-data", "Manchester City", "ENG").provenance == "canonical"
    assert d.resolve("football-data", "MCFC", "ENG").team_id is None  # alias belongs to another source
    assert d.resolve("other-source", "MCFC", "ENG").team_id == "ENG_manchester_city"
    assert d.resolve("football-data", "Man City", "ESP").team_id is None  # country scoped
    assert d.usage["football-data|Man City|ENG_manchester_city"] == 1


def test_alias_validity_window():
    d = directory()
    assert d.resolve("football-data", "Man United", "ENG", date(2010, 5, 1)).team_id
    assert d.resolve("football-data", "Man United", "ENG", date(1999, 5, 1)).team_id is None
    d.aliases[1] = Alias("football-data", "Man United", "ENG_manchester_united", valid_to=date(2010, 1, 1))
    assert d.resolve("football-data", "Man United", "ENG", date(2020, 1, 1)).team_id is None


def test_unknown_name_goes_to_queue_with_suggestions_never_auto_mapped():
    d = directory()
    r = d.resolve("football-data", "Man Cityy", "ENG", date(2024, 1, 1))
    assert r.team_id is None and r.provenance == "unresolved"
    assert r.suggestions[0]["team_id"] == "ENG_manchester_city" and r.suggestions[0]["similarity"] > 0.8
    entry = d.review_queue["football-data|ENG|man cityy"]
    assert entry["raw_name"] == "Man Cityy" and entry["first_seen"] == "2024-01-01"
    assert d.resolve("football-data", "", "ENG").team_id is None
    assert "ENG_man_cityy" not in d.teams  # nothing was registered


def test_auto_register_is_opt_in_and_marked():
    d = directory()
    r = d.resolve("football-data", "Totally New FC", "ENG", auto_register=True)
    assert r.provenance == "auto_registered" and r.team_id == "ENG_totally_new_fc"
    assert d.aliases[-1].confidence == 0.0
    d2 = directory()
    assert (
        d2.resolve("football-data", "Man Cityy", "ENG", auto_register=True).team_id is None
    )  # close -> review


def test_validate_detects_integrity_problems():
    d = directory()
    assert d.validate() == []
    d.aliases.append(Alias("football-data", "Man City", "ENG_manchester_united"))  # conflict
    d.aliases.append(Alias("football-data", "Ghost", "ENG_nobody"))
    d.aliases.append(
        Alias("football-data", "Backwards", "ENG_manchester_city", date(2020, 1, 1), date(2019, 1, 1))
    )
    d.teams["XXX_wrong"] = {"team_id": "XXX_wrong", "canonical_name": "W", "country": "ENG"}
    problems = " | ".join(d.validate())
    assert "conflicting aliases" in problems and "unknown team_id ENG_nobody" in problems
    assert "valid_from after valid_to" in problems and "does not start with its country" in problems


def test_non_overlapping_windows_are_not_conflicts():
    d = directory()
    d.aliases = [
        Alias("s", "X", "ENG_manchester_city", valid_to=date(2010, 1, 1)),
        Alias("s", "X", "ENG_manchester_united", valid_from=date(2010, 1, 2)),
    ]
    assert d.validate() == []


def test_dump_load_roundtrip(tmp_path):
    d = directory()
    path = tmp_path / "aliases.yaml"
    d.dump(path)
    d2 = TeamDirectory.load(path)
    assert d2.teams == d.teams and {a.raw_name for a in d2.aliases} == {a.raw_name for a in d.aliases}
    assert d2.aliases[0].valid_from is None and any(a.valid_from == date(2000, 1, 1) for a in d2.aliases)


# ------------------------------------------------------------------------------- CLI
def test_cli_review_suggest_approve_workflow(project, capsys):
    edit_raw(project, "TST_2122.csv", lambda t: t.replace("Gamma Town", "Gamma Twn", 1))
    with pytest.raises(PipelineError):
        run(project, RunMode.RESEARCH)  # gate fails, queue is recorded in the failed report
    root = ["--root", str(project)]
    assert cli.main([*root, "review"]) == 1
    out = capsys.readouterr().out
    assert "UNRESOLVED 'Gamma Twn'" in out
    assert cli.main([*root, "suggest"]) == 1 and "TST_gamma_town" in capsys.readouterr().out
    # approving into an unknown team is refused; registering first makes it possible
    assert cli.main([*root, "approve", "--raw", "Gamma Twn", "--team-id", "TST_nope"]) == 2
    assert cli.main([*root, "approve", "--raw", "Gamma Twn", "--team-id", "TST_gamma_town",
                     "--approved-by", "tester"]) == 0  # fmt: skip
    assert cli.main([*root, "validate"]) == 0
    assert run(project, RunMode.RESEARCH).data_version  # the pipeline now resolves the name
    text = (project / "configs/team_aliases.yaml").read_text()
    assert "approved_by: tester" in text and "Gamma Twn" in text


def test_cli_register_team_and_validate_failures(project, capsys):
    root = ["--root", str(project)]
    assert (
        cli.main([*root, "register-team", "--team-id", "TST_new_fc", "--name", "New FC", "--country", "TST"])
        == 0
    )
    assert (
        cli.main([*root, "register-team", "--team-id", "TST_new_fc", "--name", "x", "--country", "TST"]) == 2
    )
    assert cli.main([*root, "register-team", "--team-id", "BAD_id", "--name", "x", "--country", "TST"]) == 2
    assert (
        cli.main([*root, "approve", "--raw", "Alpha FC", "--team-id", "TST_new_fc"]) == 2
    )  # conflicts with existing alias
    assert cli.main([*root, "validate"]) == 0
    assert cli.main([*root, "review"]) == 0 and "empty" in capsys.readouterr().out
