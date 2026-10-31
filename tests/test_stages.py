"""Phase 3 sprint 15: stage records (ADR 0093). The forecasts are the REAL artifacts of the shared excerpt (five 2026-10-09/10 fixtures). The edge cases — a moved kick-off, a late line-up,
odds observed after the cutoff, a broken artifact, a second writer — are TEST_ONLY edits of copies of those files."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.stages import evaluate, freeze, view
from src.web import i18n, pages

ROOT = Path(__file__).resolve().parents[1]
EXCERPT = ROOT / "tests" / "fixtures" / "real_oddsens_excerpt.json"
NOW = datetime(2026, 10, 20, tzinfo=UTC)


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
    return tmp_path


def fixtures(tree) -> list[Path]:
    return sorted((tree / "artifacts" / "markets").glob("*__*__*"))


def test_each_stage_holds_the_forecast_that_existed_at_its_cutoff_and_a_rerun_changes_nothing(tree):
    rep = freeze.freeze(tree, NOW)
    assert rep["WRITTEN"] == 20 and rep["FAILED"] == 0 and rep["CONFLICT"] == 0
    for d in fixtures(tree):
        for r in view.stages_for(tree, d.name):
            if r["status"] == "OK":
                assert r["source_information_cutoff"] <= r["cutoff_utc"] and r["staleness_minutes"] >= 0 and r["calibration"].startswith("CALIBRATION_FALLBACK")
                assert r["odds"] is None or r["odds"]["observed_at"] <= r["cutoff_utc"]
            assert r["idempotency_key"].endswith(r["kickoff_utc"]) and r["frozen_late_minutes"] > 0
        assert [r["stage"] for r in view.stages_for(tree, d.name)] == ["t-24h", "t-90m", "t-30m", "kickoff"]
    again = freeze.freeze(tree, NOW + timedelta(days=1))
    assert again["WRITTEN"] == 0 and again["UNCHANGED"] == 20                                          # a later run picks the same forecasts: selection depends on the cutoff only


def test_a_stage_before_its_cutoff_is_not_written_and_one_without_a_forecast_says_so(tree):
    d = fixtures(tree)[0]
    kick = datetime.fromisoformat(json.loads(sorted(d.glob("intel-*.json"))[-1].read_text(encoding="utf-8"))["kickoff_utc"])
    rep = freeze.freeze(tree, kick - timedelta(hours=2))
    assert rep["NOT_DUE"] > 0 and all(r["stage"] == "t-24h" for r in view.stages_for(tree, d.name))
    early = freeze.build_record(tree, d.name, [(f.name, json.loads(f.read_text(encoding="utf-8"))) for f in sorted(d.glob("intel-*.json"))], "early", 60 * 24 * 30, NOW)
    assert early["status"] == "NO_FORECAST" and "probabilities" not in early


def test_a_conflicting_record_is_logged_and_never_overwritten(tree):
    freeze.freeze(tree, NOW)
    f = next((tree / "artifacts" / "stage_records").glob("*/t-30m__*.json"))
    body = json.loads(f.read_text(encoding="utf-8"))
    body["record_sha256"] = "tampered"
    f.write_text(json.dumps(body), encoding="utf-8")
    rep = freeze.freeze(tree, NOW)
    assert rep["CONFLICT"] == 1 and json.loads(f.read_text(encoding="utf-8"))["record_sha256"] == "tampered"
    assert (tree / "artifacts" / "stage_records" / "_conflicts.jsonl").read_text(encoding="utf-8").count("\n") == 1


def test_odds_observed_after_the_cutoff_never_enter_an_earlier_stage(tree):
    d = fixtures(tree)[0]
    f = sorted(d.glob("intel-*.json"))[-1]
    body = json.loads(f.read_text(encoding="utf-8"))
    kick = datetime.fromisoformat(body["kickoff_utc"])
    body["market_source"] = {"bookmaker": "x", "observed_at": (kick - timedelta(minutes=5)).isoformat(), "quality": "exact", "odds": [2, 3, 4]}   # TEST_ONLY: a quote from after t-30m
    body["information_cutoff"] = body["generated_at"] = (kick - timedelta(minutes=40)).isoformat()
    (d / "intel-zz.json").write_text(json.dumps(body), encoding="utf-8")
    arts = [(x.name, json.loads(x.read_text(encoding="utf-8"))) for x in sorted(d.glob("intel-*.json"))]
    r = freeze.build_record(tree, d.name, arts, "t-30m", 30, NOW)
    assert r["status"] == "ODDS_AFTER_CUTOFF"


def test_a_moved_kickoff_gets_new_records_and_the_old_schedule_stays(tree):
    freeze.freeze(tree, NOW)
    d = fixtures(tree)[0]
    old = sorted((tree / "artifacts" / "stage_records" / d.name).glob("*.json"))
    f = sorted(d.glob("intel-*.json"))[-1]
    body = json.loads(f.read_text(encoding="utf-8"))
    new_kick = datetime.fromisoformat(body["kickoff_utc"]) + timedelta(days=1)                       # TEST_ONLY: postponed by a day
    body["kickoff_utc"] = new_kick.isoformat()
    (d / "intel-zzz.json").write_text(json.dumps(body), encoding="utf-8")
    rep = freeze.freeze(tree, NOW)
    files = sorted((tree / "artifacts" / "stage_records" / d.name).glob("*.json"))
    assert rep["kickoff_moved"] == 1 and len(files) == len(old) + 4 and all(o in files for o in old)
    assert {r["kickoff_utc"] for r in view.stages_for(tree, d.name)} == {new_kick.isoformat()}


def test_a_late_lineup_is_unconfirmed_at_t30m_and_confirmed_at_kickoff(tree):
    d = fixtures(tree)[0]
    kick = datetime.fromisoformat(json.loads(sorted(d.glob("intel-*.json"))[-1].read_text(encoding="utf-8"))["kickoff_utc"])
    ld = tree / "artifacts" / "lineups" / d.name
    ld.mkdir(parents=True)
    (ld / "lineup-x.json").write_text(json.dumps({"observed_at": (kick - timedelta(minutes=20)).isoformat()}), encoding="utf-8")   # TEST_ONLY: XI seen 20 min before
    freeze.freeze(tree, NOW)
    by = {r["stage"]: r for r in view.stages_for(tree, d.name)}
    assert by["t-30m"]["lineup_status"] == "LINEUP_UNCONFIRMED" and by["kickoff"]["lineup_status"] == "CONFIRMED"


def test_a_failure_goes_to_the_dead_letter_file_and_the_replay_is_marked(tree):
    d = fixtures(tree)[1]
    f = sorted(d.glob("intel-*.json"))[-1]
    good = f.read_text(encoding="utf-8")
    body = json.loads(good)
    del body["intelligence"]["goals"]
    f.write_text(json.dumps(body), encoding="utf-8")
    rep = freeze.freeze(tree, NOW)
    assert rep["FAILED"] >= 1 and (tree / "artifacts" / "stage_records" / "_failed.jsonl").exists()
    f.write_text(good, encoding="utf-8")                                                            # the cause is fixed
    res = freeze.replay_failed(tree, NOW)
    assert res["replayed"] >= 1 and res["FAILED"] == 0 and not (tree / "artifacts" / "stage_records" / "_failed.jsonl").exists()
    assert any(r.get("replayed") for r in view.stages_for(tree, d.name))
    assert freeze.replay_failed(tree, NOW) == {"replayed": 0}


def test_the_paired_comparison_uses_the_same_fixtures_and_refuses_a_verdict_on_few_weeks(tree):
    freeze.freeze(tree, NOW)
    res = evaluate.evaluate(tree)
    if res["status"] == "MEASURED":
        assert all(m["n"] == res["paired_fixtures"] for m in res["stages"].values())
        assert all(p["verdict"] == "TOO_FEW_WEEKLY_BLOCKS" for p in res["paired_vs_kickoff"].values())   # five fixtures in one week
    text = evaluate.report(res, "test")
    assert "paired set" in text and "| t-24h |" in text


def test_api_and_page_show_the_stage_records(tree):
    freeze.freeze(tree, NOW)
    key = fixtures(tree)[0].name
    c = TestClient(create_app(tree, keys=["k"]), raise_server_exceptions=False)
    r = c.get(f"/v1/fixtures/{key}/stages", headers={"X-API-Key": "k"})
    assert r.status_code == 200 and [s["stage"] for s in r.json()["data"]["stages"]] == ["t-24h", "t-90m", "t-30m", "kickoff"]
    assert c.get("/v1/fixtures/NO__SUCH__2026-01-01/stages", headers={"X-API-Key": "k"}).status_code == 404
    html = pages.stages_card(view.stages_for(tree, key))
    assert "Aşamalara göre tahmin" in html and "bahis tavsiyesi değildir" in html and "Forecast by stage" in i18n.translate_html(html)
    assert pages.stages_card([]) == "" and "bu aşamada tahmin yoktu" in pages.stages_card([{"stage": "t-24h", "status": "NO_FORECAST"}])


def test_the_cloud_job_freezes_publishes_and_restores_the_stage_records_and_the_bundle_ships_them(tree, tmp_path):
    wf = (ROOT / ".github" / "workflows" / "markets-cloud.yml").read_text(encoding="utf-8")
    assert "python -m src.stages.freeze --root . --replay" in wf and "cp -r markets-data-wt/artifacts/stage_records artifacts/" in wf and "artifacts/stage_records/. markets-data-wt/artifacts/stage_records/" in wf
    import importlib.util

    spec = importlib.util.spec_from_file_location("mvb5", ROOT / "scripts" / "make_vercel_bundle.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    freeze.freeze(tree, NOW)
    for x in ("src", "configs"):
        (tree / x).mkdir(exist_ok=True)
        (tree / x / "f").write_text("", encoding="utf-8")
    (tree / "requirements-api.lock").write_text("a==1\n", encoding="utf-8")
    files = mod.build(tmp_path / "out", tree, now=datetime(2026, 10, 11, tzinfo=UTC))
    assert any(f.startswith("artifacts/stage_records/") and f.endswith(".json") for f in files)
