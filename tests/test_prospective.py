"""Prospective record audit (ADR 0090). The forecasts are the REAL artifacts of the shared excerpt (`real_oddsens_excerpt.json`); the excerpt dropped the `fixture_key` field, whose value is the
folder name, so the tree below restores it and the stored content hashes must then match exactly. Tampering and timing cases are TEST_ONLY edits of those real files."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.prospective import audit, report

ROOT = Path(__file__).resolve().parents[1]
EXCERPT = ROOT / "tests" / "fixtures" / "real_oddsens_excerpt.json"
AS_OF = datetime(2026, 10, 29, 14, tzinfo=UTC)


@pytest.fixture
def tree(tmp_path):
    data = json.loads(EXCERPT.read_text(encoding="utf-8"))
    rows = []
    for key, m in data["matches"].items():
        rows.append({"key": key, **m["result_row"]})
        d = tmp_path / "artifacts" / "markets" / key
        d.mkdir(parents=True)
        for f in m["intel"]:
            (d / f["file"]).write_text(json.dumps({**f["body"], "fixture_key": key}), encoding="utf-8")
    track = {"generated_at": data["track_generated_at"], "matches": rows, "matches_scored": len(rows),
             "headline_1x2": {"log_loss": 0.9, "log_loss_model_only": 0.9, "log_loss_league_base_rate": 1.0, "brier": 0.5, "rps": 0.2}, "over_under_2_5_log_loss": 0.65, "btts_log_loss": 0.69}  # fmt: skip
    (tmp_path / "artifacts" / "markets" / "track_record.json").write_text(json.dumps(track), encoding="utf-8")
    return tmp_path


def test_the_real_artifacts_are_unchanged_published_before_kickoff_and_settled(tree):
    a = audit.audit(tree, AS_OF)
    assert a["artifacts"] == 32 and a["hash_mismatch"] == [] and a["cutoff_differs_from_generated"] == 0
    assert a["post_kickoff"] == 0 and a["pre_kickoff"] == 32 and a["settled"] == 5 and a["unsettled"] == []


def test_a_changed_probability_breaks_the_hash_and_a_missing_result_is_listed_not_filled(tree):
    f = next((tree / "artifacts" / "markets").glob("*/intel-*.json"))
    body = json.loads(f.read_text(encoding="utf-8"))
    body["intelligence"]["result"]["headline_probs"]["home"] += 0.01
    f.write_text(json.dumps(body), encoding="utf-8")
    trk = tree / "artifacts" / "markets" / "track_record.json"
    t = json.loads(trk.read_text(encoding="utf-8"))
    dropped = t["matches"].pop()["key"]
    trk.write_text(json.dumps(t), encoding="utf-8")
    a = audit.audit(tree, AS_OF)
    assert len(a["hash_mismatch"]) == 1 and f.parent.name in a["hash_mismatch"][0]
    assert [u["fixture"] for u in a["unsettled"]] == [dropped] and a["settled"] == 4


def test_an_artifact_generated_after_kickoff_is_counted_and_never_a_forecast(tree):
    d = next((tree / "artifacts" / "markets").glob("*"))
    f = sorted(d.glob("intel-*.json"))[-1]
    body = json.loads(f.read_text(encoding="utf-8"))
    body["information_cutoff"] = body["generated_at"] = "2026-12-01T00:00:00+00:00"
    body["content_hash"] = audit.recompute_hash(body)
    (d / "intel-20261201T000000Z.json").write_text(json.dumps(body), encoding="utf-8")
    a = audit.audit(tree, AS_OF)
    assert a["post_kickoff"] == 1 and a["hash_mismatch"] == []


def test_the_report_tables_count_hits_per_league_and_show_the_trivial_strategies(tree):
    track = json.loads((tree / "artifacts" / "markets" / "track_record.json").read_text(encoding="utf-8"))
    text = report.build(track, audit.audit(tree, AS_OF), AS_OF, {})
    assert "| Lig | Maç | 1X2 | 2.5 Alt/Üst | KG | Skor |" in text and "**Toplam**" in text and "hep ev sahibi" in text and "0 mismatches" in text
    n = sum(1 for m in track["matches"] if m["pick_hit"])
    assert f"{n}/5 (%{100 * n / 5:.1f})" in text
    assert report.cell(0, 0) == "-" and report.cell(3, 4) == "3/4 (%75.0)"


def test_a_forecast_under_a_provisional_date_is_joined_to_the_same_pair_played_within_three_days():
    """ADR 0090: e.g. a forecast stored as 2026-10-24 (TBD kick-off) for a match played on 2026-10-26. TEST_ONLY results."""
    from src.markets.data import StatMatch
    from src.markets.track import result_index

    def m(home, away, day):
        k = datetime.fromisoformat(day).replace(tzinfo=UTC)
        return StatMatch(f"t-{home}-{day}", "2026-27", "LALIGA", k, k, home, away, 2, 1, {}, {}, None)

    find = result_index([m("ESP_barcelona", "ESP_real_madrid", "2026-10-26T15:15:00"), m("ESP_real_madrid", "ESP_barcelona", "2026-10-24T15:15:00"), m("ESP_a", "ESP_b", "2026-10-10T12:00:00")])
    assert find("ESP_barcelona", "ESP_real_madrid", "2026-10-24").kickoff_utc.day == 26          # same ordered pair, two days later
    assert find("ESP_real_madrid", "ESP_barcelona", "2026-10-24").kickoff_utc.day == 24          # exact date wins, the reversed fixture is another match
    assert find("ESP_a", "ESP_b", "2026-10-14") is None and find("ESP_a", "ESP_b", "2026-10-13").kickoff_utc.day == 10   # beyond three days: no join


def test_an_abandoned_match_is_reported_with_its_reason_and_the_settlement_lag_is_measured(tree):
    trk = tree / "artifacts" / "markets" / "track_record.json"
    t = json.loads(trk.read_text(encoding="utf-8"))
    dropped = t["matches"].pop()
    trk.write_text(json.dumps(t), encoding="utf-8")
    res_dir = tree / "artifacts" / "results"
    res_dir.mkdir(parents=True)
    kept = t["matches"][0]
    k_kept = kept["key"].replace("__", "|")
    (res_dir / "X.json").write_text(json.dumps({"matches": {k_kept: {"kickoff_utc": "2026-10-09T18:30:00+00:00", "first_seen": "2026-10-10T00:30:00+00:00"}},
                                                "void": {dropped["key"].replace("__", "|"): {"status": "STATUS_ABANDONED", "source": "espn scoreboard (unofficial, research only)"}}}), encoding="utf-8")  # fmt: skip
    a = audit.audit(tree, AS_OF)
    assert a["unsettled"][0]["reason"] == "ABANDONED (espn)" and a["settlement_lag_hours"]["n"] == 1 and a["settlement_lag_hours"]["median"] == pytest.approx(6.0)


def test_stage_rows_score_the_forecast_as_it_stood_at_each_cutoff(tree):
    rows, skipped = report.stage_rows(tree)
    stages = {r["stage"] for r in rows}
    assert stages <= {"t-24h", "t-90m", "t-30m"} and rows and skipped >= 0
    by = {s: sum(1 for r in rows if r["stage"] == s) for s in stages}
    assert by.get("t-30m", 0) >= by.get("t-24h", 0)                                       # later cutoffs see at least as many published forecasts
    text = "\n".join(report.stage_table(rows, skipped))
    assert "| aşama | maç |" in text and "provisional date" in text
