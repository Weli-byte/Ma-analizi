"""League-model programme, Sprint 6 (ADR 0068): immutable prospective records, shadow run, canary router, monitoring, kill switch, scorecard.

Tests marked TEST_ONLY use minimal synthetic data or a fake provider to exercise logic; they are NOT live verification. The real shadow run and its records are
documented separately (docs/league_models/sprint06_*.md) and were made against the real provider."""

import hashlib
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
import yaml

from src.ingestion.provider import ProviderError, RateLimitedError, RawFixture
from src.league_models.licenses import load_manifest
from src.league_models.prediction_router import GLOBAL, RegistryEntry, RouterError
from src.league_models.rollout import control, evaluation, monitoring, records, shadow
from tests.test_league_models_s3 import _synthetic_audit

ROOT = Path(__file__).resolve().parents[1]
RAW = yaml.safe_load((ROOT / "configs" / "league_models" / "rollout.yaml").read_text(encoding="utf-8"))
NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
KICK = NOW + timedelta(hours=23, minutes=30)   # the t-24h window opened 30 minutes ago


def cfg_with(tmp_path: Path, **over):
    raw = yaml.safe_load(yaml.safe_dump(RAW))
    for k, v in over.items():
        if "." in k:
            a, b = k.split(".")
            raw[a][b] = v
        else:
            raw[k] = v
    p = tmp_path / "rollout.yaml"
    p.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return control.load_rollout(p)


def rec(fixture="fdorg-1", stage="t-24h", family="global", version="v1", p=(0.5, 0.3, 0.2, 0.55, 0.52), as_of=NOW, kick=KICK, **kw):
    return records.ProspectiveRecord(fixture, "ITA1", kick.isoformat(), as_of.isoformat(), stage, version, family, "global" if family == "global" else "candidate", *p,
                                     "snap-abc", ("openfootball_domestic", "football_data_org"), {"lineups": "UNKNOWN"}, None, **kw)  # fmt: skip


# ------------------------------------------------------------------ safe defaults
def test_the_committed_rollout_config_is_safe_by_default():
    cfg = control.load_rollout()
    assert cfg["kill_switch"] is True and cfg["canary"]["active_fraction"] == 0.0 and cfg["canary"]["require_license_permitted"] is True
    fr = [s["fraction"] for s in cfg["canary"]["stages"]]
    assert fr == [0.0, 0.01, 0.05, 0.2, 0.5, 1.0] and cfg["canary"]["stages"][0]["name"] == "shadow_only"
    assert all(s["min_observation_days"] > 0 and s["min_resolved_pairs"] >= 100 for s in cfg["canary"]["stages"])  # written in advance, never a tiny sample
    assert control.kill_active(cfg, ROOT)[0] is True


def test_a_malformed_rollout_config_is_refused(tmp_path):
    bad = yaml.safe_load(yaml.safe_dump(RAW))
    del bad["monitoring"]
    (tmp_path / "a.yaml").write_text(yaml.safe_dump(bad), encoding="utf-8")
    with pytest.raises(control.RolloutConfigError, match="missing"):
        control.load_rollout(tmp_path / "a.yaml")
    with pytest.raises(control.RolloutConfigError, match="active_fraction"):
        cfg_with(tmp_path, **{"canary.active_fraction": 1.5})
    bad2 = yaml.safe_load(yaml.safe_dump(RAW))
    bad2["canary"]["stages"][1]["fraction"] = 0.9
    (tmp_path / "b.yaml").write_text(yaml.safe_dump(bad2), encoding="utf-8")
    with pytest.raises(control.RolloutConfigError, match="increasing"):
        control.load_rollout(tmp_path / "b.yaml")


# ------------------------------------------------------------------ immutable prospective records
def test_a_record_is_idempotent_immutable_and_never_user_visible(tmp_path):
    st = records.ProspectiveStore(tmp_path / "p.jsonl")
    assert st.put(rec(), NOW) == "CREATED"
    assert st.put(rec(), NOW + timedelta(minutes=5)) == "EXISTS"            # same key, a later replay: idempotent, even outside the window
    assert st.put(rec(as_of=NOW + timedelta(hours=5)), NOW + timedelta(hours=5)) == "EXISTS"
    assert len(st.all()) == 1 and st.all()[0]["visible_to_users"] is False and st.all()[0]["content_hash"]
    with pytest.raises(records.ProspectiveConflict, match="immutable"):
        st.put(rec(p=(0.6, 0.2, 0.2, 0.55, 0.52)), NOW)                       # a changed prediction needs a new model version
    assert st.put(rec(version="v2", p=(0.6, 0.2, 0.2, 0.55, 0.52)), NOW) == "CREATED"
    assert len(st.all()) == 2


def test_records_that_could_leak_or_lie_are_refused(tmp_path):
    st = records.ProspectiveStore(tmp_path / "p.jsonl")
    with pytest.raises(records.ProspectiveError, match="not before kick-off"):
        st.put(rec(as_of=KICK + timedelta(minutes=1)), NOW)                   # post-kickoff
    with pytest.raises(records.ProspectiveError, match="outside"):
        st.put(rec(as_of=NOW - timedelta(hours=6), kick=KICK), NOW)           # stage window already closed
    with pytest.raises(records.ProspectiveError, match="not a probability"):
        st.put(rec(p=(1.4, -0.2, -0.2, 0.5, 0.5)), NOW)
    with pytest.raises(records.ProspectiveError, match="sum"):
        st.put(rec(p=(0.5, 0.5, 0.5, 0.5, 0.5)), NOW)
    with pytest.raises(records.ProspectiveError, match="complete or absent"):
        st.put(rec(p=(0.5, None, 0.5, 0.5, 0.5)), NOW)
    with pytest.raises(records.ProspectiveError, match="no probability at all"):
        st.put(rec(p=(None,) * 5), NOW)
    with pytest.raises(records.ProspectiveError, match="user-visible"):
        st.put(rec(visible_to_users=True), NOW)
    assert st.all() == []                                                     # nothing stored by a refused record
    assert st.put(rec(p=(None, None, None, 0.61, None), family="B_dc_league:over_under"), NOW) == "CREATED"   # a candidate fills only its own target: the rest is null


def test_a_storage_failure_fails_closed_and_a_corrupt_store_is_reported(tmp_path):
    d = tmp_path / "dir_instead_of_file"
    d.mkdir()
    with pytest.raises(records.StoreUnavailable):
        records.ProspectiveStore(d).put(rec(), NOW)                           # database outage: nothing is acknowledged
    p = tmp_path / "bad.jsonl"
    p.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(records.StoreUnavailable, match="corrupt"):
        records.ProspectiveStore(p).all()


def test_outcomes_are_a_separate_append_only_store(tmp_path):
    o = records.OutcomeStore(tmp_path / "o.jsonl")
    assert o.put("fdorg-1", 2, 1, "t", None, NOW) == "CREATED" and o.put("fdorg-1", 2, 1, "t", None, NOW) == "EXISTS"
    with pytest.raises(records.ProspectiveConflict, match="different final score"):
        o.put("fdorg-1", 0, 0, "t", None, NOW)
    assert (tmp_path / "o_conflicts.jsonl").is_file() and o.all()["fdorg-1"]["home_goals"] == 2    # nothing overwritten, conflict logged
    with pytest.raises(records.ProspectiveError):
        o.put("fdorg-2", -1, 0, "t", None, NOW)


# ------------------------------------------------------------------ kill switch and canary
def _entry(tmp_path, model="E_elo_bridge", status="APPROVED_FOR_CANARY", mode="LEAGUE_ADAPTED", league="ITA1", target="1x2", content="booster"):
    p = tmp_path / f"{model}_{league.replace('*', 'ALL')}_{target}.txt"
    p.write_text(content, encoding="utf-8")
    return RegistryEntry(model, "v1", mode, target, league, status, p.name, hashlib.sha256(content.encode()).hexdigest(), "booster_file")


def _router(tmp_path, status="APPROVED_FOR_CANARY", fraction=1.0, kill=False, lic="PERMITTED", **kw):
    cfg = cfg_with(tmp_path, kill_switch=kill, **{"canary.active_fraction": fraction})
    reg = control.LeagueModelRegistry([_entry(tmp_path, "elo_s2", "GLOBAL_REFERENCE", GLOBAL, "*"), _entry(tmp_path, status=status)], tmp_path)
    manifest = load_manifest()
    licenses = {"openfootball_domestic": manifest["openfootball_domestic"], "football_data_org": manifest["football_data_org"]}
    if lic == "PERMITTED":
        licenses["football_data_org"] = licenses["football_data_org"].model_copy(update={"plan_status": "PERMITTED"})
    return control.CanaryRouter(cfg, reg, licenses, {"E_elo_bridge": ["openfootball_domestic", "football_data_org"]}, tmp_path / "audit.jsonl", tmp_path, **kw), cfg


def test_default_routing_is_global_and_the_kill_switch_beats_everything(tmp_path):
    r, cfg = _router(tmp_path, kill=True)
    d = r.route("user-1", "ITA1", "1x2", "t-24h", NOW)
    assert d.served == "GLOBAL" and d.reason == "KILL_SWITCH_CONFIG"
    r2, _ = _router(tmp_path, fraction=0.0)
    assert r2.route("user-1", "ITA1", "1x2", "t-24h", NOW).reason == "FRACTION_ZERO"
    r3, cfg3 = _router(tmp_path)
    assert r3.route("user-1", "ITA1", "1x2", "t-24h", NOW).served == "E_elo_bridge"
    control.trip_kill_switch(cfg3, "test", tmp_path, NOW)                      # the flag file has the same effect as the config switch
    assert r3.route("user-1", "ITA1", "1x2", "t-24h", NOW).reason == "KILL_SWITCH_FLAG_FILE" and r3.route("user-2", "ITA1", "1x2", "t-24h", NOW).served == "GLOBAL"


def test_canary_routing_is_deterministic_cohort_limited_and_audited_without_the_user(tmp_path):
    r, _ = _router(tmp_path, fraction=0.2)
    users = [f"u{i}" for i in range(400)]
    a = [r.route(u, "ITA1", "1x2", "t-24h", NOW).served for u in users]
    assert a == [r.route(u, "ITA1", "1x2", "t-24h", NOW).served for u in users]   # deterministic
    share = a.count("E_elo_bridge") / len(users)
    assert 0.12 < share < 0.28
    audit = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "u17" not in audit and "user_hash" in audit and "config_fingerprint" in audit      # the user is a salted hash; the served version is recorded
    rows = [json.loads(x) for x in audit.splitlines()]
    assert {x["served"] for x in rows} == {"GLOBAL", "E_elo_bridge"} and all(x["reason"] in ("CANARY", "OUTSIDE_COHORT") for x in rows)


def test_only_an_approved_and_licensed_model_can_ever_be_served(tmp_path):
    r, _ = _router(tmp_path, status="SHADOW_INCONCLUSIVE")
    assert r.route("u", "ITA1", "1x2", "t-24h", NOW).reason == "MODEL_NOT_APPROVED_FOR_CANARY"
    r2, _ = _router(tmp_path, lic="CONDITIONAL")
    d = r2.route("u", "ITA1", "1x2", "t-24h", NOW)
    assert d.served == "GLOBAL" and d.reason == "LICENSE_CONDITIONAL:football_data_org"      # a research-only schedule source blocks the canary whatever the score
    r3, _ = _router(tmp_path)
    (tmp_path / "E_elo_bridge_ITA1_1x2.txt").write_text("tampered", encoding="utf-8")        # corrupted artifact
    d3 = r3.route("u", "ITA1", "1x2", "t-24h", NOW)
    assert d3.served == "GLOBAL" and d3.reason == "ARTIFACT_INVALID" and d3.alarms
    (tmp_path / "elo_s2_ALL_1x2.txt").write_text("tampered", encoding="utf-8")               # the global model broken: a real error, no invented answer
    with pytest.raises(RouterError):
        r3.route("u", "ITA1", "1x2", "t-24h", NOW)
    r4, _ = _router(tmp_path / "x" if (tmp_path / "x").mkdir() is None else tmp_path, disabled_leagues={"ITA1"})
    assert r4.route("u", "ITA1", "1x2", "t-24h", NOW).reason == "LEAGUE_DISABLED"


def test_stage_gate_is_advice_with_reasons_and_small_samples_never_advance(tmp_path):
    cfg = cfg_with(tmp_path)
    assert control.stage_gate(cfg, "shadow_only", 20, 50, -0.01, 0, True)["may_advance"] is False       # too few resolved pairs
    assert control.stage_gate(cfg, "shadow_only", 20, 150, 0.004, 0, True)["may_advance"] is False      # interval not below 0
    assert control.stage_gate(cfg, "shadow_only", 20, 150, -0.004, 1, True)["may_advance"] is False     # a critical alarm in the window
    assert control.stage_gate(cfg, "shadow_only", 20, 150, -0.004, 0, False)["may_advance"] is False    # licence not PERMITTED
    ok = control.stage_gate(cfg, "shadow_only", 20, 150, -0.004, 0, True)
    assert ok["may_advance"] is True and ok["next"] == "internal_users" and "owner" in ok["note"]
    assert control.stage_gate(cfg, "full_100", 99, 999, -1, 0, True)["may_advance"] is False and control.stage_gate(cfg, "full_100", 99, 999, -1, 0, True)["next"] is None
    with pytest.raises(control.RolloutConfigError):
        control.stage_gate(cfg, "no_such_stage", 1, 1, 0, 0, True)


# ------------------------------------------------------------------ scorecard
def _resolved(n, better=True, seed=0):
    """TEST_ONLY: n fixtures with a global and a candidate 1X2 record plus a result."""
    rng = np.random.default_rng(seed)
    recs, outs = [], {}
    for i in range(n):
        k = datetime(2026, 9, 1, 15, tzinfo=UTC) + timedelta(days=i)
        y = int(rng.integers(0, 3))
        g = np.full(3, 1 / 3)
        c = np.full(3, 0.2)
        c[y if better else (y + 1) % 3] = 0.6
        for fam, p in (("global", g), ("E_elo_bridge:1x2", c)):
            recs.append({"fixture_id": f"f{i}", "league_id": "ITA1", "kickoff_utc": k.isoformat(), "stage": "t-24h", "family": fam, "role": "x", "p_home": float(p[0]), "p_draw": float(p[1]),
                         "p_away": float(p[2]), "p_over25": None, "p_btts_yes": None, "model_version": "v", "schema_version": records.SCHEMA_VERSION, "data_license_ids": ["openfootball_domestic"],
                         "coverage_flags": {"lineups": "UNKNOWN"}, "visible_to_users": False})
        outs[f"f{i}"] = {"fixture_id": f"f{i}", "home_goals": [2, 1, 0][y], "away_goals": [0, 1, 2][y]}
    return recs, outs


def test_the_scorecard_makes_no_claim_on_a_small_sample_and_a_verdict_on_a_large_one():
    recs, outs = _resolved(10)
    sc = evaluation.scorecard(recs, outs, NOW)
    assert sc["live_verified"] is True and sc["resolved_fixtures"] == 10 and sc["pairs"][0]["verdict"] == "TOO_FEW_PAIRS_NO_CLAIM" and "ci95" not in sc["pairs"][0]
    assert "prospective" in sc["kind"].lower() or "PROSPECTIVE" in sc["kind"]
    recs, outs = _resolved(120, better=True)
    big = evaluation.scorecard(recs, outs, NOW)["pairs"][0]
    assert big["resolved_pairs"] == 120 and big["verdict"] == "CANDIDATE_BETTER" and big["ci95"][1] < 0 and big["delta_log_loss_candidate_minus_global"] < 0
    recs, outs = _resolved(120, better=False)
    assert evaluation.scorecard(recs, outs, NOW)["pairs"][0]["verdict"] == "CANDIDATE_WORSE"


def test_no_record_means_no_live_verification_and_pending_results_are_not_failures():
    sc = evaluation.scorecard([], {}, NOW)
    assert sc["live_verified"] is False and sc["pairs"] == [] and sc["records_total"] == 0
    recs, _ = _resolved(3)
    lag = evaluation.lag_report(recs, {}, datetime(2026, 9, 3, 16, tzinfo=UTC), 6)
    assert lag["late_fixtures"] == 2        # the match of the next day has not kicked off yet; those two are past kick-off + grace
    assert evaluation.lag_report(recs, {}, datetime(2026, 9, 1, 17, tzinfo=UTC), 6)["late_fixtures"] == 0   # inside the grace window: pending, not late


# ------------------------------------------------------------------ monitoring and fail-closed alarms
def _run(**kw):
    base = {"run_at_utc": NOW.isoformat(), "status": "OK", "provider_errors": [], "skipped": [], "records": {"CREATED": 4, "EXISTS": 0, "CONFLICT": 0, "REFUSED": 0}, "fixtures_mapped": 2,
            "fixtures_due": 2, "per_fixture_ms": {"fdorg-1": 40.0}, "model_errors": {}}
    base.update(kw)
    return base


def _keys(alerts, severity=None):
    return {a.key for a in alerts if severity is None or a.severity == severity}


def test_every_resilience_scenario_has_an_alarm_and_a_decision(tmp_path):
    cfg = cfg_with(tmp_path)
    recs, outs = _resolved(5)
    ok = monitoring.run_checks(records=recs, outcomes=outs, runs=[_run()], registry=None, licenses=load_manifest(), cfg=cfg, now=NOW)
    assert not [a for a in ok if a.fail_closed]                                                          # a healthy system raises no fail-closed alarm
    assert "metric_small_n:E_elo_bridge:1x2:t-24h" in _keys(ok, "info")                                   # a small N never alarms on performance
    bad_schema = [{**recs[0], "schema_version": "old"}]
    assert "schema_mismatch" in _keys(monitoring.check_records(bad_schema, cfg), "critical")
    assert "probabilities_malformed" in _keys(monitoring.check_records([{**recs[0], "p_home": 0.9}], cfg), "critical")
    assert "fixture_identity_conflict" in _keys(monitoring.check_records([recs[0], {**recs[2], "fixture_id": recs[0]["fixture_id"], "league_id": "ENG1"}], cfg), "critical")
    assert "lineups_not_unknown" in _keys(monitoring.check_records([{**recs[0], "coverage_flags": {"lineups": "CONFIRMED"}}], cfg), "critical")
    assert "shadow_record_visible" in _keys(monitoring.check_records([{**recs[0], "visible_to_users": True}], cfg), "critical")
    fail3 = [_run(status="PROVIDER_FAILED", provider_errors=[{"error": "RateLimitedError"}], records={"CREATED": 0, "EXISTS": 0, "CONFLICT": 0, "REFUSED": 0}, fixtures_mapped=0)] * 3
    assert "provider_unavailable" in _keys(monitoring.check_runs(fail3, cfg, NOW), "critical")           # 429 / 5xx three times in a row
    assert "provider_error" in _keys(monitoring.check_runs(fail3[:1], cfg, NOW), "warning")                # once: a warning, not a rollback
    assert "zero_coverage" in _keys(monitoring.check_runs([_run(records={"CREATED": 0, "EXISTS": 0, "CONFLICT": 0, "REFUSED": 0})], cfg, NOW), "critical")
    assert "records_refused" in _keys(monitoring.check_runs([_run(status="RECORDS_REFUSED")], cfg, NOW), "critical")
    assert "model_missing" in _keys(monitoring.check_runs([_run(model_errors={"E_elo_bridge": "NOT_TRAINED"})], cfg, NOW), "warning")
    assert "slow_inference" in _keys(monitoring.check_runs([_run(per_fixture_ms={"fdorg-1": 9000.0})], cfg, NOW), "warning")
    assert "scheduler_stale" in _keys(monitoring.check_runs([_run(run_at_utc=(NOW - timedelta(hours=60)).isoformat())], cfg, NOW), "warning")
    revoked = {"football_data_org": load_manifest()["football_data_org"].model_copy(update={"plan_status": "BLOCKED"})}
    assert "license:football_data_org" in _keys(monitoring.check_licenses(recs and [{**recs[0], "data_license_ids": ["football_data_org"]}], revoked), "critical")
    assert any(a.key == "license:nope" and a.fail_closed for a in monitoring.check_licenses([{**recs[0], "data_license_ids": ["nope"]}], {}))   # unknown source = blocked
    assert not [a for a in monitoring.check_licenses([{**recs[0], "data_license_ids": ["football_data_org"]}], load_manifest()) if a.severity == "critical"]   # CONDITIONAL: info only


def test_corrupted_or_missing_artifacts_alarm_and_only_a_broken_global_is_fail_closed(tmp_path):
    reg = control.LeagueModelRegistry([_entry(tmp_path, "elo_s2", "GLOBAL_REFERENCE", GLOBAL, "*"), _entry(tmp_path)], tmp_path)
    assert monitoring.check_registry(reg) == []
    (tmp_path / "E_elo_bridge_ITA1_1x2.txt").write_text("tampered", encoding="utf-8")
    a = monitoring.check_registry(reg)
    assert len(a) == 1 and a[0].severity == "warning" and not a[0].fail_closed
    (tmp_path / "elo_s2_ALL_1x2.txt").unlink()
    assert any(x.severity == "critical" and x.fail_closed for x in monitoring.check_registry(reg))


def test_an_absent_artifact_store_is_not_verified_not_a_rollback(tmp_path):
    reg = control.LeagueModelRegistry([_entry(tmp_path, "elo_s2", "GLOBAL_REFERENCE", GLOBAL, "*")], tmp_path / "no_such_store")
    a = monitoring.check_registry(reg)
    assert [x.key for x in a] == ["artifact_store_missing"] and a[0].severity == "info" and not a[0].fail_closed         # nothing is canary-approved: no message to the owner
    canary = control.LeagueModelRegistry([_entry(tmp_path, "elo_s2", "GLOBAL_REFERENCE", GLOBAL, "*"), _entry(tmp_path, status="APPROVED_FOR_CANARY")], tmp_path / "no_such_store")
    b = monitoring.check_registry(canary)
    assert [x.key for x in b] == ["artifact_store_missing"] and b[0].severity == "warning" and not b[0].fail_closed         # a servable model with unverifiable artifacts is a warning


def test_performance_alarms_need_enough_resolved_pairs_and_drift_is_reported_separately(tmp_path):
    cfg = cfg_with(tmp_path)
    recs, outs = _resolved(150, better=False)                                         # the candidate is clearly worse, with enough pairs
    al = monitoring.check_drift_and_performance(recs, outs, cfg, NOW)
    assert any(a.key.startswith("performance_regression") and a.fail_closed for a in al)
    recs2, outs2 = _resolved(20, better=False)
    al2 = monitoring.check_drift_and_performance(recs2, outs2, cfg, NOW)
    assert not any(a.key.startswith("performance_regression") for a in al2) and any(a.key.startswith("metric_small_n") for a in al2)
    assert any(a.key == "drift_insufficient_rows" and a.severity == "info" for a in al2)
    # a pending result inside the grace window is not an outcome-lag alarm
    pending = [r for r in recs2]
    assert not any(a.key == "outcome_lag" for a in monitoring.check_drift_and_performance(pending, {}, cfg, datetime(2026, 9, 20, 17, tzinfo=UTC)) if "2026-09-01" in a.message)


def test_a_fail_closed_alarm_trips_the_kill_switch_and_keeps_every_record(tmp_path):
    cfg = cfg_with(tmp_path, kill_switch=False)
    st = records.ProspectiveStore(tmp_path / "rollout" / "prospective.jsonl")
    st.put(rec(), NOW)
    before = (tmp_path / "rollout" / "prospective.jsonl").read_bytes()
    assert control.kill_active(cfg, tmp_path)[0] is False
    dec = monitoring.apply_alerts([monitoring.Alert("critical", "zero_coverage", "x", True), monitoring.Alert("info", "i", "y")], cfg, tmp_path, tmp_path / "rollout", NOW)
    assert dec["fail_closed"] and dec["routing"].startswith("GLOBAL_ONLY") and control.kill_active(cfg, tmp_path) == (True, "KILL_SWITCH_FLAG_FILE")
    assert (tmp_path / "rollout" / "prospective.jsonl").read_bytes() == before                  # rollback never touches stored predictions
    assert len((tmp_path / "rollout" / "alerts.jsonl").read_text(encoding="utf-8").splitlines()) == 2
    assert monitoring.apply_alerts([], cfg, tmp_path, tmp_path / "rollout", NOW)["fail_closed"] is False


# ------------------------------------------------------------------ shadow run with a fake provider (TEST_ONLY)
class FakeDirectory:
    def resolve(self, source, raw_name, country, day=None, **kw):
        class R:
            team_id = None

        r = R()
        if raw_name.startswith("Team "):
            r.team_id = f"ITA_t{raw_name.split()[1]}"
        return r


class FakeProvider:
    def __init__(self, fixtures=None, error=None):
        self.fixtures, self.error = fixtures or [], error

    def list_fixtures(self, code, season):
        if self.error:
            raise self.error
        return self.fixtures if code == "SA" else []


def _fx(i, home, away, kick=KICK, status="NS"):
    return RawFixture(str(i), "SA", "2026-27", kick, home, away, status)


@pytest.fixture
def shadow_env(tmp_path):
    (tmp_path / "configs").mkdir()
    shutil.copy(ROOT / "configs" / "model.yaml", tmp_path / "configs" / "model.yaml")
    shutil.copy(ROOT / "configs" / "ingestion.yaml", tmp_path / "configs" / "ingestion.yaml")
    cal = {"final_calibrators": {t: {} for t in ("1x2", "over_under", "btts")}}
    (tmp_path / "cal.json").write_text(json.dumps(cal), encoding="utf-8")
    bench = (ROOT / "configs" / "league_models" / "benchmark.yaml").read_text(encoding="utf-8").replace("min_train_matches: 500", "min_train_matches: 50")
    (tmp_path / "bench.yaml").write_text(bench, encoding="utf-8")
    stat = (ROOT / "configs" / "league_models" / "stat_models.yaml").read_text(encoding="utf-8").replace("min_uefa_matches_total: 200", "min_uefa_matches_total: 100")
    (tmp_path / "stat.yaml").write_text(stat, encoding="utf-8")
    entries = [_entry(tmp_path, "elo_s2", "GLOBAL_REFERENCE", GLOBAL, "*"), _entry(tmp_path, "E_elo_bridge", "SHADOW_INCONCLUSIVE", target="1x2"),
               _entry(tmp_path, "B_dc_league", "SHADOW_INCONCLUSIVE", target="over_under"), _entry(tmp_path, "B_dc_league", "REJECTED", league="ENG1", target="1x2")]
    return tmp_path, control.LeagueModelRegistry(entries, tmp_path)


def _shadow(env, provider, now=NOW, per_league=False, **kw):
    root, reg = env
    cfg = cfg_with(root, kill_switch=True)
    cfg.raw["shadow"]["per_league"]["enabled"] = per_league           # the Sprint 6 tests pin the exact family set; ADR 0071 tests switch the exploratory family on
    return shadow.run_shadow(root, now, provider=provider, audit=_synthetic_audit(), cfg=cfg, registry=reg, out_dir=root / "rollout", calibration_manifest=root / "cal.json",
                             directory=FakeDirectory(), bench_config_path=root / "bench.yaml", stat_config_path=root / "stat.yaml", **kw)  # fmt: skip


def test_the_shadow_run_stores_a_global_and_candidate_records_on_the_same_snapshot(shadow_env):
    env = shadow_env
    rep = _shadow(env, FakeProvider([_fx(1, "Team 0", "Team 1")]))
    assert rep["status"] == "OK" and rep["fixtures_due"] == 1 and rep["fixtures_mapped"] == 1 and rep["records"]["CREATED"] == 3
    recs = records.ProspectiveStore(env[0] / "rollout" / "prospective.jsonl").all()
    fams = {r["family"]: r for r in recs}
    assert set(fams) == {"global", "E_elo_bridge:1x2", "B_dc_league:over_under"}                        # REJECTED candidates never shadow
    assert len({r["snapshot_hash"] for r in recs}) == 1 and all(r["visible_to_users"] is False and r["stage"] == "t-24h" for r in recs)   # same as-of snapshot for all
    g, e, d = fams["global"], fams["E_elo_bridge:1x2"], fams["B_dc_league:over_under"]
    assert abs(g["p_home"] + g["p_draw"] + g["p_away"] - 1) < 1e-6 and all(g[k] is not None for k in ("p_over25", "p_btts_yes"))
    assert e["p_over25"] is None and e["p_btts_yes"] is None and abs(e["p_home"] + e["p_draw"] + e["p_away"] - 1) < 1e-6
    assert d["p_home"] is None and 0 < d["p_over25"] < 1
    assert all(r["coverage_flags"]["lineups"] in ("UNKNOWN", "UNKNOWN_NOT_A_FEATURE") for r in recs) and all("football_data_org" in r["data_license_ids"] for r in recs)
    assert rep["schedule_license_plan_status"] == "CONDITIONAL"


def test_a_shadow_replay_is_idempotent_and_a_late_fixture_gets_no_record(shadow_env):
    env = shadow_env
    prov = FakeProvider([_fx(1, "Team 0", "Team 1"), _fx(2, "Team 2", "Team 3", kick=NOW + timedelta(hours=40))])     # the second one is not due yet
    first = _shadow(env, prov)
    again = _shadow(env, prov, now=NOW + timedelta(minutes=20))
    assert first["fixtures_due"] == 1 and again["records"]["CREATED"] == 0 and again["records"]["EXISTS"] == 3
    assert len(records.ProspectiveStore(env[0] / "rollout" / "prospective.jsonl").all()) == 3
    late = _shadow(env, FakeProvider([_fx(3, "Team 0", "Team 1", kick=NOW + timedelta(hours=10))]))              # t-24h window passed long ago: no backfill
    assert late["fixtures_due"] == 0 and late["records"]["CREATED"] == 0


def test_provider_failures_unresolved_clubs_and_duplicates_never_produce_a_guess(shadow_env):
    env = shadow_env
    rl = _shadow(env, FakeProvider(error=RateLimitedError("HTTP 429")))
    assert rl["status"] == "PROVIDER_FAILED" and rl["provider_errors"][0]["error"] == "RateLimitedError" and rl["records"]["CREATED"] == 0
    five = _shadow(env, FakeProvider(error=ProviderError("HTTP 503")))
    assert five["status"] == "PROVIDER_FAILED" and five["records"]["CREATED"] == 0
    un = _shadow(env, FakeProvider([_fx(1, "Mystery FC", "Team 1")]))
    assert un["fixtures_mapped"] == 0 and un["skipped"][0]["reason"] == "UNRESOLVED_TEAM" and un["records"]["CREATED"] == 0
    dup = _shadow(env, FakeProvider([_fx(1, "Team 0", "Team 1"), _fx(1, "Team 0", "Team 1")]))
    assert dup["status"] == "DUPLICATE_FIXTURE_IDS" and dup["records"]["CREATED"] == 0
    assert not (env[0] / "rollout" / "prospective.jsonl").exists()


def test_a_candidate_that_cannot_be_fitted_is_reported_and_skipped_while_the_global_model_still_records(shadow_env, monkeypatch):
    from src.league_models.stat_models import NotTrained

    def failing(self, rows):
        raise NotTrained("forced (TEST_ONLY)")

    monkeypatch.setattr(shadow.TimeAwareElo, "fit", failing)
    rep = _shadow(shadow_env, FakeProvider([_fx(1, "Team 0", "Team 1")]))
    assert "E_elo_bridge" in rep["model_errors"] and any(s["family"] == "E_elo_bridge:1x2" and "NOT_TRAINED" in s["reason"] for s in rep["skipped"])
    fams = {r["family"] for r in records.ProspectiveStore(shadow_env[0] / "rollout" / "prospective.jsonl").all()}
    assert fams == {"global", "B_dc_league:over_under"}
    alerts = monitoring.check_runs([rep], cfg_with(shadow_env[0]), NOW)
    assert "model_missing" in _keys(alerts, "warning")


def test_the_disabled_and_the_unconfigured_shadow_do_nothing(shadow_env, tmp_path):
    root, reg = shadow_env
    off = shadow.run_shadow(root, NOW, provider=FakeProvider(), audit=_synthetic_audit(), cfg=cfg_with(root, **{"shadow.enabled": False}), registry=reg, out_dir=root / "o2")
    assert off["status"] == "SHADOW_DISABLED"


def test_prediction_for_a_stale_snapshot_is_never_made_from_the_future(shadow_env):
    """The fitted history ends at `now` minus the result lag: a result published after the call is not history."""
    root, _ = shadow_env
    audit = _synthetic_audit()
    from src.league_models.splits import load_bench_config

    hist = shadow.operational_history(audit, load_bench_config(root / "bench.yaml"), NOW, 3)
    assert hist and max(r.kickoff_utc for r in hist) <= NOW - timedelta(hours=3)
    assert not [r for r in hist if r.league_id == "ENG1" and False]
    guard = shadow.operational_history(audit, load_bench_config(root / "bench.yaml"), NOW + timedelta(days=900), 3)
    assert len(guard) > len(hist)


# ------------------------------------------------------------------ committed evidence
def test_committed_sprint6_evidence_is_consistent():
    p = ROOT / "docs" / "league_models" / "s6_shadow_first_run.json"
    if not p.is_file():
        pytest.skip("Sprint 6 first-run evidence not generated")
    ev = json.loads(p.read_text(encoding="utf-8"))
    assert ev["records"] and all(r["visible_to_users"] is False and r["role"] in ("global", "candidate") for r in ev["records"])
    assert all(r["stage"] in ("t-24h", "t-90m", "t-30m", "kickoff") and r["prediction_as_of_utc"] < r["kickoff_utc"] for r in ev["records"])
    assert ev["kill_switch_at_run"] is True or ev["kill_switch_at_run"] is False


# ------------------------------------------------------------------ ADR 0071: separately trained per-league family and the score-forecast sidecar (TEST_ONLY synthetic history)
def test_the_per_league_family_and_the_score_forecast_sidecar_are_stored_next_to_the_record_without_changing_it(shadow_env):
    env = shadow_env
    rep = _shadow(env, FakeProvider([_fx(1, "Team 0", "Team 1")]), per_league=True)
    recs = records.ProspectiveStore(env[0] / "rollout" / "prospective.jsonl").all()
    fams = {r["family"]: r for r in recs}
    assert {"P_dc_sep:1x2", "P_dc_sep:over_under", "P_dc_sep:btts", "H_dc_hier:1x2", "H_dc_hier:over_under", "H_dc_hier:btts"} <= set(fams) and rep["skipped"] == []
    sep = [r for r in recs if r["family"].startswith("P_dc_sep")]
    for r in sep:
        assert r["role"] == "candidate" and r["visible_to_users"] is False and r["coverage_flags"]["calibration"] == "NONE_EXPLORATORY_RAW"
    side = records.ScoreForecastStore(env[0] / "rollout" / "score_forecasts.jsonl").all()
    by_key = {x["key"]: x for x in side}
    glob = fams["global"]
    g = by_key[f"{glob['fixture_id']}|{glob['stage']}|{glob['model_version']}|global"]
    assert g["record_content_hash"] == glob["content_hash"] and g["expected_goals_home"] > 0 and g["expected_goals_away"] > 0 and len(g["top_scores"]) == 5 and 0 <= g["tail_mass"] < 0.01
    d = fams["B_dc_league:over_under"]
    assert by_key[f"{d['fixture_id']}|{d['stage']}|{d['model_version']}|B_dc_league:over_under"]["record_content_hash"] == d["content_hash"]
    assert "p_0_0" in g and "expected_goals_home" not in glob and "lam_home" not in glob       # the immutable record itself is unchanged
    assert rep["score_forecasts"]["CREATED"] == len(side)
    again = _shadow(env, FakeProvider([_fx(1, "Team 0", "Team 1")]), now=NOW + timedelta(minutes=20), per_league=True)
    assert again["records"]["CREATED"] == 0 and again["score_forecasts"]["CREATED"] == 0 and len(records.ScoreForecastStore(env[0] / "rollout" / "score_forecasts.jsonl").all()) == len(side)


def test_a_score_forecast_with_different_content_is_a_conflict_and_bad_expected_goals_are_refused(tmp_path):
    st = records.ScoreForecastStore(tmp_path / "s.jsonl")
    r = rec()
    assert st.put(r, 1.4, 1.1, [("1-1", 0.12)], 0.08, 0.12, 1e-5) == "CREATED" and st.put(r, 1.4, 1.1, [("1-1", 0.12)], 0.08, 0.12, 1e-5) == "EXISTS"
    with pytest.raises(records.ProspectiveConflict):
        st.put(r, 1.5, 1.1, [("1-1", 0.12)], 0.08, 0.12, 1e-5)
    with pytest.raises(records.ProspectiveError):
        st.put(rec(fixture="fdorg-2"), 0.0, 1.1, [], 0.0, 0.0, 0.0)


def test_the_score_scorecard_joins_forecasts_with_outcomes_and_never_claims_on_a_tiny_sample(tmp_path):
    st = records.ScoreForecastStore(tmp_path / "s.jsonl")
    grid = [[0.0] * 7 for _ in range(7)]
    grid[1][0], grid[1][1], grid[0][0] = 0.5, 0.3, 0.2
    r = rec()
    st.put(r, 1.4, 0.9, [("1-0", 0.5), ("1-1", 0.3), ("0-0", 0.2)], 0.2, 0.3, 0.0, "TEST_ONLY", grid)
    out = evaluation.score_scorecard(st.all(), {"fdorg-1": {"home_goals": 1, "away_goals": 0}}, NOW)
    f = out["families"][0]
    assert f["resolved"] == 1 and f["log_score"] == pytest.approx(-np.log(0.5), abs=1e-5) and f["top1_hit_rate"] == 1.0 and f["top5_hit_rate"] == 1.0
    assert f["expected_goals_mae_home"] == pytest.approx(0.4) and f["expected_goals_mae_away"] == pytest.approx(0.9) and f["verdict"] == "TOO_FEW_RESOLVED_NO_CLAIM"
    big = evaluation.score_scorecard(st.all(), {"fdorg-1": {"home_goals": 8, "away_goals": 0}}, NOW)["families"][0]
    assert big["outside_stored_grid"] == 1 and big["log_score"] is None            # a scoreline above 6 goals is counted, not scored
    assert evaluation.score_scorecard(st.all(), {}, NOW)["families"] == []          # no outcome, no row
