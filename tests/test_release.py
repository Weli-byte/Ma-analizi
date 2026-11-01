"""Phase 3 sprint 18: production hardening (ADR 0097). Forecast data are the REAL artifacts of the shared excerpt (`real_oddsens_excerpt.json`); HTTP failures are offline stubs of
`urllib.request.urlopen` (TEST_ONLY: they test the breaker's classification, they are not evidence about any provider); drill rows are TEST_ONLY."""

import io
import json
import urllib.error
from datetime import UTC, datetime, timedelta
from email.message import Message
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.mlops import oplog
from src.release import breaker, canary, drills, gate, slo
from src.web import i18n, pages

ROOT = Path(__file__).resolve().parents[1]
EXCERPT = ROOT / "tests" / "fixtures" / "real_oddsens_excerpt.json"


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
    (tmp_path / "artifacts" / "markets" / "track_record.json").write_text(json.dumps({"generated_at": "2026-10-12T08:00:00+00:00", "matches": rows}), encoding="utf-8")
    return tmp_path


@pytest.fixture
def armed(monkeypatch, tmp_path):
    monkeypatch.setenv("PROVIDER_BREAKER", "1")
    monkeypatch.setenv("PROVIDER_BREAKER_STATE", str(tmp_path / "breakers.json"))
    monkeypatch.setattr(oplog, "record_call", lambda *a, **k: None)
    monkeypatch.setattr(breaker, "RETRY_PAUSE", 0.0)
    breaker.reset()
    yield tmp_path / "breakers.json"
    breaker.reset()


def _http(code: int, retry_after: str | None = None):
    h = Message()
    if retry_after:
        h["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://example.invalid", code, "x", h, io.BytesIO(b""))


class _Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _stub(monkeypatch, outcomes):
    calls = []

    def fake(req, timeout=None):
        calls.append(1)
        o = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
        if isinstance(o, Exception):
            raise o
        return _Resp(o)

    monkeypatch.setattr(oplog.urllib.request, "urlopen", fake)
    return calls


def test_429_opens_the_circuit_without_a_retry_and_later_calls_fail_fast(armed, monkeypatch):
    calls = _stub(monkeypatch, [_http(429, "120")])
    with pytest.raises(urllib.error.HTTPError):
        oplog.logged_urlopen("p", "e", "https://example.invalid", 5)
    assert len(calls) == 1 and breaker.state("p")["reason"] == "RATE_LIMITED"
    with pytest.raises(breaker.CircuitOpen):
        oplog.logged_urlopen("p", "e", "https://example.invalid", 5)
    assert len(calls) == 1                                                   # no call while open
    assert json.loads(armed.read_text(encoding="utf-8"))["p"]["opened_total"] == 1   # persisted for the next run


def test_a_503_is_retried_once_and_a_success_closes_the_circuit(armed, monkeypatch):
    calls = _stub(monkeypatch, [_http(503), b"ok"])
    assert oplog.logged_urlopen("q", "e", "https://example.invalid", 5) == b"ok" and len(calls) == 2
    assert breaker.state("q")["failures"] == 0


def test_repeated_failures_open_and_auth_errors_open_long_and_404_is_not_a_provider_fault(armed, monkeypatch):
    _stub(monkeypatch, [_http(500)])
    for _ in range(breaker.FAILURE_THRESHOLD):
        with pytest.raises(urllib.error.HTTPError):
            oplog.logged_urlopen("r", "e", "https://example.invalid", 5)
    assert breaker.snapshot()["r"]["open"] and breaker.state("r")["reason"] == "FAILURES"
    _stub(monkeypatch, [_http(401)])
    with pytest.raises(urllib.error.HTTPError):
        oplog.logged_urlopen("s", "e", "https://example.invalid", 5)
    assert breaker.state("s")["reason"] == "AUTH" and breaker.state("s")["open_until"] - breaker._now() > breaker.AUTH_COOLDOWN - 60
    _stub(monkeypatch, [_http(404)])
    with pytest.raises(urllib.error.HTTPError):
        oplog.logged_urlopen("t", "e", "https://example.invalid", 5)
    assert breaker.state("t")["failures"] == 0 and not breaker.snapshot()["t"]["open"]


def test_the_breaker_is_off_by_default_and_observe_never_retries(monkeypatch):
    monkeypatch.delenv("PROVIDER_BREAKER", raising=False)
    breaker.reset()
    assert not breaker.enabled() and breaker.observe("x", lambda: 7) == 7
    monkeypatch.setenv("PROVIDER_BREAKER", "1")
    n = []

    def boom():
        n.append(1)
        raise _http(429)

    with pytest.raises(urllib.error.HTTPError):
        breaker.observe("odds", boom)
    with pytest.raises(breaker.CircuitOpen):
        breaker.observe("odds", boom)
    assert len(n) == 1
    breaker.schema_error("odds")
    assert breaker.state("odds")["schema_errors"] == 1
    breaker.reset()


def test_slo_indicators_on_real_forecasts(tree):
    from src.markets.track import load_artifacts

    arts = load_artifacts(tree)
    kick = max(datetime.fromisoformat(v[-1]["kickoff_utc"]) for v in arts.values())
    now = kick + timedelta(hours=12)
    res = slo.measure(tree, now, None, None, [])
    s = res["sli"]
    assert s["forecast_freshness_hours"] > 0 and s["settlement"] == 1.0 and s["stages_due_7d"] > 0
    assert s["stage_completion"] == 0.0                                       # no stage records in the excerpt: honestly 0, not assumed
    assert s["calibration_ece_1x2"] is None and s["ece_n"] == 5               # below 100 matches no calibration verdict
    assert res["objectives"]["calibration_ece_1x2"]["status"] == "NOT_MEASURED" and res["objectives"]["stage_completion"]["status"] == "BREACHED"


def test_slo_evaluation_error_budget_and_noise_floor():
    now = datetime(2026, 11, 1, 12, tzinfo=UTC)
    hist = [{"ts": (now - timedelta(hours=h)).isoformat()} for h in range(1, 101) if h % 10]   # TEST_ONLY: every tenth hour missing
    ra = slo.run_availability(hist, now)
    assert ra["hours_expected"] == 100 and ra["run_availability_28d"] < 0.95 and ra["budget_left"] < 0
    ev = slo.evaluate({"forecast_freshness_hours": 7.0, "api_availability": 0.95, "calibration_ece_1x2": 0.07, "ece_null95": 0.08})
    assert ev["forecast_freshness_hours"]["status"] == "CRITICAL" and ev["api_availability"]["status"] == "BREACHED"
    assert ev["calibration_ece_1x2"]["status"] == "MET" and ev["odds_freshness_hours"]["status"] == "NOT_MEASURED"   # within the noise floor at this n


def test_slo_probe_and_cli(tree, monkeypatch, tmp_path):
    monkeypatch.setattr(slo.urllib.request, "urlopen", lambda req, timeout=None: _Resp(b"{}"))   # TEST_ONLY offline stub
    p = slo.probe("https://example.invalid", n=5)
    assert p["api_availability"] == 1.0 and p["probe_n"] == 5
    out, hist = tmp_path / "slo.json", tmp_path / "h.jsonl"
    assert slo.main(["--markets-root", str(tree), "--history", str(hist), "--out", str(out), "--notify-state", str(tmp_path / "n.json")]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["objectives"] and len(slo.read_history(hist)) == 1


def test_canary_routes_deterministically_and_rolls_back_on_a_stop_criterion(tmp_path):
    cfg = canary.load()
    assert cfg["challenger"] is None and canary.route("u1", cfg) == "champion" and canary.evaluate(cfg, {})["action"] == "HOLD"
    c5 = {**cfg, "challenger": "cand", "stage": "canary_5"}
    share = sum(canary.route(f"u{i}", c5) == "challenger" for i in range(4000)) / 4000
    assert 0.03 < share < 0.07 and canary.route("u7", c5) == canary.route("u7", c5) and canary.route("u7", {**c5, "kill_switch": True}) == "champion"
    good = {"days": 20, "settled": 250, "delta_interval": [-0.02, -0.005], "ece_delta": 0.0, "critical_slo": [], "leakage": "PASS"}
    assert canary.evaluate(c5, good)["action"] == "ADVANCE_RECOMMENDED"
    assert canary.evaluate(c5, {**good, "settled": 50})["action"] == "HOLD"                  # a small sample never advances
    assert canary.evaluate({**c5, "stage": "full_100"}, {**good, "days": 30, "settled": 700})["action"] == "COMPLETE"
    assert canary.evaluate(c5, {**good, "critical_slo": ["api_availability"]})["action"] == "ROLLBACK"
    assert canary.evaluate(c5, {**good, "leakage": "FAIL"})["action"] == "ROLLBACK"
    assert canary.bindable({"promotion_gate": "PROMOTION_CANDIDATE", "leakage_gate": "PASS"}) and not canary.bindable({"promotion_gate": "NO_GAIN"})
    p = tmp_path / "c.yaml"
    p.write_text(canary.CONFIG.read_text(encoding="utf-8").replace("stage: shadow", "stage: canary_5"), encoding="utf-8")
    with pytest.raises(ValueError, match="challenger"):
        canary.load(p)


def test_drills_migration_and_model_rollback_pass(tmp_path):
    assert drills.migration()["status"] == "PASS"
    assert drills.model_rollback()["status"] == "PASS"
    out = tmp_path / "d.json"
    assert drills.main(["record-workflow", "--name", "deploy_dry_run", "--run-id", "1", "--conclusion", "success", "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["deploy_dry_run"]["status"] == "PASS"


def test_restore_drill_verifies_every_file_and_forecast_hash(tree):
    r = drills.restore(tree)
    assert r["status"] == "PASS" and r["forecasts"] > 0 and r["files_mismatched"] == 0 and r["forecast_hash_mismatches"] == 0


def test_the_release_gate(tmp_path, monkeypatch):
    assert gate.evidence()["status"] == "PASS" and gate.champion_pin()["status"] == "PASS"
    sec = gate.security(None)
    assert sec["status"] == "PASS" and sec["evidence"]["api_fails_closed"] and sec["evidence"]["api_rate_limited"] and sec["evidence"]["workflow_permissions"]
    lic = gate.licences()
    assert lic["research_product"]["status"] == "PASS" and lic["commercial_launch"]["status"] == "FAIL"   # CONDITIONAL is never approved
    res = gate.gate("success", None, ROOT / "docs" / "phase3" / "s18_drills.json")
    assert res["research_release"] == "GO" and res["commercial_launch"] == "NO_GO"
    assert gate.gate(None, None, tmp_path / "none.json")["research_release"] == "NO_GO"              # no drills recorded
    bundle = tmp_path / "b"
    bundle.mkdir()
    monkeypatch.setenv("VERCEL_TOKEN", "TEST_ONLY_value_1234567")
    (bundle / "x.js").write_text("const t = 'TEST_ONLY_value_1234567';", encoding="utf-8")
    assert gate.security(bundle)["evidence"]["bundle_has_no_secret"] is False
    assert gate.main(["--bundle", str(bundle), "--require-clean-bundle"]) == 1


def test_date_only_kickoffs_are_labelled_skipped_by_the_freezer_and_kept_out_of_the_slo(tree):
    from src.stages import freeze

    d = next((tree / "artifacts" / "markets").glob("*__*__*"))
    f = sorted(d.glob("intel-*.json"))[-1]
    body = json.loads(f.read_text(encoding="utf-8"))
    body["kickoff_time_known"] = False                                       # TEST_ONLY: a date-only source
    f.write_text(json.dumps(body), encoding="utf-8")
    rep = freeze.freeze(tree, datetime.fromisoformat(body["kickoff_utc"]) + timedelta(hours=1))
    assert rep["TIME_UNKNOWN"] == 1
    i = {"kickoff_utc": body["kickoff_utc"], "kickoff_time_known": False}
    assert "saat belli değil" in pages.kickoff_label(i) and "time not set" in i18n.translate_html(pages.kickoff_label(i))
    assert pages.kickoff_label({**i, "kickoff_time_known": True}) == pages.kickoff_tr(body["kickoff_utc"])


def test_status_page_and_route(tree):
    c = TestClient(create_app(tree, keys=["k"]), raise_server_exceptions=False)
    assert "Henüz ölçüm yok" in c.get("/durum").text
    now = datetime.now(UTC)
    res = slo.measure(tree, now, None, None, [])
    (tree / "artifacts" / "markets" / "slo.json").write_text(json.dumps(res), encoding="utf-8")
    html = c.get("/durum").text
    assert "Hizmet durumu" in html and "En yeni tahminin yaşı" in html and "ölçülemeyen bir hedef karşılanmış sayılmaz" in html
    assert "slo.json" in (ROOT / "scripts" / "make_vercel_bundle.py").read_text(encoding="utf-8")
