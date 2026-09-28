import csv
import io
from datetime import date

import pytest
from test_data_provenance import rewrite_raw
from test_pipeline import edit_raw, run

from src.data.pipeline import PipelineError
from src.data.quality import summarize
from src.runmode import RunMode

AS_OF = date(2026, 9, 25)


def set_field(text, line, column, value):
    rows = list(csv.reader(io.StringIO(text)))
    rows[line][rows[0].index(column)] = value
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(rows)
    return out.getvalue()


def failing(report):
    return {c["id"]: c["severity"] for c in report["checks"] if c["status"] == "FAIL"}


def dev_report(project):
    return run(project, RunMode.DEVELOPMENT).report


def test_clean_fixture_passes_every_check(project):
    rep = dev_report(project)
    assert failing(rep) == {} and rep["summary"]["n_checks"] == 21
    assert {c["id"] for c in rep["checks"]} >= {f"Q{i:02d}" for i in range(1, 21)}


@pytest.mark.parametrize(
    "file,edit,check,severity",
    [
        ("TST_2223.csv", lambda t: set_field(t, 2, "FTHG", "-1"), "Q06", "error"),
        ("TST_2223.csv", lambda t: set_field(t, 2, "FTHG", "abc"), "Q06", "error"),
        ("TST_2223.csv", lambda t: set_field(t, 2, "FTHG", ""), "Q06", "error"),  # played fixture w/o score
        (
            "TST_2223.csv",
            lambda t: set_field(t, 2, "FTR", "A" if ",H," in t.splitlines()[2] else "H"),
            "Q07",
            "error",
        ),
        ("TST_2223.csv", lambda t: set_field(t, 2, "Date", "32/01/2022"), "Q05", "error"),
        ("TST_2223.csv", lambda t: set_field(t, 2, "Time", "25:99"), "Q05", "error"),
        ("TST_2223.csv", lambda t: set_field(t, 2, "B365H", "0.9"), "Q08", "warning"),
        ("TST_2223.csv", lambda t: set_field(t, 2, "AvgH", "x"), "Q08", "warning"),  # incomplete odds
        ("TST_2223.csv", lambda t: set_field(t, 2, "Date", "15/05/2021"), "Q14", "error"),  # before window
        ("TST_2223.csv", lambda t: set_field(t, 2, "Date", "01/01/2031"), "Q15", "error"),  # after as_of
        (
            "TST_2223.csv",
            lambda t: set_field(set_field(set_field(t, 2, "FTHG", "12"), 2, "FTAG", "0"), 2, "FTR", "H"),
            "Q16",
            "warning",
        ),
        (
            "TST_2223.csv",
            lambda t: set_field(
                set_field(set_field(t, 2, "AvgH", "1.05"), 2, "AvgD", "1.05"), 2, "AvgA", "1.05"
            ),
            "Q17",
            "warning",
        ),
        ("TST_2223.csv", lambda t: set_field(t, 2, "B365H", "150"), "Q17", "warning"),
        (
            "TST_2223.csv",
            lambda t: t + t.splitlines()[3] + "\n",
            "Q03",
            "error",
        ),  # duplicate fixture (+raw row Q04)
        ("TST_2223.csv", lambda t: t + t.splitlines()[3] + "\n", "Q04", "warning"),
        ("TST_2223.csv", lambda t: "\n".join(t.splitlines()[:-1]) + "\n", "Q12", "error"),
        ("TST_2223.csv", lambda t: "\n".join(t.splitlines()[:-1]) + "\n", "Q13", "error"),
        ("TST_2223.csv", lambda t: set_field(t, 2, "HomeTeam", "Gamma Twn"), "Q10", "error"),
        ("TST_2223.csv", lambda t: "\n".join(t.splitlines()[:2]) + "\n", "Q18", "error"),  # tiny file
    ],
)
def test_each_check_detects_its_defect(project, file, edit, check, severity):
    edit_raw(project, file, edit)
    rep = dev_report(project)  # development never blocks, so the report can be inspected
    assert failing(rep).get(check) == severity, failing(rep)


def test_dropping_a_team_breaks_team_count_check(project):
    def drop_delta(t):
        head, *rows = t.splitlines()
        return "\n".join([head] + [r for r in rows if "Delta City" not in r]) + "\n"

    edit_raw(project, "TST_2223.csv", drop_delta)
    assert failing(dev_report(project)).get("Q11") == "error"


def test_missing_optional_columns_and_encoding_are_warnings(project):
    def drop_hs(t):
        return (
            "\n".join(
                ",".join(c for i, c in enumerate(r.split(",")) if i not in (8, 9)) for r in t.splitlines()
            )
            + "\n"
        )

    edit_raw(project, "TST_2223.csv", drop_hs)
    f = failing(dev_report(project))
    assert f.get("Q20") == "warning"


def test_null_rate_check_flags_a_dead_column(project):
    def blank_hs(t):
        rows = list(csv.reader(io.StringIO(t)))
        for r in rows[1:]:
            r[rows[0].index("HS")] = ""
            r[rows[0].index("AS")] = ""
        out = io.StringIO()
        csv.writer(out, lineterminator="\n").writerows(rows)
        return out.getvalue()

    edit_raw(project, "TST_2223.csv", blank_hs)  # one of three seasons -> 33% of team-matches lack shots
    assert failing(dev_report(project)).get("Q09") == "warning"


def test_non_utf8_file_is_a_warning(project):
    path = project / "data/raw/football_data/TST_2223.csv"
    rows = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8"))))
    rows[0].append("Referee")
    for r in rows[1:]:
        r.append("José")
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(rows)
    rewrite_raw(project, "TST_2223.csv", out.getvalue().encode("latin-1"))
    assert failing(dev_report(project)).get("Q02") == "warning"


# ---------------------------------------------------------------------- gating by mode
def test_error_blocks_research_warning_blocks_only_strict(project):
    raw = project / "data/raw/football_data/TST_2223.csv"
    original = raw.read_text(encoding="utf-8")
    edit_raw(project, "TST_2223.csv", lambda t: set_field(t, 2, "FTHG", "-1"))
    with pytest.raises(PipelineError, match="Q06"):
        run(project, RunMode.RESEARCH)
    rewrite_raw(project, "TST_2223.csv", original)  # restore the genuine file
    edit_raw(project, "TST_2223.csv", lambda t: set_field(t, 3, "B365H", "0.9"))  # warning-level defect
    assert run(project, RunMode.RESEARCH).report["summary"]["warnings"] == ["Q08"]
    with pytest.raises(PipelineError, match="Q08"):
        run(project, RunMode.STRICT)


def test_acknowledged_anomalies_stay_visible_but_do_not_block_strict(project):
    edit_raw(project, "TST_2223.csv", lambda t: set_field(t, 2, "AvgH", "150"))
    rep = dev_report(project)
    item = next(c["observed"][0] for c in rep["checks"] if c["id"] == "Q17")
    fixture_id = item.split(" ")[0]
    with pytest.raises(PipelineError, match="Q17"):
        run(project, RunMode.STRICT)
    cfg = project / "configs/data.yaml"
    cfg.write_text(
        cfg.read_text()
        + f"acknowledged_anomalies:\n  - check: Q17\n    match: {fixture_id}\n    reason: reviewed test anomaly\n"
    )
    res = run(project, RunMode.STRICT)
    q17 = next(c for c in res.report["checks"] if c["id"] == "Q17")
    assert q17["status"] == "pass" and fixture_id in q17["detail"]  # visible in the report, not blocking


def test_summary_helper_lists_failed_ids(project):
    from src.data.quality import CheckResult

    checks = [CheckResult("Q1", "a", "error", False, 1), CheckResult("Q2", "b", "warning", False, 1),
              CheckResult("Q3", "c", "error", True, 0)]  # fmt: skip
    assert summarize(checks) == {"errors": ["Q1"], "warnings": ["Q2"], "n_checks": 3}
