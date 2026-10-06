"""S16 MLOps (ADR 0032): operational log, monitors, alerts, registry, retrain gate, report.

Real inputs wherever they exist: the operational log is produced by REAL HTTP exchanges (a local
http.server serving real files through the production `logged_urlopen`), LLM health uses the REAL
benchmark calls recorded from OpenAI/Gemini/Groq, features come from the real EPL fixture. Pure monitor
functions are also called with explicit rows/timestamps as test INPUTS (never as provider output)."""

import json
import shutil
import sys
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.ci_real_data_sanity import AS_OF, FIXTURE_ROOT  # noqa: E402
from src.config import MlopsConfig, config_dir_for, load_config  # noqa: E402
from src.data.pipeline import run_pipeline  # noqa: E402
from src.features.builder import build_features  # noqa: E402
from src.mlops import alerts, monitor  # noqa: E402
from src.mlops import report as report_mod  # noqa: E402
from src.mlops.oplog import heartbeat, logged_urlopen, read_rows  # noqa: E402
from src.mlops.registry import build_registry, write_registry  # noqa: E402
from src.mlops.retrain_gate import evaluate_retrain  # noqa: E402

CAP = Path(__file__).parent / "fixtures" / "real_provider_captures" / "benchmark_historical"
CFG: MlopsConfig = load_config("mlops", REPO_ROOT / "configs")
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


# ----------------------------------------------------------------------- real HTTP -> ops log
@pytest.fixture
def local_server():
    """A real HTTP server (stdlib) serving the real captured files; requests go over a real socket."""
    handler = partial(SimpleHTTPRequestHandler, directory=str(CAP.parent))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_logged_urlopen_records_real_success_latency_and_size(local_server):
    body = logged_urlopen(
        "local", "fdorg_matches", urllib.request.Request(f"{local_server}/fdorg_matches.json"), 10
    )
    (row,) = read_rows("provider_calls.jsonl")
    assert row["ok"] is True and row["status"] == 200 and row["response_bytes"] == len(body) > 1000
    assert (
        row["latency_ms"] > 0 and row["provider"] == "local" and "127.0.0.1" not in json.dumps(row)
    )  # no URL logged


def test_logged_urlopen_records_real_failures_and_reraises(local_server):
    with pytest.raises(urllib.error.HTTPError):
        logged_urlopen("local", "missing", urllib.request.Request(f"{local_server}/nope.json"), 10)
    with pytest.raises(urllib.error.URLError):
        logged_urlopen("local", "refused", urllib.request.Request("http://127.0.0.1:9/x"), 2)
    rows = read_rows("provider_calls.jsonl")
    assert [(r["ok"], r["status"]) for r in rows] == [(False, 404), (False, None)]
    assert rows[0]["error"] == "HTTP 404" and rows[1]["error"] == "URLError"


def test_provider_health_from_the_real_log(local_server):
    for _ in range(3):
        logged_urlopen("local", "ok", urllib.request.Request(f"{local_server}/fdorg_matches.json"), 10)
    with pytest.raises(urllib.error.HTTPError):
        logged_urlopen("local", "bad", urllib.request.Request(f"{local_server}/nope.json"), 10)
    now = datetime.now(UTC)
    h = monitor.provider_health(read_rows("provider_calls.jsonl"), now, CFG)["local"]
    assert (h["requests"], h["errors"], h["error_rate"]) == (4, 1, 0.25) and h[
        "status"
    ] == "WARNING"  # 25% > 20%
    assert h["last_success"] and h["last_failure"] and h["error_kinds"] == {"HTTP 404": 1}
    assert h["latency_p50_ms"] > 0 and h["minutes_since_success"] < 1


def test_provider_staleness_and_failure_states_use_the_time_since_the_last_success():
    old = (NOW - timedelta(minutes=90)).isoformat()
    rows = [
        {
            "ts": old,
            "provider": "p",
            "endpoint": "e",
            "ok": True,
            "latency_ms": 10.0,
            "status": 200,
            "error": None,
        }
    ]
    assert monitor.provider_health(rows, NOW, CFG)["p"]["status"] == "STALE"  # 90 min > 60
    bad = [{**rows[0], "ok": False, "status": 500, "error": "HTTP 500"}]
    assert monitor.provider_health(bad, NOW, CFG)["p"]["status"] == "FAILED"  # never succeeded
    assert monitor.provider_health([], NOW, CFG) == {}


# ------------------------------------------------------------------- heartbeats / data freshness
def test_heartbeat_gap_detection_finds_a_switched_off_machine():
    beats = [
        {"ts": (NOW - timedelta(minutes=m)).isoformat(), "name": "live"}
        for m in (600, 598, 596, 10, 8, 6, 4, 2)
    ]
    h = monitor.heartbeat_gaps(beats, NOW, CFG)
    assert h["live"]["status"] == "WARNING" and h["live"]["n_gaps"] == 1 and h["live"]["gaps"][0]["hours"] > 9
    assert h["snapshot"] == {"status": "NO_DATA", "beats": 0}
    stale = monitor.heartbeat_gaps([{"ts": (NOW - timedelta(hours=5)).isoformat(), "name": "odds"}], NOW, CFG)
    assert stale["odds"]["status"] == "STALE"


def test_heartbeat_function_writes_a_real_timestamped_row():
    heartbeat("snapshot")
    (row,) = read_rows("heartbeats.jsonl")
    assert (
        row["name"] == "snapshot"
        and abs((datetime.now(UTC) - datetime.fromisoformat(row["ts"])).total_seconds()) < 5
    )


def test_data_freshness_levels():
    assert monitor.data_freshness(NOW - timedelta(days=2), NOW, CFG)["status"] == "FRESH"
    assert monitor.data_freshness(NOW - timedelta(days=10), NOW, CFG)["status"] == "WARNING"
    assert monitor.data_freshness(NOW - timedelta(days=40), NOW, CFG)["status"] == "STALE"
    assert monitor.data_freshness(None, NOW, CFG)["status"] == "FAILED"


# ----------------------------------------------------------------------- LLM health (real calls)
def real_llm_calls():
    calls, sizes = [], []
    for prov in ("openai", "gemini", "groq"):
        calls += [
            json.loads(x) for x in (CAP / prov / "calls.jsonl").read_text(encoding="utf-8").splitlines() if x
        ]
        sizes += [
            len(json.loads(x)["text"])
            for x in (CAP / prov / "responses.jsonl").read_text(encoding="utf-8").splitlines()
            if x
        ]
    return calls, sizes


def test_llm_health_from_real_provider_calls_matches_independent_sums():
    calls, sizes = real_llm_calls()
    far_future = datetime.fromisoformat(calls[0]["generated_at"]) + timedelta(hours=1)
    h = monitor.llm_health(calls, sizes, far_future, CFG)
    assert h["calls"] == 6 and h["successful"] == 6 and h["failed"] == 0 and h["status"] == "OK"
    assert h["tokens_total"] == sum(c["total_tokens"] for c in calls)
    assert h["estimated_cost_usd"] == pytest.approx(sum(c["cost_usd"] for c in calls), abs=1e-6)
    assert h["avg_response_chars"] == pytest.approx(np.mean(sizes), abs=0.1)
    assert h["latency_p95_ms"] >= h["latency_p50_ms"] > 0
    assert set(h["by_provider"]) == {"openai", "gemini", "groq"}


def test_llm_health_flags_rate_limit_server_and_schema_failures_and_cost():
    calls, sizes = real_llm_calls()
    rl = {
        **calls[0],
        "status": "provider_error",
        "error_kind": "rate_limit",
        "cost_usd": None,
        "total_tokens": None,
    }
    sv = {**calls[1], "status": "provider_error", "error_kind": "server"}
    sc = {**calls[2], "status": "malformed_json_exhausted", "error_kind": "schema"}
    now = datetime.fromisoformat(calls[0]["generated_at"]) + timedelta(minutes=1)
    h = monitor.llm_health(calls + [rl, sv, sc], sizes, now, CFG)
    assert h["status"] == "WARNING" and {
        "failure_rate",
        "rate_limit_rate",
        "server_error_rate",
        "schema_failure_rate",
    } <= set(h["breaches"])
    assert h["calls_without_cost_estimate"] == 1 and h["failed"] == 3  # failures counted, not dropped
    assert monitor.llm_health([], [], NOW, CFG) == {"status": "NO_DATA"}


# ------------------------------------------------------------------ prediction / metric drift
def real_probs():
    out = []
    for prov in ("openai", "gemini", "groq"):
        out += [
            [r["p_home"], r["p_draw"], r["p_away"]]
            for r in map(
                json.loads, (CAP / prov / "predictions.jsonl").read_text(encoding="utf-8").splitlines()
            )
        ]
    return np.array(out)


def test_psi_is_zero_for_identical_samples_and_large_for_a_shifted_one():
    rng = np.random.default_rng(1)
    ref = rng.beta(5, 3, 500)
    assert monitor.psi(ref, ref) == pytest.approx(0.0, abs=1e-9)
    assert monitor.psi(ref, np.clip(ref * 0.5, 0, 1)) > 0.25


def test_prediction_distribution_reports_insufficient_data_instead_of_a_verdict():
    p = real_probs()
    out = monitor.prediction_distribution({"llm": p[:3], "elo": p}, {"elo": p}, CFG)
    assert out["llm"]["status"] == "INSUFFICIENT_DATA"
    assert out["elo"]["status"] == "OK" and out["elo"]["psi_top_probability"] == pytest.approx(0.0, abs=1e-9)
    assert out["elo"]["mean_probs"] == pytest.approx(list(p.mean(axis=0)), abs=1e-3)
    assert monitor.prediction_distribution({"x": p}, {}, CFG)["x"]["status"] == "NO_REFERENCE"


def test_metric_drift_waits_for_enough_settled_forecasts_then_compares_log_loss():
    outcomes = {}
    for prov in ("openai", "gemini", "groq"):
        outcomes.update(json.loads((CAP / prov / "outcomes.json").read_text(encoding="utf-8")))
    settled = []
    for prov in ("openai", "gemini", "groq"):
        for line in (CAP / prov / "predictions.jsonl").read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            settled.append(
                (np.array([r["p_home"], r["p_draw"], r["p_away"]]), outcomes[r["fixture_id"]]["outcome"])
            )
    assert monitor.metric_drift(settled, 0.7, CFG)["status"] == "INSUFFICIENT_DATA"  # 6 < 20
    small = CFG.model_copy(update={"metric_drift": CFG.metric_drift.model_copy(update={"min_settled": 5})})
    manual = float(np.mean([-np.log(p[y]) for p, y in settled]))
    d = monitor.metric_drift(settled, manual - 0.2, small)
    assert (
        d["log_loss"] == pytest.approx(manual, abs=1e-4)
        and d["status"] == "WARNING"
        and d["delta"] == pytest.approx(0.2, abs=1e-3)
    )
    assert monitor.metric_drift(settled, manual, small)["status"] == "OK"
    assert monitor.metric_drift(settled, None, small)["status"] == "NO_REFERENCE"


# -------------------------------------------------------------------------------- real project
@pytest.fixture(scope="module")
def real_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("mlops") / "proj"
    shutil.copytree(FIXTURE_ROOT, root, ignore=shutil.ignore_patterns("artifacts", "__pycache__"))
    shutil.copy(REPO_ROOT / "configs" / "mlops.yaml", root / "configs" / "mlops.yaml")
    run_pipeline(root, "research", as_of=AS_OF)
    build_features(root, "research", audit_samples=20)
    return root


def test_feature_health_uses_real_features_ok_when_unchanged_and_warns_on_a_shift(real_root):
    from src.data.dataset import resolve_dataset
    from src.features.artifact import load_features

    cdir = config_dir_for(real_root)
    ref = resolve_dataset(real_root / load_config("data", cdir).processed_dir)
    rows = list(load_features(real_root, ref, load_config("model", cdir).feature_version).rows.values())
    assert monitor.feature_health(rows[:50], rows, CFG)["status"] in ("OK", "WARNING")
    same = monitor.feature_health(rows, rows, CFG)
    assert same["status"] == "OK" and same["drifting_features"] == {} and same["features_checked"] > 5
    shifted = [
        {
            k: (v * 40 + 100 if isinstance(v, float) and not k.endswith("_available") else v)
            for k, v in r.items()
        }
        for r in rows[:20]
    ]
    bad = monitor.feature_health(shifted, rows, CFG)
    assert bad["status"] == "WARNING" and bad["drifting_features"]
    assert monitor.feature_health(rows[:2], rows, CFG)["status"] == "INSUFFICIENT_DATA"


def test_report_over_a_real_project_covers_every_monitor_and_flags_a_stale_dataset(real_root, capsys):
    rep = report_mod.build_report(real_root, NOW)
    assert rep["data_freshness"]["status"] == "STALE" and rep["data_freshness"]["age_days"] > 30
    for key in (
        "providers",
        "heartbeats",
        "llm",
        "prediction_distribution",
        "feature_health",
        "metric_drift",
        "checklist",
        "retrain_gate",
    ):
        assert key in rep
    assert rep["llm"] == {"status": "NO_DATA"} and rep["retrain_gate"]["automatic_retraining"] is False
    assert report_mod.main(["--root", str(real_root), "--registry"]) == 0
    assert "[CRITICAL] data_freshness" in capsys.readouterr().out
    assert report_mod.main(["--root", str(real_root), "--fail-on-critical"]) == 1
    assert (real_root / "artifacts" / "registry" / "registry.json").exists()


# ----------------------------------------------------------------------------------- alerts
def test_alerts_cover_each_failure_class_and_persist():
    report = {
        "data_freshness": {"status": "STALE", "latest_result_utc": "x", "age_days": 40},
        "providers": {
            "fpl": {"status": "WARNING", "error_rate": 0.5, "minutes_since_success": 5, "error_kinds": {}}
        },
        "heartbeats": {"live": {"status": "STALE", "last_beat": "t", "minutes_since_last": 999, "n_gaps": 3}},
        "llm": {"breaches": ["rate_limit_rate"], "calls": 10, "failed": 4, "cost_last_24h_usd": 0.1},
        "prediction_distribution": {"elo": {"status": "WARNING", "psi_top_probability": 0.9}},
        "feature_health": {"status": "WARNING", "drifting_features": {"a": 5.0}, "missing_share": 0.1},
        "metric_drift": {"status": "WARNING", "log_loss": 1.1, "reference_log_loss": 0.9, "delta": 0.2},
    }
    out = alerts.evaluate(report)
    assert {a.key for a in out} == {"data_freshness", "provider:fpl", "scheduler:live", "llm:rate_limit_rate",
                                    "prediction_drift:elo", "feature_health", "metric_drift"}  # fmt: skip
    assert {a.severity for a in out if a.key in ("data_freshness", "scheduler:live")} == {"critical"}
    alerts.persist(out, NOW)
    assert len(read_rows("alerts.jsonl")) == len(out) and "[CRITICAL]" in alerts.render(out)
    assert alerts.evaluate({"data_freshness": {"status": "FRESH"}}) == [] and alerts.render([]) == "no alerts"


# --------------------------------------------------------------------------------- registry
def test_registry_lists_models_llm_prompts_calibration_and_changes_only_when_content_changes(tmp_path):
    root = tmp_path / "proj"
    for prov in ("openai", "gemini"):
        run = root / "artifacts" / "llm_runs" / f"benchmark_historical_dv-6f3af90c6bdb_{prov}"
        shutil.copytree(CAP / prov, run)
    reg = build_registry(root)
    assert {m["model_id"] for m in reg["models"]} >= {"elo", "poisson", "xgboost", "lightgbm"}
    assert {(m["provider"], m["model_class"]) for m in reg["llm"]["models"]} == {
        ("openai", "LLM_REAL"),
        ("gemini", "LLM_REAL"),
    }
    assert (
        reg["llm"]["prompt"]["prompt_version"] == "llm-prompt-v2"
        and reg["llm"]["calibration"]["fitted"] == {}
    )
    assert {a["data_version"] for a in reg["artifacts"]} == {"dv-6f3af90c6bdb"}
    path, changed = write_registry(root, NOW)
    assert changed and json.loads(path.read_text(encoding="utf-8"))["registry_hash"] == reg["registry_hash"]
    assert write_registry(root, NOW)[1] is False  # same content: no new history line
    (
        root / "artifacts" / "llm_runs" / "benchmark_historical_dv-6f3af90c6bdb_openai" / "extra.txt"
    ).write_text("x")
    assert write_registry(root, NOW)[1] is True
    assert len((root / "artifacts" / "registry" / "registry_history.jsonl").read_text().splitlines()) == 2


# ------------------------------------------------------------------------------ retrain gate
def test_retrain_gate_never_retrains_and_needs_new_data_validation_audit_and_approval(real_root):
    g = evaluate_retrain(real_root, "fv2")
    assert g["eligible"] is False and g["automatic_retraining"] is False
    assert {c["check"] for c in g["checks"]} == {"current_data_version", "new_data_version", "validation_report_for_current",
                                                 "feature_artifact_lineage", "llm_leakage_audit", "human_approval"}  # fmt: skip
    by = {c["check"]: c["ok"] for c in g["checks"]}
    assert by["current_data_version"] and by["feature_artifact_lineage"] and by["llm_leakage_audit"]
    assert not by["human_approval"] and not by["validation_report_for_current"]
    cur = json.loads((real_root / "data" / "processed" / "CURRENT.json").read_text(encoding="utf-8"))[
        "data_version"
    ]
    approval = real_root / "artifacts" / "registry"
    approval.mkdir(parents=True, exist_ok=True)
    (approval / "retrain_approval.json").write_text(json.dumps({"data_version": cur, "approved_by": "owner"}))
    assert {c["check"]: c["ok"] for c in evaluate_retrain(real_root, "fv2")["checks"]}[
        "human_approval"
    ] is True
    assert evaluate_retrain(real_root, "fv2")["eligible"] is False  # approval alone is not enough


def test_checklist_marks_only_real_evidence_as_done(real_root, monkeypatch):
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)
    items = {c["item"]: c["done"] for c in report_mod.checklist(real_root)}
    assert not any(items.values()) or all(isinstance(v, bool) for v in items.values())
    assert items["exact-timestamp odds source configured (THE_ODDS_API_KEY)"] is False
    cap = real_root / "artifacts" / "live" / "_captures"
    cap.mkdir(parents=True)
    (cap / "fdorg_1_IN_PLAY.json").write_text("{}")
    assert {c["item"]: c["done"] for c in report_mod.checklist(real_root)}[
        "first real in-play football-data.org payload captured"
    ]
