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
