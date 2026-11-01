"""Phase 3 sprint 16: uncertainty and selective prediction (ADR 0095). Thresholds and curves run on the REAL out-of-fold excerpt (`real_calibsel_oof_excerpt.jsonl`) and the REAL forecast
excerpt (`real_oddsens_excerpt.json`); the abstaining forecast is a TEST_ONLY edit of a real one."""

import copy
import json
import math
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.calibsel.oof import read
from src.uncertainty import measures, selective, serving, study
from src.web import i18n, pages

ROOT = Path(__file__).resolve().parents[1]
EXCERPT = ROOT / "tests" / "fixtures" / "real_oddsens_excerpt.json"
SEL = json.loads((ROOT / "docs" / "phase3" / "s16_selective.json").read_text(encoding="utf-8"))


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
    (tmp_path / "artifacts" / "markets" / "track_record.json").write_text(json.dumps({"generated_at": "t", "matches": rows}), encoding="utf-8")
    (tmp_path / "docs" / "phase3").mkdir(parents=True)
    (tmp_path / "docs" / "phase3" / "s16_selective.json").write_text(json.dumps(SEL), encoding="utf-8")
    return tmp_path


def real_intel(tree) -> dict:
    f = sorted(next((tree / "artifacts" / "markets").glob("*__*__*")).glob("intel-*.json"))[-1]
    return json.loads(f.read_text(encoding="utf-8"))["intelligence"]


def test_the_measures_have_their_textbook_values():
    p = [0.5, 0.3, 0.2]
    assert measures.top(p) == 0.5 and measures.margin(p) == pytest.approx(0.2) and measures.entropy([1 / 3] * 3) == pytest.approx(math.log(3))
    assert measures.disagreement(p, p) == 0 and measures.disagreement([1, 0, 0], [0, 1, 0]) == 1 and measures.disagreement(p, None) is None


def test_thresholds_come_from_history_and_the_curve_keeps_every_match_in_the_coverage():
    rows = read(ROOT / "tests" / "fixtures" / "real_calibsel_oof_excerpt.jsonl")
    hist = [max(r["p"]) for r in rows if r["season"] in selective.HISTORY_SEASONS]
    cuts = selective.thresholds(hist)
    assert cuts["100"] == 0 and cuts["80"] < cuts["60"] < cuts["40"]
    share = sum(1 for x in hist if x >= cuts["40"]) / len(hist)
    assert share == pytest.approx(0.4, abs=0.02)
    test = [r for r in rows if r["season"] == selective.TEST_SEASON]
    curve = selective.curve([max(r["p"]) for r in test], [r["p"] for r in test], [r["y"] for r in test], cuts)
    assert curve[0]["answered"] == len(test) and curve[0]["coverage"] == 1.0 and all(c["answered"] <= len(test) for c in curve)
    assert [c["answered"] for c in curve] == sorted((c["answered"] for c in curve), reverse=True)
    assert selective.bucket(0.9, cuts) == "40" and selective.bucket(0.0, cuts) == "abstain"


def test_the_study_runs_on_real_excerpts_and_marks_thin_buckets_insufficient(tree):
    rows = read(ROOT / "tests" / "fixtures" / "real_calibsel_oof_excerpt.jsonl")
    res = study.study(rows, study.live_rows(tree))
    assert res["live_rows"] == 5 and all(t["evidence"] == "INSUFFICIENT_EVIDENCE" for t in res["live_tiers"].values())   # five matches never support a confidence claim
    text = study.report(res, "test")
    assert "Untouched season 2023-24" in text and "An abstained match stays in the coverage denominator" in text and "top3" in text


def test_the_served_block_reports_bucket_reasons_and_evidence(tree):
    i = real_intel(tree)
    b = serving.block(i, SEL)
    assert b["coverage_bucket"] in ("40", "60", "80", "abstain") and b["calibration"].startswith("identity") and "not betting advice" in b["note"]
    assert b["bucket_evidence"]["evidence"] in ("MEASURED", "INSUFFICIENT_EVIDENCE")
    weak = copy.deepcopy(i)
    weak["result"]["model_probs"] = {"home": 0.36, "draw": 0.33, "away": 0.31}                       # TEST_ONLY: a near coin-flip
    weak["result"]["market_probs_devig"] = None
    w = serving.block(weak, SEL)
    assert w["abstain"] and w["abstain_reason"].startswith("TOP_PROBABILITY") and "NO_EXACT_ODDS (model only)" in w["uncertainty_reason"]
    assert serving.block(i, None) is None


def test_api_and_page_carry_the_uncertainty(tree):
    c = TestClient(create_app(tree, keys=["k"]), raise_server_exceptions=False)
    key = next((tree / "artifacts" / "markets").glob("*__*__*")).name
    u = c.get(f"/v1/fixtures/{key}/intelligence", headers={"X-API-Key": "k"}).json()["data"]["uncertainty"]
    assert u["coverage_bucket"] and "calibrated_probability" in u and "abstain_reason" in u and "uncertainty_reason" in u
    card = pages.uncertainty_card(u)
    assert "Model güveni" in card and "kazanma garantisi ve bahis tavsiyesi değildir" in card and "Model confidence" in i18n.translate_html(card)
    thin = pages.uncertainty_card({**u, "bucket_evidence": {"evidence": "INSUFFICIENT_EVIDENCE", "n": 3}})
    assert "yeterli canlı kanıt yok" in thin and pages.uncertainty_card(None) == ""


def test_an_abstaining_forecast_hides_value_suggestions_and_the_bundle_ships_the_thresholds():
    src = (ROOT / "src" / "web" / "routes.py").read_text(encoding="utf-8")
    assert 'if (found.get("uncertainty") or {}).get("abstain"):\n            picks = []' in src
    bundle = (ROOT / "scripts" / "make_vercel_bundle.py").read_text(encoding="utf-8")
    assert 's16_selective.json' in bundle
