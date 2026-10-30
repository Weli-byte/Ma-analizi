"""The report commands of Phase 2 / Phase 3 run end to end on the REAL excerpts kept for the tests (`real_oddsens_excerpt.json`, `real_calibsel_oof_excerpt.jsonl`,
`real_highlightly_xg_rows.jsonl`, `real_espn_lineups/`) and write their documents. These are offline runs on stored data, not live checks."""

import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"


@pytest.fixture
def markets_tree(tmp_path):
    """Markets-data layout rebuilt from the forecast excerpt (artifacts, results-free track record) plus its odds store."""
    data = json.loads((FIX / "real_oddsens_excerpt.json").read_text(encoding="utf-8"))
    mk, od = tmp_path / "markets", tmp_path / "odds"
    rows = []
    for key, m in data["matches"].items():
        rows.append({"key": key, **m["result_row"]})
        d = mk / "artifacts" / "markets" / key
        d.mkdir(parents=True)
        for f in m["intel"]:
            (d / f["file"]).write_text(json.dumps({**f["body"], "fixture_key": key}), encoding="utf-8")
        for o in m["odds"]:
            q = od / "artifacts" / "odds" / o["fixture"]
            q.mkdir(parents=True, exist_ok=True)
            (q / "meta.json").write_text(json.dumps(o["meta"]), encoding="utf-8")
            (q / "quotes.jsonl").write_text("\n".join(json.dumps(x) for x in o["quotes"]) + "\n", encoding="utf-8")
    track = {"generated_at": data["track_generated_at"], "matches": rows, "matches_scored": len(rows),
             "headline_1x2": {"log_loss": 0.9, "log_loss_model_only": 0.9, "log_loss_league_base_rate": 1.0, "brier": 0.5, "rps": 0.2}, "over_under_2_5_log_loss": 0.65, "btts_log_loss": 0.69}  # fmt: skip
    (mk / "artifacts" / "markets" / "track_record.json").write_text(json.dumps(track), encoding="utf-8")
    return mk, od


def test_prospective_report_cli(markets_tree, tmp_path):
    from src.prospective import report

    out = tmp_path / "rec.md"
    assert report.main(["--markets-root", str(markets_tree[0]), "--as-of", "2026-10-29T14:00:00Z", "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "| Lig | Maç |" in text and "Forecast at each stage" in text and "0 mismatches" in text


def test_oddsens_report_cli(markets_tree, tmp_path):
    from src.oddsens import report

    out = tmp_path / "o.md"
    assert report.main(["--markets-root", str(markets_tree[0]), "--odds-root", str(markets_tree[1]), "--out", str(out)]) == 0
    assert "Only 5 rows" in out.read_text(encoding="utf-8") or "Chronological split" in out.read_text(encoding="utf-8")


def test_goalmkts_report_cli(markets_tree, tmp_path):
    from src.goalmkts import report

    out = tmp_path / "g.md"
    assert report.main(["--markets-root", str(markets_tree[0]), "--out", str(out)]) == 0
    assert "Label contract" in out.read_text(encoding="utf-8")


def test_xg_baseline_cli(tmp_path):
    from src.xg import baseline

    m = json.loads((ROOT / "docs" / "phase3" / "s13_baseline_manifest.json").read_text(encoding="utf-8"))
    from src.lineage.baseline import FIELDS

    rows = []
    for k, o in zip(m["fixtures"], m["outcomes"], strict=True):
        r = dict(zip(FIELDS, o, strict=True))
        h, a = (int(x) for x in r["score"].split("-"))
        rows.append({"key": k, "date": k.rsplit("__", 1)[1], "league": "X", "over_actual": h + a > 2, **r})
    root = tmp_path / "root"
    (root / "artifacts" / "markets").mkdir(parents=True)
    (root / "artifacts" / "markets" / "track_record.json").write_text(json.dumps({"generated_at": "t", "matches": rows}), encoding="utf-8")
    out = tmp_path / "b.md"
    assert baseline.main(["--root", str(root), "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "| 1X2 | 94/138" in text and "always over | 95/138" in text


def test_xg_report_cli(tmp_path):
    from src.xg import report

    d = tmp_path / "artifacts" / "stats" / "highlightly"
    d.mkdir(parents=True)
    shutil.copy(FIX / "real_highlightly_xg_rows.jsonl", d / "EPL_2026.jsonl")
    out, obs = tmp_path / "x.md", tmp_path / "obs.jsonl"
    assert report.main(["--root", str(tmp_path), "--as-of", "2026-10-27T12:00:00Z", "--out", str(out), "--observations-out", str(obs)]) == 0
    assert "Leakage self-check" in out.read_text(encoding="utf-8") and obs.read_text(encoding="utf-8").count("\n") > 50


def test_availability_report_cli(tmp_path):
    from src.availability import report

    shutil.copytree(FIX / "real_espn_lineups", tmp_path / "artifacts" / "lineups")
    out, snaps = tmp_path / "a.md", tmp_path / "snaps"
    assert report.main(["--root", str(tmp_path), "--as-of", "2026-10-29T00:00:00Z", "--out", str(out), "--write-snapshots", str(snaps)]) == 0
    assert "Leakage and idempotency" in out.read_text(encoding="utf-8") and list((snaps / "artifacts" / "lineup_snapshots").glob("*/t-30m.json"))
    ledger = tmp_path / "artifacts" / "lineups" / "_checks"
    ledger.mkdir()
    (ledger / "2026-10-28.jsonl").write_text(json.dumps({"espn_event_id": "1", "league": "SERIEA", "outcome": "not_published"}) + "\n" + json.dumps({"espn_event_id": "2", "league": "SERIEA", "outcome": "stored"}) + "\n", encoding="utf-8")
    assert report.main(["--root", str(tmp_path), "--as-of", "2026-10-29T00:00:00Z", "--out", str(out)]) == 0
    assert "2 fixtures looked at, 1 with a stored XI, 1 without a published XI" in out.read_text(encoding="utf-8")


def test_calibsel_report_cli(tmp_path, markets_tree):
    from src.calibsel import report

    oof = tmp_path / "oof.jsonl"
    shutil.copy(FIX / "real_calibsel_oof_excerpt.jsonl", oof)
    (tmp_path / "oof.jsonl.meta.json").write_text(json.dumps({"data_version": "dv-test", "fits": 1, "refit_block_days": 30}), encoding="utf-8")
    assert report.main(["--oof", str(oof), "--markets-root", str(markets_tree[0]), "--out-dir", str(tmp_path / "docs")]) == 0
    text = (tmp_path / "docs" / "sprint11_calibration_and_selection.md").read_text(encoding="utf-8")
    manifest = json.loads((tmp_path / "docs" / "sprint11_evaluation_manifest.json").read_text(encoding="utf-8"))
    assert "Split (by season" in text and "Prospective reliability" in text and manifest["test_rows_read_once"] and set(manifest["registry"]) == {"1x2", "over_2_5", "btts"}
