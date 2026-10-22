"""Sprint 6 (ADR 0068): monitoring and fail-closed alarms for the shadow / canary layer.

Each check returns Alert rows (severity, key, message, fail_closed). A fail-closed alert trips the kill switch flag file: every league route goes back to GLOBAL,
stored records and artifacts are untouched. Two rules keep the alarms honest: a metric alarm needs a minimum number of RESOLVED pairs (a small N never alarms),
and a result that has not arrived yet is "pending" until kick-off + grace (a late label is not a negative signal). Drift (a change of the input / output
distribution, PSI) is reported separately from performance (log loss on resolved matches): moving inputs are not evidence of lower accuracy."""

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from src.league_models.prediction_router import artifact_ok
from src.mlops.monitor import psi

from .control import LeagueModelRegistry, RolloutConfig, trip_kill_switch
from .evaluation import MIN_N_FOR_CLAIM, lag_report, pairs
from .records import SCHEMA_VERSION, _append

CRITICAL, WARNING, INFO = "critical", "warning", "info"


@dataclass(frozen=True)
class Alert:
    severity: str
    key: str
    message: str
    fail_closed: bool = False


def check_records(records: list[dict], cfg: RolloutConfig) -> list[Alert]:
    out: list[Alert] = []
    tol = cfg["monitoring"]["max_p_sum_error"]
    bad_schema = [r["fixture_id"] for r in records if r.get("schema_version") != SCHEMA_VERSION]
    if bad_schema:
        out.append(Alert(CRITICAL, "schema_mismatch", f"{len(bad_schema)} record(s) carry an unknown schema version", True))
    malformed = []
    for r in records:
        one = [r.get(k) for k in ("p_home", "p_draw", "p_away")]
        vals = [r.get(k) for k in ("p_home", "p_draw", "p_away", "p_over25", "p_btts_yes")]
        if any(v is not None and (not np.isfinite(v) or v < -tol or v > 1 + tol) for v in vals):
            malformed.append(r["fixture_id"])
        elif all(x is not None for x in one) and abs(sum(one) - 1) > tol:
            malformed.append(r["fixture_id"])
    if malformed:
        out.append(Alert(CRITICAL, "probabilities_malformed", f"{len(malformed)} record(s) with probabilities out of bounds or not summing to 1", True))
    ident: dict[str, tuple] = {}
    for r in records:
        sig = (r["league_id"], r["kickoff_utc"])
        if ident.setdefault(r["fixture_id"], sig) != sig:
            out.append(Alert(CRITICAL, "fixture_identity_conflict", f"{r['fixture_id']} appears with two different league / kick-off values", True))
            break
    nonnull_lineup = [r["fixture_id"] for r in records if r.get("coverage_flags", {}).get("lineups") not in (None, "UNKNOWN", "UNKNOWN_NOT_A_FEATURE")]
    if nonnull_lineup:
        out.append(Alert(CRITICAL, "lineups_not_unknown", "a record claims known lineups: not a feature of this universe", True))
    visible = [r["fixture_id"] for r in records if r.get("visible_to_users")]
    if visible:
        out.append(Alert(CRITICAL, "shadow_record_visible", "a shadow record is marked user-visible", True))
    return out


def check_runs(runs: list[dict], cfg: RolloutConfig, now: datetime) -> list[Alert]:
    m = cfg["monitoring"]
    out: list[Alert] = []
    if not runs:
        return [Alert(WARNING, "no_shadow_run", "no shadow run recorded yet")]
    last = runs[-1]
    age_h = (now - datetime.fromisoformat(last["run_at_utc"])).total_seconds() / 3600
    if age_h > m["max_snapshot_age_hours"]:
        out.append(Alert(WARNING, "scheduler_stale", f"the last shadow run is {age_h:.1f} h old (limit {m['max_snapshot_age_hours']} h)"))
    tail = runs[-3:]
    if len(tail) == 3 and all(r["status"] in ("PROVIDER_FAILED", "NOT_CONFIGURED") for r in tail):
        out.append(Alert(CRITICAL, "provider_unavailable", "the schedule provider failed in 3 consecutive runs (429 / 5xx / network / key)", True))
    elif last["status"] == "PROVIDER_FAILED":
        out.append(Alert(WARNING, "provider_error", f"provider errors in the last run: {[e['error'] for e in last['provider_errors']]}"))
    if last["status"] == "NOT_CONFIGURED":
        out.append(Alert(WARNING, "provider_not_configured", "no schedule provider key: shadow runs are NOT_CONFIGURED"))
    made = last["records"]["CREATED"] + last["records"]["EXISTS"]
    if last["fixtures_mapped"] > 0 and made == 0:
        out.append(Alert(CRITICAL, "zero_coverage", "fixtures were due and mapped but no record was stored", True))
    if last["status"] in ("RECORDS_REFUSED", "DUPLICATE_FIXTURE_IDS"):
        out.append(Alert(CRITICAL, "records_refused", f"the last run ended {last['status']}", True))
    if last.get("model_errors"):
        out.append(Alert(WARNING, "model_missing", f"candidate model(s) unavailable and skipped, not guessed: {sorted(last['model_errors'])}"))
    total_attempts = made + len([s for s in last["skipped"] if "family" in s])
    if total_attempts and len([s for s in last["skipped"] if "family" in s]) / total_attempts > m["max_fallback_rate"]:
        out.append(Alert(WARNING, "fallback_rate", "more than the allowed share of model predictions was skipped (missing model / refusal)"))
    slow = {k: v for k, v in last.get("per_fixture_ms", {}).items() if v > 1000.0}
    cfg_ms = cfg["shadow"]["max_inference_ms_per_fixture"]
    if any(v > cfg_ms for v in last.get("per_fixture_ms", {}).values()):
        out.append(Alert(WARNING, "slow_inference", f"inference above {cfg_ms} ms per fixture: {slow}"))
    return out


def check_registry(registry: LeagueModelRegistry) -> list[Alert]:
    out = []
    if not registry.root.exists():  # the run output that holds the artifacts is not on this machine: that is NOT_VERIFIED, not a corrupted artifact
        return [Alert(WARNING, "artifact_store_missing", f"artifact directory {registry.root.name} is absent: artifact hashes NOT_VERIFIED on this machine")]
    for e in registry.entries:
        ok, why = artifact_ok(e, registry.root)
        if not ok:
            crit = e.mode == "GLOBAL"
            out.append(Alert(CRITICAL if crit else WARNING, f"artifact:{e.model_id}:{e.target}:{e.league}", why, crit))
    return out


def check_licenses(records: list[dict], licenses: dict) -> list[Alert]:
    out = []
    used = {lid for r in records for lid in r.get("data_license_ids", [])}
    for lid in sorted(used):
        lic = licenses.get(lid)
        if lic is None:
            out.append(Alert(CRITICAL, f"license:{lid}", "licence unknown (unknown source = blocked)", True))
        elif lic.plan_status == "BLOCKED":
            out.append(Alert(CRITICAL, f"license:{lid}", "licence is BLOCKED/revoked", True))
        elif lic.plan_status != "PERMITTED":
            out.append(Alert(INFO, f"license:{lid}", f"plan_status {lic.plan_status}: records are research evidence only; a canary can never serve them"))
    return out


def check_drift_and_performance(records: list[dict], outcomes: dict[str, dict], cfg: RolloutConfig, now: datetime) -> list[Alert]:
    m = cfg["monitoring"]
    out: list[Alert] = []
    glob = [r["p_home"] for r in sorted(records, key=lambda r: r["kickoff_utc"]) if r["family"] == "global" and r.get("p_home") is not None]
    if len(glob) >= 2 * m["min_drift_rows"]:
        half = len(glob) // 2
        v = psi(np.array(glob[:half]), np.array(glob[half:]))
        if v >= m["psi_critical"]:
            out.append(Alert(WARNING, "drift_critical", f"the global p_home distribution moved (PSI {v:.2f}); this is input drift, not a measured accuracy change"))
        elif v >= m["psi_warn"]:
            out.append(Alert(INFO, "drift_warn", f"PSI {v:.2f} between the first and second half of the records"))
    else:
        out.append(Alert(INFO, "drift_insufficient_rows", f"{len(glob)} global records < {2 * m['min_drift_rows']}: drift is not assessed"))
    for (stage, target, fam), rows in pairs(records, outcomes).items():
        if len(rows) < m["min_resolved_for_metric_alarm"]:
            out.append(Alert(INFO, f"metric_small_n:{fam}:{stage}", f"{len(rows)} resolved pairs < {m['min_resolved_for_metric_alarm']}: no performance alarm (small N)"))
            continue
        pc = np.array([x[0] for x in rows])
        pg = np.array([x[1] for x in rows])
        y = np.array([x[2] for x in rows])
        from .evaluation import _loss

        d = float(_loss(target, pc, y).mean() - _loss(target, pg, y).mean())
        if d > m["max_log_loss_regression"]:
            out.append(Alert(WARNING, f"performance_regression:{fam}:{stage}", f"candidate minus global log loss {d:+.4f} on {len(rows)} resolved pairs", True))
    lag = lag_report(records, outcomes, now, m["outcome_lag_grace_hours"])
    if lag["max_lag_hours"] > m["outcome_lag_alarm_hours"]:
        out.append(Alert(WARNING, "outcome_lag", f"{lag['late_fixtures']} fixture(s) without a result for more than {m['outcome_lag_alarm_hours']} h"))
    return out


def run_checks(*, records: list[dict], outcomes: dict[str, dict], runs: list[dict], registry: LeagueModelRegistry | None, licenses: dict, cfg: RolloutConfig, now: datetime) -> list[Alert]:
    alerts = check_records(records, cfg) + check_runs(runs, cfg, now) + check_licenses(records, licenses) + check_drift_and_performance(records, outcomes, cfg, now)
    if registry is not None:
        alerts += check_registry(registry)
    return alerts


def apply_alerts(alerts: list[Alert], cfg: RolloutConfig, root: Path, out_dir: Path, now: datetime) -> dict:
    """Append the alerts to the log; a fail-closed alert trips the kill switch (idempotent). Returns the decision."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for a in alerts:
        _append(out_dir / "alerts.jsonl", json.dumps({"at": now.isoformat(), **asdict(a)}, sort_keys=True))
    tripped = [a for a in alerts if a.fail_closed]
    flag = None
    if tripped:
        flag = str(trip_kill_switch(cfg, "; ".join(f"{a.key}: {a.message}" for a in tripped)[:500], root, now))
    return {"alerts": len(alerts), "critical": len([a for a in alerts if a.severity == CRITICAL]), "fail_closed": bool(tripped), "kill_switch_flag": flag,
            "routing": "GLOBAL_ONLY (kill switch)" if tripped else "UNCHANGED"}  # fmt: skip


__all__ = ["Alert", "run_checks", "apply_alerts", "check_records", "check_runs", "check_registry", "check_licenses", "check_drift_and_performance", "MIN_N_FOR_CLAIM"]
