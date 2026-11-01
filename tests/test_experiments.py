"""Phase 3 sprint 17: ablation laboratory and promotion gates (ADR 0096). The comparisons run on REAL out-of-fold rows (`real_calibsel_oof_excerpt.jsonl`, `real_oof_rates_excerpt.jsonl`)
and REAL published forecasts (`real_oddsens_excerpt.json`); every edited row is marked TEST_ONLY."""

import copy
import dataclasses
import json
from pathlib import Path

import pytest

from src.calibsel.oof import read
from src.experiments import gates, lab, registry

ROOT = Path(__file__).resolve().parents[1]
OOF = ROOT / "tests" / "fixtures" / "real_calibsel_oof_excerpt.jsonl"
RATES = ROOT / "tests" / "fixtures" / "real_oof_rates_excerpt.jsonl"
EXCERPT = ROOT / "tests" / "fixtures" / "real_oddsens_excerpt.json"


@pytest.fixture
def markets(tmp_path):
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


def test_the_committed_specs_are_the_registered_ones():
    for s in lab.SPECS:
        reg = json.loads((registry.BASE / s.id / "spec.json").read_text(encoding="utf-8"))
        assert reg["spec_sha256"] == s.sha256(), s.id
    assert json.loads((registry.BASE / "champion.json").read_text(encoding="utf-8"))["model_version"] == "markets-1.0.0"


def test_a_result_needs_a_registration_and_is_written_once(tmp_path):
    s = lab.SPECS[0]
    with pytest.raises(registry.RegistryError, match="not registered"):
        registry.record_result(s, {"decision": "X"}, tmp_path)
    assert registry.register(s, tmp_path) == "REGISTERED" and registry.register(s, tmp_path) == "ALREADY_REGISTERED"
    assert registry.record_result(s, {"decision": "X"}, tmp_path) == "RECORDED"
    with pytest.raises(registry.RegistryError, match="already has a result"):
        registry.record_result(s, {"decision": "BETTER"}, tmp_path)
    changed = dataclasses.replace(s, min_practical_effect=0.5)
    with pytest.raises(registry.RegistryError, match="new experiment id"):
        registry.register(changed, tmp_path)
    with pytest.raises(registry.RegistryError, match="registered hash"):
        registry.record_result(changed, {}, tmp_path)
    with pytest.raises(registry.RegistryError, match="unknown track"):
        registry.register(dataclasses.replace(s, id="x", track="RANDOM_SPLIT"), tmp_path)
    assert [x["id"] for x in registry.load_all(tmp_path)] == [s.id]


def test_the_leakage_gate_catches_late_fits_final_test_seasons_and_overlaps():
    rows = read(OOF)
    assert gates.leakage_oof(rows, lab.HISTORY, lab.TEST)["status"] == "PASS"
    late = copy.deepcopy(rows[:3])
    late[0]["fit_as_of"] = "2030-01-01T00:00:00+00:00"                                   # TEST_ONLY: a model fitted after the kick-off
    late[1]["season"] = "2024-25"                                                         # TEST_ONLY: a locked season
    res = gates.leakage_oof(late, lab.HISTORY, ("2022-23",))
    assert res["status"] == "FAIL" and len(res["problems"]) == 3


def test_the_live_leakage_gate_rejects_odds_seen_after_the_cutoff():
    arts = {"k": [{"information_cutoff": "2026-10-10T10:00:00+00:00", "kickoff_utc": "2026-10-10T18:00:00+00:00", "market_source": {"observed_at": "2026-10-10T09:00:00+00:00"}},
                  {"information_cutoff": "2026-10-10T19:00:00+00:00", "kickoff_utc": "2026-10-10T18:00:00+00:00", "market_source": None}]}  # fmt: skip
    ok = gates.leakage_live(arts)
    assert ok["status"] == "PASS" and ok["post_kickoff_forecasts_not_scored"] == 1
    arts["k"][0]["market_source"]["observed_at"] = "2026-10-10T11:00:00+00:00"            # TEST_ONLY: odds after the cutoff
    assert gates.leakage_live(arts)["status"] == "FAIL"


def test_the_promotion_gate_needs_every_check():
    good = {"interval": [-0.02, -0.01]}
    assert gates.promotion(good, {}, 0.0, 1.0, 0, "PASS", True, 0.002)["decision"] == "PROMOTION_CANDIDATE"
    assert gates.promotion({"interval": [-0.02, 0.001]}, {}, 0.0, 1.0, 0, "PASS", True, 0.002)["decision"] == "NO_GAIN"
    assert not gates.promotion(good, {"EPL": {"n": 150, "interval": [0.001, 0.02]}}, 0.0, 1.0, 0, "PASS", True, 0.002)["checks"]["no_severe_league_regression"]
    assert not gates.promotion(good, {}, 0.01, 1.0, 0, "PASS", True, 0.002)["checks"]["calibration_non_regression"]
    assert not gates.promotion(good, {}, 0.0, 0.9, 0, "PASS", True, 0.002)["checks"]["coverage"]
    assert not gates.promotion(good, {}, 0.0, 1.0, 3, "PASS", True, 0.002)["checks"]["cost"]
    assert not gates.promotion(good, {}, 0.0, 1.0, 0, "PASS", False, 0.002)["checks"]["reproduced"]
    assert gates.promotion(good, {}, 0.0, 1.0, 0, "FAIL", True, 0.002)["decision"] == "BLOCKED_LEAKAGE"


def test_the_matrix_runs_on_real_data_and_records_once(tmp_path, markets):
    base = tmp_path / "exp"
    done = lab.run(OOF, markets, base)
    assert done["S17-E01-champion-vs-naive-oof"] == "RECORDED" and done["S17-E10-llm"] == "RECORDED"
    assert done["S17-E06-bivariate-poisson"] == "WAITING_FOR_DATA"                       # the excerpt has no expected goals
    e1 = json.loads((base / "S17-E01-champion-vs-naive-oof" / "result.json").read_text(encoding="utf-8"))
    assert e1["leakage"]["status"] == "PASS" and e1["markets"]["1x2"]["paired"]["interval"][1] < 0   # the model beats the base rates on 1X2
    e2 = json.loads((base / "S17-E02-champion-vs-naive-live" / "result.json").read_text(encoding="utf-8"))
    assert e2["decision"] == "TOO_FEW_WEEKLY_BLOCKS" and e2["n"] == 5
    assert json.loads((base / "S17-E10-llm" / "result.json").read_text(encoding="utf-8"))["status"] == "NOT_RUN"
    assert lab.run(OOF, markets, base)["S17-E01-champion-vs-naive-oof"] == "ALREADY_RECORDED"
    text = lab.report(base)
    assert "| S17-E08-xg | NOT_RUN | NOT_RUN |" in text and "leakage gate PASS" in text


@pytest.mark.skipif(not RATES.exists(), reason="rates excerpt not built")
def test_goal_families_are_compared_through_the_promotion_gate():
    rows = read(RATES)
    res = lab.goal_family(rows, "negbin")
    assert res["reproduced"] and res["leakage"]["status"] == "PASS" and res["leakage"]["rows_without_fit_time"] == 0
    assert set(res["markets"]) == {"1x2", "over_2_5", "btts"} and res["decision"] in ("NO_GAIN", "PROMOTION_CANDIDATE")
    assert all(set(m["gate"]["checks"]) >= {"primary_metric", "leakage_gate", "cost"} for m in res["markets"].values())


def test_cli_register_and_report(tmp_path, capsys):
    assert lab.main(["register"]) == 0 and "ALREADY_REGISTERED" in capsys.readouterr().out
    out = tmp_path / "r.md"
    assert lab.main(["report", "--out", str(out)]) == 0 and "ablation laboratory" in out.read_text(encoding="utf-8")
