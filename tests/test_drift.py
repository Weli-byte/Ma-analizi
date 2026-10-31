"""Phase 3 sprint 14: drift, data quality and the retrain candidate (ADR 0092).

The quality checks run on the REAL forecast artifacts of the shared excerpt (`real_oddsens_excerpt.json`). Every fault below (stale provider, schema change, duplicate result, future result,
coverage loss, shifted predictions, worse losses) is a CONTROLLED, SYNTHETIC injection labelled TEST_ONLY: it checks that the right alarm fires, it is not a production observation."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from src.drift import monitor, quality, retrain, stats, thresholds

ROOT = Path(__file__).resolve().parents[1]
EXCERPT = ROOT / "tests" / "fixtures" / "real_oddsens_excerpt.json"
TH = json.loads((ROOT / "docs" / "phase3" / "s14_thresholds.json").read_text(encoding="utf-8"))["thresholds"]


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
    (tmp_path / "artifacts" / "markets" / "track_record.json").write_text(json.dumps({"generated_at": data["track_generated_at"], "matches": rows}), encoding="utf-8")
    res = tmp_path / "artifacts" / "results"
    res.mkdir()
    (res / "EPL.json").write_text(json.dumps({"matches": {"ENG_a|ENG_b|2026-10-09": {"home_id": "ENG_a", "away_id": "ENG_b", "kickoff_utc": "2026-10-09T19:00:00+00:00", "home_goals": 1, "away_goals": 0, "first_seen": "2026-10-10T00:00:00+00:00"}}}), encoding="utf-8")
    return tmp_path


def latest_time(tree) -> datetime:
    return max(datetime.fromisoformat(json.loads(f.read_text(encoding="utf-8"))["generated_at"]) for f in (tree / "artifacts" / "markets").glob("*/intel-*.json"))


def test_the_real_stores_pass_schema_duplicate_and_consistency_checks(tree):
    now = latest_time(tree) + timedelta(hours=1)
    q = quality.check(tree, now)
    assert q["schema"]["forecasts_checked"] == 5 and not q["schema"]["forecasts_missing_fields"] and q["duplicates"]["n_result"] == 0
    assert q["freshness"]["forecast"]["status"] == "OK" and q["freshness"]["odds"]["status"] == "NOT_CHECKED" and "consistency" not in q["alarms"]


def test_fault_stale_provider_fires_the_freshness_alarm(tree):
    """TEST_ONLY: the clock is moved ten days ahead, as if every collector had stopped."""
    track_at = datetime.fromisoformat(json.loads((tree / "artifacts" / "markets" / "track_record.json").read_text(encoding="utf-8"))["generated_at"])
    q = quality.check(tree, max(latest_time(tree), track_at) + timedelta(days=10))
    assert {"freshness:live_record", "freshness:forecast", "freshness:results:EPL"} <= set(q["alarms"]) and q["status"] == "ALARM"


def test_fault_schema_change_duplicate_and_future_result_fire_their_alarms(tree):
    """TEST_ONLY: a forecast loses a field, a result is stored twice two days apart, one result has a future kick-off and is also void."""
    f = next((tree / "artifacts" / "markets").glob("*/intel-*.json"))
    files = sorted(f.parent.glob("intel-*.json"))
    body = json.loads(files[-1].read_text(encoding="utf-8"))
    del body["model_version"]
    files[-1].write_text(json.dumps(body), encoding="utf-8")
    store = json.loads((tree / "artifacts" / "results" / "EPL.json").read_text(encoding="utf-8"))
    store["matches"]["ENG_a|ENG_b|2026-10-11"] = {**store["matches"]["ENG_a|ENG_b|2026-10-09"], "kickoff_utc": "2026-10-11T19:00:00+00:00"}
    store["matches"]["ENG_c|ENG_d|2027-01-01"] = {"home_id": "ENG_c", "away_id": "ENG_d", "kickoff_utc": "2027-01-01T15:00:00+00:00", "home_goals": 0, "away_goals": 0}
    store["void"] = {"ENG_c|ENG_d|2027-01-01": {"status": "STATUS_POSTPONED", "source": "espn"}}
    (tree / "artifacts" / "results" / "EPL.json").write_text(json.dumps(store), encoding="utf-8")
    q = quality.check(tree, latest_time(tree) + timedelta(hours=1))
    assert {"schema", "duplicates:results", "consistency"} <= set(q["alarms"])
    assert q["schema"]["forecasts_missing_fields"] == [f.parent.name] and q["consistency"]["result_and_void"] == ["ENG_c|ENG_d|2027-01-01"]


def test_fault_league_coverage_loss_fires(tree):
    """TEST_ONLY: the clock moves five weeks on; the leagues that had forecasts have none in the last two weeks (only leagues with at least COVERAGE_MIN fixtures count)."""
    monkey = quality.COVERAGE_MIN
    quality.COVERAGE_MIN = 1
    try:
        q = quality.check(tree, latest_time(tree) + timedelta(days=17))
    finally:
        quality.COVERAGE_MIN = monkey
    assert any(a.startswith("coverage:") for a in q["alarms"])


def test_statistics_refuse_small_samples_and_measure_a_shift():
    rng = np.random.default_rng(1)
    assert stats.continuous(rng.random(50), rng.random(500))["status"] == "INSUFFICIENT_DATA"
    same = stats.continuous(rng.beta(4, 4, 800), rng.beta(4, 4, 800))
    shifted = stats.continuous(rng.beta(4, 4, 800), rng.beta(6, 3, 800))                    # TEST_ONLY shift
    assert same["psi"] < TH["p_home.psi"] < shifted["psi"] and shifted["ks_p"] < 1e-6
    cat = stats.categorical(["EPL"] * 100 + ["LALIGA"] * 100, ["EPL"] * 200)
    assert cat["js"] > TH["league_mix.js"] and stats.categorical(["EPL"] * 10, ["EPL"] * 200)["status"] == "INSUFFICIENT_DATA"


def _rows(n, start, p_home=0.45, leagues=("EPL", "LALIGA")):
    """TEST_ONLY forecast rows, one every two and a half hours (120 rows = 12.5 days)."""
    rng = np.random.default_rng(2)
    return [{"key": f"k{start}-{i}", "league": leagues[i % len(leagues)], "kickoff": start + timedelta(hours=2.5 * i), "p_home": float(np.clip(rng.normal(p_home, 0.08), 0.05, 0.95)),
             "p_over_2_5": float(rng.uniform(0.4, 0.6)), "p_btts": float(rng.uniform(0.45, 0.6)), "odds": True, "low_confidence": False} for i in range(n)]  # fmt: skip


def test_fault_shifted_predictions_and_a_lost_league_fire_input_drift_and_ordinary_data_does_not():
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    ref = _rows(120, t0)
    ok = monitor.input_drift(ref + _rows(120, t0 + timedelta(days=50)), TH, t0 + timedelta(days=63))
    assert ok["status"] == "OK" and ok["alarms"] == []
    bad = monitor.input_drift(ref + _rows(120, t0 + timedelta(days=50), p_home=0.62, leagues=("EPL",)), TH, t0 + timedelta(days=63))
    assert "input_drift:p_home" in bad["alarms"] and "input_drift:league_mix" in bad["alarms"] and bad["status"] == "ALARM"
    assert monitor.input_drift([], TH, t0)["status"] == "INSUFFICIENT_DATA"


def test_performance_drift_needs_results_and_weeks_and_fires_on_a_clear_worsening():
    t0 = datetime(2026, 1, 5, tzinfo=UTC)
    assert monitor.performance_drift([])["status"] == "INSUFFICIENT_DATA"                    # no settled match: no performance alarm is made up
    rng = np.random.default_rng(3)

    def rows(n, start, base):
        return [{"kickoff": start + timedelta(days=i * 0.5), "1x2": float(base + rng.normal(0, 0.05)), "over_2_5": float(0.69 + rng.normal(0, 0.05)), "btts": float(0.69 + rng.normal(0, 0.05))} for i in range(n)]

    short = rows(100, t0, 0.95) + rows(50, t0 + timedelta(days=50), 0.95)
    compact = [{**r, "kickoff": t0 + timedelta(hours=i)} for i, r in enumerate(short)]        # all in one week
    assert monitor.performance_drift(compact)["status"] == "INSUFFICIENT_DATA"
    worse = rows(100, t0, 0.95) + rows(50, t0 + timedelta(days=60), 1.20)                     # TEST_ONLY: 1X2 losses clearly worse
    res = monitor.performance_drift(worse)
    assert res["status"] == "ALARM" and res["alarms"] == ["performance_drift:1x2"] and res["markets"]["1x2"]["interval"][0] > 0


def test_thresholds_are_calibrated_on_history_only():
    t0 = datetime(2020, 9, 1, tzinfo=UTC)
    rng = np.random.default_rng(4)
    rows = [{"season": "2020-21" if i < 600 else "2023-24", "kickoff": (t0 + timedelta(hours=8 * i)).isoformat(), "league": ["EPL", "LALIGA"][i % 2], "p": [float(rng.uniform(0.3, 0.6)), 0.25, 0.2],
             "po": float(rng.uniform(0.4, 0.6)), "pb": float(rng.uniform(0.4, 0.6))} for i in range(900)]  # TEST_ONLY  # fmt: skip
    res = thresholds.calibrate(rows)
    assert res["window_pairs"] >= 1 and set(res["thresholds"]) >= {"p_home.psi", "league_mix.js"}
    assert all(res["thresholds"][k] == pytest.approx(res["history_max"][k] * thresholds.MARGIN, rel=1e-4) for k in res["thresholds"])
    with pytest.raises(ValueError):
        thresholds.calibrate([r for r in rows if r["season"] == "2023-24"])                    # the test season alone is never used
    stored = json.loads((ROOT / "docs" / "phase3" / "s14_thresholds.json").read_text(encoding="utf-8"))
    assert stored["seasons"] == ["2020-21", "2021-22", "2022-23"] and stored["window_pairs"] >= 10


def test_the_retrain_candidate_is_reproducible_registered_once_and_never_served(tmp_path):
    from src.config import load_config
    from src.markets.data import STATS, StatMatch

    cfg = load_config("markets")
    t0 = datetime(2026, 8, 1, 15, tzinfo=UTC)
    teams = [f"ENG_{c}" for c in "abcdefgh"]
    ms = [StatMatch(f"t{i}", "2026-27", "EPL", t0 + timedelta(hours=12 * i), t0 + timedelta(hours=12 * i + 3), teams[i % 8], teams[(i * 3 + 1) % 8] if (i * 3 + 1) % 8 != i % 8 else teams[(i + 1) % 8],
                    i % 3, (i + 1) % 2, dict.fromkeys(STATS), dict.fromkeys(STATS), None) for i in range(240)]  # TEST_ONLY  # fmt: skip
    as_of = t0 + timedelta(days=125)
    a = retrain.fit_candidate(ms, as_of, cfg, "dv-test", "cfg1")
    b = retrain.fit_candidate(list(reversed(ms)), as_of, cfg, "dv-test", "cfg1")
    assert a["artifact_sha256"] == b["artifact_sha256"] and a["manifest_sha256"] == b["manifest_sha256"] and a["status"] == "SHADOW_CANDIDATE" and a["seed"] is None
    later = retrain.fit_candidate(ms, as_of + timedelta(days=30), cfg, "dv-test", "cfg1")
    assert later["artifact_sha256"] == a["artifact_sha256"] or later["as_of_utc"] != a["as_of_utc"]
    reg = tmp_path / "registry" / "market_candidates.jsonl"
    assert retrain.register(a, reg) == "REGISTERED" and retrain.register(b, reg) == "EXISTS" and len(reg.read_text(encoding="utf-8").splitlines()) == 1
    assert not (tmp_path / "artifacts" / "markets").exists()                                   # nothing reaches the served forecasts


def test_the_report_and_threshold_commands_write_their_documents(tree, tmp_path):
    from src.drift import report

    out = tmp_path / "r.md"
    assert report.main(["--markets-root", str(tree), "--as-of", "2026-10-29T21:00:00Z", "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "## 1. Data quality" in text and "## 2. Input drift" in text and "## 3. Performance drift" in text and "INSUFFICIENT_DATA" in text
    t0 = datetime(2020, 9, 1, tzinfo=UTC)
    rng = np.random.default_rng(5)
    oof = tmp_path / "oof.jsonl"
    oof.write_text("\n".join(json.dumps({"season": "2020-21", "kickoff": (t0 + timedelta(hours=8 * i)).isoformat(), "league": "EPL", "p": [float(rng.uniform(0.3, 0.6)), 0.3, 0.2],
                                          "po": 0.5, "pb": 0.5}) for i in range(700)) + "\n", encoding="utf-8")  # TEST_ONLY  # fmt: skip
    th = tmp_path / "th.json"
    assert thresholds.main(["--oof", str(oof), "--out", str(th)]) == 0 and json.loads(th.read_text(encoding="utf-8"))["window_pairs"] >= 1


def test_alarms_are_sent_once_per_period_by_severity(tmp_path, monkeypatch):
    from src.drift import report
    from src.mlops import notify as nt

    sent = []
    monkeypatch.setattr(nt, "send", lambda text: sent.append(text) or "SENT")                  # no Telegram call in tests
    state = tmp_path / "n.json"
    now = datetime(2026, 11, 2, 10, tzinfo=UTC)
    assert report.severity("freshness:odds") == "warning" and report.severity("schema") == "critical" and report.severity("performance_drift:1x2") == "critical"
    report.notify(["freshness:odds", "schema"], state, now)
    report.notify(["freshness:odds", "schema"], state, now + timedelta(hours=4))                # same day: nothing new
    report.notify(["freshness:odds", "schema"], state, now + timedelta(days=1))                 # next day: the critical one again, the warning not (same ISO week)
    assert len(sent) == 3 and sum("schema" in s for s in sent) == 2 and sum("freshness:odds" in s for s in sent) == 1


def test_the_cloud_job_runs_the_monitor_with_the_quote_folder_and_alarms():
    wf = (ROOT / ".github" / "workflows" / "markets-cloud.yml").read_text(encoding="utf-8")
    assert "python -m src.drift.report --markets-root . --odds-dir artifacts/odds_remote" in wf and "--notify-state artifacts/markets/notified_drift.json" in wf
