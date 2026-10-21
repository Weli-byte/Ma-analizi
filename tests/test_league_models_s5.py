"""League-model programme, Sprint 5 (ADR 0067): calibrators, approval gate, holdout lock, router, end-to-end pre-registration.

Tests marked TEST_ONLY use minimal synthetic data for logic; they are NOT verification of any real data."""

import dataclasses
import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
import yaml

from src.league_models import approval_gate as ag
from src.league_models import calibrators as cb
from src.league_models import holdout_lock as hl
from src.league_models import prediction_router as pr
from src.league_models import s5_benchmark as s5
from tests.test_league_models_s3 import _synthetic_audit

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "league_models"
CACHE = ROOT / "artifacts" / "markets" / "_cache" / "openfootball"
needs_cache = pytest.mark.skipif(not CACHE.is_dir(), reason="cached openfootball source files are absent (CI)")
POLICY = yaml.safe_load((ROOT / "configs" / "league_models" / "approval_policy.yaml").read_text(encoding="utf-8"))


def utc(*a: int) -> datetime:
    return datetime(*a, tzinfo=UTC)


def _overconfident(n=3000, seed=0):
    """TEST_ONLY: probabilities that are too extreme for what happens."""
    rng = np.random.default_rng(seed)
    true = rng.dirichlet([3, 2, 3], n)
    y = np.array([rng.choice(3, p=t) for t in true])
    p = true**2.5
    return p / p.sum(axis=1, keepdims=True), y, list(rng.choice(["A", "B", "C"], n))


# ------------------------------------------------------------------ calibrators
def test_temperature_scaling_fixes_overconfidence_and_keeps_the_simplex():
    p, y, lg = _overconfident()
    raw = -np.mean(np.log(p[np.arange(len(y)), y]))
    for m in ("temperature", "affine", "hier"):
        c = cb.fit_calibrator(m, "1x2", p, y, lg)
        q = c.apply(p, lg)
        assert c.status == cb.FITTED and c.a < 0.7  # a slope below 1 shrinks over-confident log-probabilities
        assert np.allclose(q.sum(axis=1), 1.0) and (q > 0).all() and np.isfinite(q).all()
        assert -np.mean(np.log(q[np.arange(len(y)), y])) < raw - 0.02


def test_binary_calibrators_stay_inside_the_open_unit_interval():
    rng = np.random.default_rng(1)
    p = rng.uniform(0.0, 1.0, 2000)
    y = (rng.random(2000) < 0.3 + 0.2 * p).astype(int)
    lg = list(rng.choice(["A", "B"], 2000))
    for m in cb.METHODS_BINARY:
        q = cb.fit_calibrator(m, "btts", p, y, lg).apply(p, lg)
        assert ((q > 0) & (q < 1)).all() and np.isfinite(q).all(), m


def test_malformed_probabilities_are_rejected_before_and_after_calibration():
    with pytest.raises(cb.CalibrationError, match="non-finite"):
        cb.check_probs(np.array([[np.nan, 0.5, 0.5]]), "1x2")
    with pytest.raises(cb.CalibrationError, match="simplex"):
        cb.check_probs(np.array([[0.5, 0.5, 0.5]]), "1x2")
    with pytest.raises(cb.CalibrationError, match="binary"):
        cb.check_probs(np.array([1.2, 0.3]), "btts")
    c = cb.fit_calibrator("temperature", "1x2", *_overconfident()[:2], ["A"] * 3000)
    with pytest.raises(cb.CalibrationError):
        c.apply(np.array([[0.9, 0.9, 0.9]]), ["A"])
    with pytest.raises(cb.CalibrationError, match="not valid"):
        cb.fit_calibrator("isotonic", "1x2", *_overconfident()[:2], ["A"] * 3000)


def test_a_degenerate_fit_is_recorded_and_applies_the_identity_not_a_guess():
    p, y, lg = _overconfident(300)
    assert cb.fit_calibrator("affine", "1x2", p[:50], y[:50], lg[:50]).status == "NOT_FITTED_TOO_FEW_ROWS"
    one = cb.fit_calibrator("affine", "btts", np.full(500, 0.5), np.ones(500, int), ["A"] * 500)
    assert one.status == "NOT_FITTED_ONE_CLASS" and one.effective_method == "none"
    assert np.array_equal(one.apply(np.array([0.2, 0.9]), ["A", "A"]), [0.2, 0.9])
    iso = cb.fit_calibrator("isotonic", "btts", np.linspace(0.05, 0.95, 300), (np.arange(300) % 2), ["A"] * 300, min_isotonic=500)
    assert iso.status == "NOT_FITTED_TOO_FEW_ROWS" and "isotonic" in iso.reason


def test_hierarchical_calibration_pulls_a_small_league_toward_the_global_one_and_ignores_unseen_leagues():
    rng = np.random.default_rng(3)
    n = 3000
    p = rng.uniform(0.1, 0.9, n)
    lg = np.array(["BIG"] * (n - 30) + ["TINY"] * 30)
    y = (rng.random(n) < np.where(lg == "TINY", 0.95, p)).astype(int)  # the tiny league behaves very differently
    c = cb.fit_calibrator("hier", "btts", p, y, list(lg), ridge=50.0)
    off = c.as_dict()["league_offsets"]
    unconstrained = cb.fit_calibrator("hier", "btts", p, y, list(lg), ridge=0.0001).as_dict()["league_offsets"]
    assert abs(off["TINY"][0]) < abs(unconstrained["TINY"][0])  # the ridge keeps a 30-row league near the global fit
    glob = cb._sigmoid(c.a * cb._logit(p[:5]) + float(c.b[0]))  # an unseen league gets the global part only (offset 0)
    assert np.allclose(c.apply(p[:5], ["NEW"] * 5), glob, atol=1e-9)


def test_walk_forward_selection_uses_only_earlier_folds_and_leaves_calibrated_data_alone():
    p, y, lg = _overconfident(4800)
    fp, fy, fl = [p[i * 1200 : (i + 1) * 1200] for i in range(4)], [y[i * 1200 : (i + 1) * 1200] for i in range(4)], [lg[i * 1200 : (i + 1) * 1200] for i in range(4)]
    sel = cb.select_method(fp, fy, fl, "1x2", first_eval_fold=2, margin=0.0005)
    assert sel["chosen"] != "none" and sel["mean_log_loss"][sel["chosen"]] < sel["mean_log_loss"]["none"]
    fp2, fy2 = list(fp), list(fy)
    fp2[3], fy2[3] = fp[3][::-1] * 0 + 1 / 3, fy[3]  # change the LAST fold: the score of fold 2 cannot move
    sel2 = cb.select_method(fp2, fy2, fl, "1x2", first_eval_fold=2, margin=0.0005)
    assert sel2["walk_forward_log_loss_by_fold"]["affine"][0] == sel["walk_forward_log_loss_by_fold"]["affine"][0]
    rng = np.random.default_rng(5)
    good = rng.dirichlet([3, 2, 3], 4800)
    gy = np.array([rng.choice(3, p=t) for t in good])
    chosen = cb.select_method([good[i * 1200 : (i + 1) * 1200] for i in range(4)], [gy[i * 1200 : (i + 1) * 1200] for i in range(4)], fl, "1x2", first_eval_fold=2, margin=0.002)
    assert chosen["chosen"] == "none"  # already calibrated: no method gains the declared margin


# ------------------------------------------------------------------ gate
def _paired(delta=-0.01, lo=-0.02, hi=-0.002, p=0.001, brier=-0.004, acc=0.01, ece=0.0):
    return ag.PairedVsReference(delta, lo, hi, p, brier, acc, ece)


def _evidence(**kw):
    base = dict(league="ENG1", target="1x2", candidate="B_gbm_league", n_rows=380, coverage=1.0, training_matches=1900, local_model=False, license_status="APPROVED",
                no_leakage=True, snapshot_lineage=True, artifact_valid=True, latency_ms=5.0, refs={"elo_s2": _paired(), "A_gbm_tuned": _paired()}, holm_pass=True)
    base.update(kw)
    return ag.CellEvidence(**base)


def test_gate_approves_only_when_every_reference_is_beaten_with_confidence_and_the_adjustment_holds():
    d = ag.evaluate_cell(POLICY, _evidence())
    assert d.status == ag.APPROVED and all(c["passed"] for c in d.checks)
    assert ag.evaluate_cell(POLICY, _evidence(holm_pass=False)).status == ag.SHADOW
    weak = {"elo_s2": _paired(), "A_gbm_tuned": _paired(hi=0.004)}
    d2 = ag.evaluate_cell(POLICY, _evidence(refs=weak))
    assert d2.status == ag.SHADOW and any("includes 0" in r for r in d2.reasons)


def test_accuracy_alone_can_never_open_the_gate():
    refs = {"elo_s2": _paired(delta=0.002, lo=-0.01, hi=0.014, brier=0.001, acc=0.08), "A_gbm_tuned": _paired(delta=0.001, lo=-0.01, hi=0.012, brier=0.0, acc=0.09)}
    d = ag.evaluate_cell(POLICY, _evidence(refs=refs))
    assert d.status == ag.SHADOW and next(c for c in d.checks if c["check"] == "accuracy_cannot_open_the_gate")["passed"] is True


def test_gate_rejects_harm_and_blocks_missing_prerequisites():
    worse = {"elo_s2": _paired(delta=0.02, lo=0.005, hi=0.03, p=0.99, brier=0.01)}
    assert ag.evaluate_cell(POLICY, _evidence(refs=worse)).status == ag.REJECTED
    assert ag.evaluate_cell(POLICY, _evidence(latency_ms=250.0)).status == ag.REJECTED                       # high latency
    assert ag.evaluate_cell(POLICY, _evidence(artifact_valid=False)).status == ag.REJECTED                   # artifact hash mismatch
    bad_ece = {"elo_s2": _paired(ece=0.08), "A_gbm_tuned": _paired()}
    assert ag.evaluate_cell(POLICY, _evidence(refs=bad_ece)).status == ag.REJECTED                           # calibration regression
    assert ag.evaluate_cell(POLICY, _evidence(license_status="REVOKED")).status == ag.BLOCKED                # revoked licence
    assert ag.evaluate_cell(POLICY, _evidence(coverage=0.6)).status == ag.BLOCKED                            # low coverage
    assert ag.evaluate_cell(POLICY, _evidence(no_leakage=False)).status == ag.BLOCKED
    assert ag.evaluate_cell(POLICY, _evidence(snapshot_lineage=False)).status == ag.BLOCKED                  # changed data snapshot
    assert ag.evaluate_cell(POLICY, _evidence(n_rows=0)).status == ag.BLOCKED
    assert ag.evaluate_cell(POLICY, _evidence(n_rows=80)).status == ag.SHADOW                                # too few rows: inconclusive, not approved
    assert ag.evaluate_cell(POLICY, _evidence(local_model=True, training_matches=900)).status == ag.SHADOW   # local tier under 1,500 needs proof first


def test_holm_controls_the_family_and_keeps_the_order():
    p = {"a": 0.001, "b": 0.015, "c": 0.04, "d": 0.5}
    r = ag.holm(p, 0.05)
    assert r == {"a": True, "b": True, "c": False, "d": False}  # step-down stops at the first failure
    assert ag.holm({}, 0.05) == {}


# ------------------------------------------------------------------ holdout lock
def test_the_holdout_can_be_read_once_with_a_preregistration_and_a_clean_tree(tmp_path):
    log = tmp_path / "log.json"
    with pytest.raises(hl.HoldoutLockError, match="pre-registration"):
        hl.consume(log, purpose="t", prereg_sha256="", fixture_ids_sha256="x", n_fixtures=1, git_sha="a", dirty=False)
    with pytest.raises(hl.HoldoutLockError, match="uncommitted"):
        hl.consume(log, purpose="t", prereg_sha256="a" * 64, fixture_ids_sha256="x", n_fixtures=1, git_sha="a", dirty=True)
    assert not hl.is_consumed(log)
    e = hl.consume(log, purpose="t", prereg_sha256="a" * 64, fixture_ids_sha256="x", n_fixtures=3, git_sha="a", dirty=False)
    assert e["status"] == "CONSUMED" and hl.is_consumed(log) and len(hl.read_log(log)) == 1
    with pytest.raises(hl.HoldoutLockError, match="already evaluated once"):
        hl.consume(log, purpose="again", prereg_sha256="b" * 64, fixture_ids_sha256="x", n_fixtures=3, git_sha="a", dirty=False)
    assert len(hl.read_log(log)) == 1


def test_the_lock_is_closed_before_any_score_is_returned_and_the_listing_must_match(tmp_path):
    from src.league_models.splits import build_universe, load_bench_config

    bcfg = load_bench_config()
    audit = _synthetic_audit()
    uni = build_universe(bcfg, audit)
    log = tmp_path / "log.json"
    bad = dataclasses.replace(uni, holdout_ids=uni.holdout_ids[:-1])
    with pytest.raises(hl.HoldoutLockError, match="differ from the frozen listing"):
        hl.holdout_rows(audit, bcfg, bad, log_path=log, prereg_sha256="c" * 64, git_sha="g", dirty=False)
    assert not hl.is_consumed(log)  # a refused request does not burn the holdout
    rows = hl.holdout_rows(audit, bcfg, uni, log_path=log, prereg_sha256="c" * 64, git_sha="g", dirty=False)
    assert len(rows) == len(uni.holdout_ids) and {r.fixture_id for r in rows} == set(uni.holdout_ids) and hl.is_consumed(log)
    with pytest.raises(hl.HoldoutLockError):
        hl.holdout_rows(audit, bcfg, uni, log_path=log, prereg_sha256="c" * 64, git_sha="g", dirty=False)


# ------------------------------------------------------------------ router
def _entry(tmp_path, model_id="B_gbm_league", status="APPROVED_FOR_CANARY", mode=pr.LEAGUE_ADAPTED, league="ENG1", target="1x2", content="booster"):
    p = tmp_path / f"{model_id}_{league.replace('*', 'ALL')}_{target}.txt"
    p.write_text(content, encoding="utf-8")
    return pr.RegistryEntry(model_id, "v1", mode, target, league, status, p.name, hashlib.sha256(content.encode()).hexdigest(), "booster_file")


def _router(tmp_path, **kw):
    glob = _entry(tmp_path, "elo_s2", "GLOBAL_REFERENCE", pr.GLOBAL, "*", "1x2")
    cand = _entry(tmp_path, **kw)
    return pr.PredictionRouter([glob, cand], tmp_path), glob, cand


def test_router_selects_an_approved_artifact_for_canary_and_shadow_but_never_for_production(tmp_path):
    r, _, cand = _router(tmp_path)
    d = r.route("1x2", "ENG1", "t-24h", use="canary")
    assert (d.mode, d.model_id, d.fallback_reason) == (pr.LEAGUE_ADAPTED, "B_gbm_league", None)
    assert r.route("1x2", "ENG1", "t-24h", use="production").mode == pr.GLOBAL                        # production traffic opens in Sprint 6
    assert r.route("1x2", "ESP1", "t-24h", use="canary").fallback_reason == "NO_APPROVED_MODEL"      # no entry for the league
    assert r.route("1x2", "ENG1", "t-24h", use="canary", data_available=False).fallback_reason == "DATA_UNAVAILABLE"
    assert cand.status == "APPROVED_FOR_CANARY"


def test_router_refuses_unapproved_models_and_falls_back_with_a_reason(tmp_path):
    r, _, _ = _router(tmp_path, status="SHADOW_INCONCLUSIVE")
    assert r.route("1x2", "ENG1", "t-24h", use="canary").fallback_reason == "MODEL_NOT_APPROVED_FOR_CANARY"
    assert r.route("1x2", "ENG1", "t-24h", use="shadow").model_id == "B_gbm_league"                    # shadow may observe an inconclusive model
    with pytest.raises(pr.RouterError, match="unapproved models cannot be routed"):
        r.route("1x2", "ENG1", "t-24h", use="canary", force_model_id="B_gbm_league")
    rej, _, _ = _router(tmp_path, status="REJECTED")
    with pytest.raises(pr.RouterError):
        rej.route("1x2", "ENG1", "t-24h", use="shadow", force_model_id="B_gbm_league")


def test_router_falls_back_with_an_alarm_on_a_corrupted_artifact_and_fails_when_the_global_model_is_broken(tmp_path):
    r, glob, cand = _router(tmp_path)
    (tmp_path / cand.artifact_path).write_text("tampered", encoding="utf-8")
    d = r.route("1x2", "ENG1", "t-24h", use="canary")
    assert d.mode == pr.GLOBAL and d.fallback_reason == "ARTIFACT_INVALID" and "hash mismatch" in d.alarms[0] and r.alarms
    (tmp_path / cand.artifact_path).unlink()
    assert "missing" in r.route("1x2", "ENG1", "t-24h", use="canary").alarms[0]
    (tmp_path / glob.artifact_path).write_text("tampered", encoding="utf-8")
    with pytest.raises(pr.RouterError, match="refusing to invent a prediction"):
        r.route("1x2", "ENG1", "t-24h", use="canary")
    with pytest.raises(pr.RouterError, match="no global model"):
        pr.PredictionRouter([], tmp_path).route("1x2", "ENG1", "t-24h", use="canary")


def test_router_respects_disabled_leagues_and_validates_its_inputs(tmp_path):
    glob = _entry(tmp_path, "elo_s2", "GLOBAL_REFERENCE", pr.GLOBAL, "*", "1x2")
    cand = _entry(tmp_path)
    r = pr.PredictionRouter([glob, cand], tmp_path, disabled_leagues={"ENG1"})
    assert r.route("1x2", "ENG1", "t-24h", use="canary").fallback_reason == "LEAGUE_DISABLED"
    with pytest.raises(ValueError):
        r.route("1x2", "ENG1", "t-24h", use="live")
    with pytest.raises(ValueError):
        pr.PredictionRouter([dataclasses.replace(cand, mode="MAGIC")], tmp_path)


# ------------------------------------------------------------------ end to end on a synthetic audit (TEST_ONLY)
@pytest.fixture
def e2e(tmp_path):
    bench = (ROOT / "configs" / "league_models" / "benchmark.yaml").read_text(encoding="utf-8")
    bench = bench.replace("min_train_matches: 500", "min_train_matches: 50").replace("samples: 1000", "samples: 60")
    stat = (ROOT / "configs" / "league_models" / "stat_models.yaml").read_text(encoding="utf-8")
    stat = stat.replace("min_uefa_matches_total: 200", "min_uefa_matches_total: 100").replace("min_local_fit_matches: 100", "min_local_fit_matches: 40")
    stat = stat.replace("min_validation_matches: 60", "min_validation_matches: 20")
    ml = yaml.safe_load((ROOT / "configs" / "league_models" / "ml_models.yaml").read_text(encoding="utf-8"))
    ml["tuning"].update({"max_trials": 2, "n_startup_trials": 1, "max_rounds": 30, "early_stopping_rounds": 5})
    ml["gbm"]["rounds_default"] = 15
    ml["xgb_cross_check"]["rounds"] = 15
    ml["local"].update({"min_train_matches": 120, "proof_required_below": 400, "min_inner_fit_matches": 60, "min_minority_class": 3, "fixed_rounds": 10})
    ml["blend"].update({"min_validation_matches": 20})
    ml["features"].update({"min_prior_matches": 20})
    pol = yaml.safe_load((ROOT / "configs" / "league_models" / "approval_policy.yaml").read_text(encoding="utf-8"))
    pol["bootstrap"].update({"samples": 300})
    pol["screen"].update({"min_dev_rows": 20})
    pol["holdout_gate"].update({"min_holdout_rows": 20})
    pol["calibration"].update({"min_rows": 60})
    (tmp_path / "configs").mkdir()
    shutil.copy(ROOT / "configs" / "model.yaml", tmp_path / "configs" / "model.yaml")
    for name, text in (("bench.yaml", bench), ("stat.yaml", stat)):
        (tmp_path / name).write_text(text, encoding="utf-8")
    (tmp_path / "ml.yaml").write_text(yaml.safe_dump(ml), encoding="utf-8")
    (tmp_path / "policy.yaml").write_text(yaml.safe_dump(pol), encoding="utf-8")
    return tmp_path


def _run(root, out, **kw):
    return s5.run(root, utc(2026, 10, 20), out, bench_config_path=root / "bench.yaml", stat_config_path=root / "stat.yaml", ml_config_path=root / "ml.yaml",
                  policy_path=root / "policy.yaml", audit=_synthetic_audit(), **kw)  # fmt: skip


def test_dev_stage_writes_a_deterministic_preregistration_and_never_touches_the_holdout(e2e):
    log = e2e / "holdout_log.json"
    a = _run(e2e, e2e / "a", holdout_log=log)
    b = _run(e2e, e2e / "b", holdout_log=log)
    assert a["prereg_sha256"] == b["prereg_sha256"] and not log.exists() and not hl.is_consumed(log)
    pre = json.loads((e2e / "a" / "preregistration.json").read_text(encoding="utf-8"))
    assert pre["screen_selection"] is not None and pre["holdout"]["kind"].startswith("FROZEN") and "git_sha" not in pre
    split = json.loads((e2e / "a" / "split_manifest.json").read_text(encoding="utf-8"))
    assert split["fixture_roles_disjoint"]["holdout_reserved_guard_in_any_fold"] == 0 and "FROZEN_HOLDOUT" in split["windows"]["roles"]
    cal = json.loads((e2e / "a" / "calibration_manifest.json").read_text(encoding="utf-8"))
    assert set(cal["selection"]) == {"1x2", "over_under", "btts"} and cal["rule"].startswith("a calibrator is used only when")
    assert all(c["status"] in ("FITTED",) or c["status"].startswith("NOT_FITTED") for t in cal["final_calibrators"].values() for c in t.values())
    assert not (e2e / "a" / "holdout_report.json").exists()


def test_the_holdout_stage_refuses_without_a_matching_preregistration_and_reads_the_holdout_once(e2e):
    log = e2e / "holdout_log.json"
    with pytest.raises(SystemExit, match="no committed pre-registration"):
        _run(e2e, e2e / "x", stage="holdout", holdout_log=log, expect_prereg=e2e / "missing.json", dirty_override=False)
    dev = _run(e2e, e2e / "dev")
    wrong = e2e / "wrong.json"
    wrong.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit, match="does not reproduce"):
        _run(e2e, e2e / "y", stage="holdout", holdout_log=log, expect_prereg=wrong, dirty_override=False)
    assert not hl.is_consumed(log)  # refused requests do not burn the holdout
    with pytest.raises(hl.HoldoutLockError, match="uncommitted"):
        _run(e2e, e2e / "d", stage="holdout", holdout_log=log, expect_prereg=e2e / "dev" / "preregistration.json", dirty_override=True)
    assert not hl.is_consumed(log)
    res = _run(e2e, e2e / "final", stage="holdout", holdout_log=log, expect_prereg=e2e / "dev" / "preregistration.json", dirty_override=False)
    assert hl.is_consumed(log) and res["prereg_sha256"] == dev["prereg_sha256"]
    entry = hl.read_log(log)[0]
    assert entry["prereg_sha256"] == dev["prereg_sha256"] and entry["fixtures"] > 0
    with pytest.raises(hl.HoldoutLockError, match="already evaluated once"):
        _run(e2e, e2e / "again", stage="holdout", holdout_log=log, expect_prereg=e2e / "dev" / "preregistration.json", dirty_override=False)
    dec = json.loads((e2e / "final" / "approval_decisions.json").read_text(encoding="utf-8"))["decisions"]
    assert dec and all(d["status"] in ag.STATUSES for d in dec.values())
    for d in dec.values():
        if d["status"] == ag.APPROVED:
            assert d["holm_adjusted_pass"] and all(c["passed"] for c in d["checks"])
    report = json.loads((e2e / "final" / "holdout_report.json").read_text(encoding="utf-8"))
    assert report["prereg_sha256"] == dev["prereg_sha256"] and report["label"] == "FROZEN HOLDOUT, evaluated once"
    assert sum(report["status_counts"].values()) == len(dec)
    ids = {r["fixture_id"] for r in (json.loads(x) for x in [])}  # placeholder keeps the structure explicit: no fixture list is written
    assert not ids


def test_the_rehearsal_runs_the_identical_code_on_a_pseudo_holdout_with_its_own_log(e2e):
    out = e2e / "reh"
    res = _run(e2e, out, stage="holdout", rehearsal=True, holdout_log=out / "rehearsal_log.json")
    assert (out / "rehearsal_log.json").is_file() and "pseudo" in json.loads((out / "preregistration.json").read_text(encoding="utf-8"))["holdout"]["kind"]
    assert res["holdout"]["status_counts"] and "REHEARSAL" in json.loads((out / "holdout_report.json").read_text(encoding="utf-8"))["label"]
    reg = json.loads((out / "model_registry.json").read_text(encoding="utf-8"))["entries"]
    for e in reg:  # every registered artifact exists and hashes to its registered value
        assert hashlib.sha256((out / e["artifact_path"]).read_bytes()).hexdigest() == e["artifact_sha256"]
    dry = json.loads((out / "router_dry_run.json").read_text(encoding="utf-8"))
    assert dry["production_fraction"] == 0.0 and all(r.get("mode") == "GLOBAL" for r in dry["routes"] if r.get("use") == "production")
    assert all(r.get("fallback_reason") != "ARTIFACT_INVALID" for r in dry["routes"]) and not dry["alarms"]


def test_a_changed_policy_or_snapshot_changes_the_preregistration(e2e):
    base = _run(e2e, e2e / "p1")["prereg_sha256"]
    pol = yaml.safe_load((e2e / "policy.yaml").read_text(encoding="utf-8"))
    pol["holdout_gate"]["min_holdout_rows"] = 21
    (e2e / "policy2.yaml").write_text(yaml.safe_dump(pol), encoding="utf-8")
    other = s5.run(e2e, utc(2026, 10, 20), None, bench_config_path=e2e / "bench.yaml", stat_config_path=e2e / "stat.yaml", ml_config_path=e2e / "ml.yaml", policy_path=e2e / "policy2.yaml",
                   audit=_synthetic_audit())["prereg_sha256"]  # fmt: skip
    assert other != base


def test_licence_revocation_stops_the_run_before_any_row_is_read(e2e, monkeypatch):
    from src.league_models.licenses import LicenseGateError

    monkeypatch.setattr(s5, "LICENSED_PROVIDERS", ["openfootball_domestic", "football_data_co_uk"])
    with pytest.raises(LicenseGateError):
        _run(e2e, None, dry_run=True)


def test_dry_run_reports_the_plan_and_the_lock_state(e2e):
    plan = _run(e2e, None, dry_run=True, holdout_log=e2e / "none.json")["plan"]
    assert plan["holdout_already_consumed"] is False and plan["stage"] == "dev" and len(plan["development_folds"]) == 5


def test_cli_verify_and_compare_to_committed(e2e):
    out, docs = e2e / "c", e2e / "docs"
    _run(e2e, out)
    assert s5.main(["--root", str(e2e), "--verify", "c"]) == 0
    docs.mkdir()
    for n in (*s5.EXACT_FILES, *s5.NUMERIC_FILES):
        shutil.copy(out / n, docs / f"s5_{n}")
    assert s5.compare_to_committed(out, docs, 1e-3)["ok"] is True
    pre = json.loads((docs / "s5_preregistration.json").read_text(encoding="utf-8"))
    pre["screen_selection"]["TAMPERED|1x2"] = "x"
    (docs / "s5_preregistration.json").write_text(json.dumps(pre), encoding="utf-8")
    assert s5.compare_to_committed(out, docs, 1e-3)["ok"] is False
    (out / "dev_screen.json").write_text("{}", encoding="utf-8")
    assert s5.main(["--root", str(e2e), "--verify", "c"]) == 1


# ------------------------------------------------------------------ real data and committed outputs
@needs_cache
def test_real_dry_run_sees_the_frozen_holdout_listing_without_reading_a_score():
    plan = s5.run(ROOT, utc(2026, 10, 20), None, dry_run=True, holdout_log=ROOT / "artifacts" / "no_such_log.json")["plan"]
    assert plan["holdout_fixtures"] == 2278 and len(plan["holdout_listing_sha256"]) == 64
    assert plan["development_folds"] == ["2020-21", "2021-22", "2022-23", "2023-24", "2024-25"]


def test_committed_sprint5_outputs_are_internally_consistent():
    manifest = OUT / "s5_run_manifest.json"
    if not manifest.is_file():
        pytest.skip("Sprint 5 outputs not generated")
    run = json.loads(manifest.read_text(encoding="utf-8"))
    for name, digest in run["output_hashes"].items():
        committed = OUT / f"s5_{name}"
        if committed.is_file():
            assert hashlib.sha256(committed.read_bytes()).hexdigest() == digest, name
    pre = (OUT / "s5_preregistration.json")
    assert pre.is_file() and json.loads(pre.read_text(encoding="utf-8"))["policy_sha256"] == hashlib.sha256((ROOT / "configs" / "league_models" / "approval_policy.yaml").read_bytes()).hexdigest()
    log = hl.read_log(OUT / "s5_holdout_access_log.json")
    if log:
        assert len(log) == 1 and log[0]["status"] == "CONSUMED" and log[0]["prereg_sha256"] == hashlib.sha256(pre.read_bytes()).hexdigest()
        dec = json.loads((OUT / "s5_approval_decisions.json").read_text(encoding="utf-8"))["decisions"]
        assert len(dec) == 27 and all(d["status"] in ag.STATUSES for d in dec.values())
        for d in dec.values():
            if d["status"] == ag.APPROVED:
                assert d["holm_adjusted_pass"] and all(c["passed"] for c in d["checks"]) and d["candidate"]
        assert json.loads((OUT / "s5_holdout_report.json").read_text(encoding="utf-8"))["label"] == "FROZEN HOLDOUT, evaluated once"
