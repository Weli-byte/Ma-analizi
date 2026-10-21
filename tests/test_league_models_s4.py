"""League-model programme, Sprint 4 (ADR 0066): the feature contract, boosted-tree adapters, tuner, local eligibility, shrinkage blend.

Tests marked TEST_ONLY use minimal synthetic data for logic; they are NOT verification of any real data. The real-data checks read the
repository's cached source files (skipped when the cache is absent, as in CI) and the committed outputs."""

import dataclasses
import hashlib
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
import yaml

from src.evaluation.dataset import EvalRow
from src.league_models import ml_benchmark, ml_features, ml_models
from src.league_models.benchmark import verify_run
from src.league_models.ml_features import (
    FeatureConfig,
    build_features,
    make_schema,
    perturb,
    perturbation_audit,
)
from src.league_models.ml_models import (
    GBM,
    MLConfig,
    NotTrainedML,
    blend_weight,
    check_trainable,
    local_eligibility,
    mix,
    tune,
)
from src.league_models.splits import Fold
from src.league_models.stat_models import EloSpec, TimeAwareElo
from src.snapshot.stages import SnapshotStage
from tests.test_league_models_s3 import _synthetic_audit

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "league_models"
CACHE = ROOT / "artifacts" / "markets" / "_cache" / "openfootball"
needs_cache = pytest.mark.skipif(not CACHE.is_dir(), reason="cached openfootball source files are absent (CI)")
VOCAB = ["ENG1", "ESP1", "GER1", "ITA1", "FRA1", "TUR1", "UEFA_UCL", "UEFA_UEL", "UEFA_UECL"]


def utc(*a: int) -> datetime:
    return datetime(*a, tzinfo=UTC)


def raw_cfg() -> dict:
    return yaml.safe_load((ROOT / "configs" / "league_models" / "ml_models.yaml").read_text(encoding="utf-8"))


def fcfg(**over) -> FeatureConfig:
    base = FeatureConfig.from_raw(raw_cfg(), VOCAB, 3, SnapshotStage.T_24H, EloSpec())
    return dataclasses.replace(base, **over)


def _row(i, comp, season, kick, h, a, hg, ag) -> EvalRow:
    return EvalRow(f"f{i:04d}", comp, season, kick, h, a, 0 if hg > ag else (1 if hg == ag else 2), home_goals=hg, away_goals=ag)


def league(n=240, seed=4, comp="ITA1", season="2021-22", teams=8, prefix="ITA", start=None, step_h=72):
    """TEST_ONLY: goals from known strengths. Not real matches."""
    start = start or utc(2021, 9, 1, 18)
    rng = np.random.default_rng(seed)
    att = rng.normal(0, 0.3, teams)
    rows = []
    for i in range(n):
        h, a = rng.choice(teams, 2, replace=False)
        gh = int(rng.poisson(np.exp(0.25 + 0.2 + att[h] - att[a])))
        ga = int(rng.poisson(np.exp(0.25 + att[a] - att[h])))
        rows.append(_row(i, comp, season, start + timedelta(hours=step_h * i), f"{prefix}_t{h}", f"{prefix}_t{a}", gh, ga))
    return rows


# ------------------------------------------------------------------ the feature contract
def test_schema_is_one_ordered_hashed_contract_with_complete_groups():
    s = make_schema((5, 10))
    assert len(set(s.names)) == len(s.names) and set(s.groups) == set(s.names)
    assert make_schema((5, 10)).sha256 == s.sha256 and make_schema((5, 12)).sha256 != s.sha256  # the hash covers names, order and windows
    assert s.names[0] == "league_code" and set(s.groups.values()) >= {"league_identity", "league_priors", "elo", "form", "rest_congestion", "home_away"}
    assert not set(s.without_groups("league_identity", "league_priors")) & set(s.columns_of("league_identity", "league_priors"))
    assert "odds" not in " ".join(s.names) and "xg" not in " ".join(s.names)  # nothing the licensed universe cannot provide
    assert "xG" in " ".join(s.as_dict()["not_available_in_the_licensed_universe"])


def test_train_and_serve_use_one_code_path_and_earlier_rows_never_change_when_later_rows_arrive():
    rows = league()
    cfg = fcfg()
    head = build_features(rows[:150], cfg)
    full = build_features(rows, cfg)
    assert head.schema.sha256 == full.schema.sha256
    assert np.array_equal(head.X, full.X[:150], equal_nan=True)  # prefix stability: the future cannot touch the past


def test_future_results_are_invisible_to_earlier_rows_and_the_audit_catches_a_leak(monkeypatch):
    rows = league()
    cfg = fcfg()
    point = rows[120].kickoff_utc
    audit = perturbation_audit(rows, cfg, [point])
    assert audit["violations"] == 0 and audit["points"][point.isoformat()]["rows_identical"] is True
    assert audit["points"][point.isoformat()]["rows_after_point_that_changed"] > 0  # the perturbation is real: rows after the point do change

    # a deliberately leaky builder (a feature that averages ALL results, future included) must be flagged by the same audit
    def leaky(rs, c):
        schema = make_schema(c.windows)
        col = np.full((len(rs), len(schema.names)), float(np.mean([r.home_goals for r in rs])))
        return ml_features.FeatureMatrix(schema, col, [r.fixture_id for r in rs])

    monkeypatch.setattr(ml_features, "build_features", leaky)
    assert ml_features.perturbation_audit(rows, cfg, [point])["violations"] > 0


def test_the_builder_refuses_an_availability_rule_that_would_admit_an_unprocessed_match():
    """Defence in depth: a negative result lag would make future matches 'available'; the builder fails instead of reading them."""
    with pytest.raises(AssertionError, match="never processed"):
        build_features(league(n=40), fcfg(lag_hours=-24 * 4000))


def test_a_result_still_unpublished_at_the_stage_cutoff_is_not_history():
    """TEST_ONLY: T1 played 20 h before T2's match; at the t-24h cutoff that result is not yet known (kick-off + 3 h lag > cutoff)."""
    t0 = utc(2022, 1, 1, 18)
    rows = [_row(i, "ITA1", "2021-22", t0 - timedelta(days=30 - i), f"ITA_t{i % 4}", f"ITA_t{(i + 1) % 4}", 1, 1) for i in range(20)]
    rows.append(_row(100, "ITA1", "2021-22", t0, "ITA_t0", "ITA_t1", 5, 0))               # the 'late' result
    rows.append(_row(101, "ITA1", "2021-22", t0 + timedelta(hours=20), "ITA_t0", "ITA_t2", 0, 0))
    cfg = fcfg(min_form_matches=1)
    a = build_features(rows, cfg)
    changed = [dataclasses.replace(r, home_goals=0, away_goals=4, outcome=2) if r.fixture_id == "f0100" else r for r in rows]
    b = build_features(changed, cfg)
    i = [r.fixture_id for r in rows].index("f0101")
    assert np.array_equal(a.X[i], b.X[i], equal_nan=True)
    cfg_late = fcfg(min_form_matches=1, stage=SnapshotStage.KICKOFF)  # at kick-off the same result IS history (20 h > lag)
    assert not np.array_equal(build_features(rows, cfg_late).X[i], build_features(changed, cfg_late).X[i], equal_nan=True)


def test_fixtures_sharing_a_kickoff_never_see_each_other():
    rows = league(n=60, step_h=72)
    k = rows[40].kickoff_utc
    twin = _row(900, "ITA1", "2021-22", k, "ITA_t6", "ITA_t7", 3, 3)
    rows2 = rows[:41] + [twin]
    cfg = fcfg()
    base = build_features(rows2, cfg)
    bumped = build_features([dataclasses.replace(r, home_goals=9, away_goals=0, outcome=0) if r.fixture_id == "f0900" else r for r in rows2], cfg)
    idx = [r.fixture_id for r in rows2].index("f0040")
    assert np.array_equal(base.X[idx], bumped.X[idx], equal_nan=True)


def test_missing_values_are_nan_with_flags_never_zero():
    rows = league(n=30)
    fm = build_features(rows, fcfg())
    first = fm.X[0]
    col = fm.schema.index
    assert np.isnan(first[col("h_gf5")]) and first[col("form_available")] == 0.0 and np.isnan(first[col("h_rest_days")]) and first[col("rest_available")] == 0.0
    assert np.isnan(first[col("lp_goals_per_match")]) and first[col("lp_available")] == 0.0 and first[col("either_new_team")] == 1.0
    last = fm.X[-1]
    assert last[col("form_available")] == 1.0 and not np.isnan(last[col("h_gf5")]) and last[col("lp_available")] == 0.0  # 29 prior matches < 50: priors stay NaN
    assert np.isfinite(fm.X[:, col("elo_diff")]).all() and np.isfinite(fm.X[:, col("h_matches_30d")]).all()


def test_unseen_league_is_nan_with_a_flag_and_the_model_still_predicts():
    rows = [dataclasses.replace(r, league_id="XYZ9") if i % 2 else r for i, r in enumerate(league(n=300))]
    fm = build_features(rows, fcfg())
    col = fm.schema.index
    assert fm.unseen_league_rows == 150
    unseen = np.array([r.league_id == "XYZ9" for r in rows])
    assert np.isnan(fm.X[unseen, col("league_code")]).all() and (fm.X[unseen, col("league_known")] == 0).all() and (fm.X[~unseen, col("league_known")] == 1).all()
    y = np.array([r.outcome for r in rows])
    cfg = MLConfig(raw_cfg(), "x")
    m = GBM("lightgbm", "1x2", ml_models.full_params(cfg, None), list(fm.schema.names), ["league_code"], 1).fit(fm.X[:200], y[:200], 20)
    p = m.predict_proba(fm.X[200:])
    assert np.isfinite(p).all() and np.allclose(p.sum(axis=1), 1.0)


def test_elo_feature_equals_the_public_replay_when_results_are_published_between_matches():
    """TEST_ONLY: with every match published before the next cutoff the availability-driven Elo and the kick-off ordered replay agree."""
    rows = league(n=120, step_h=96)
    cfg = fcfg()
    elo = TimeAwareElo(EloSpec(), "ref", ml_features.UEFA_IDS)
    ref, _ = elo.replay(rows, update=True)
    assert np.allclose(ml_features.replay_equivalence_diffs(rows, cfg), ref)


def test_perturb_replaces_results_from_a_point_on():
    rows = league(n=10)
    p = perturb(rows, rows[5].kickoff_utc)
    assert all(r.home_goals == 7 and r.outcome == 0 for r in p[5:]) and p[:5] == rows[:5]


# ------------------------------------------------------------------ boosted-tree adapters
def _xy(n=400, seed=2):
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, 4))
    y3 = np.where(X[:, 0] > 0.5, 0, np.where(X[:, 0] < -0.5, 2, 1))
    yb = (X[:, 1] + 0.3 * rng.normal(size=n) > 0).astype(int)
    return X, y3, yb


def _gbm(backend, target, **kw):
    p = {k: v for k, v in ml_models.full_params(MLConfig(raw_cfg(), "x"), None).items() if backend == "lightgbm"} if backend == "lightgbm" else {"max_depth": 3, "learning_rate": 0.1}
    return GBM(backend, target, p, [f"x{i}" for i in range(4)], [], 7, **kw)


@pytest.mark.parametrize("backend", ["lightgbm", "xgboost"])
def test_gbm_probabilities_are_valid_ordered_and_deterministic(backend):
    X, y3, yb = _xy()
    m = _gbm(backend, "1x2").fit(X, y3, 30)
    p = m.predict_proba(X)
    assert p.shape == (400, 3) and np.allclose(p.sum(axis=1), 1.0) and (p > 0).all()
    assert p[X[:, 0] > 1.5][:, 0].mean() > 0.6 and p[X[:, 0] < -1.5][:, 2].mean() > 0.6  # class order = H, D, A = outcome encoding 0, 1, 2
    assert np.array_equal(p, _gbm(backend, "1x2").fit(X, y3, 30).predict_proba(X))
    b = _gbm(backend, "btts").fit(X, yb, 30).predict_proba(X)
    assert b.shape == (400,) and ((b > 0) & (b < 1)).all()
    assert m.model_text() == _gbm(backend, "1x2").fit(X, y3, 30).model_text()  # the artifact is reproducible


def test_one_class_and_tiny_minority_targets_refuse_to_train():
    X, _, _ = _xy()
    with pytest.raises(NotTrainedML, match="one-class"):
        _gbm("lightgbm", "btts").fit(X, np.ones(400, int), 10)
    with pytest.raises(NotTrainedML, match="rarest class"):
        check_trainable("1x2", np.array([0] * 50 + [1] * 50 + [2]), min_minority=2)
    with pytest.raises(NotTrainedML):
        _gbm("lightgbm", "1x2").predict_proba(X)  # never fitted


def test_class_weights_balance_a_skewed_target():
    y = np.array([0] * 80 + [1] * 10 + [2] * 10)
    w = ml_models.class_weights("1x2", y, "balanced")
    assert w[y == 1].sum() == pytest.approx(w[y == 0].sum())
    assert ml_models.class_weights("1x2", y, "none") is None
    with pytest.raises(ValueError):
        ml_models.class_weights("1x2", y, "bogus")


def test_early_stopping_uses_only_the_validation_block_and_reports_the_best_iteration():
    X, y3, _ = _xy(600)
    m = _gbm("lightgbm", "1x2").fit(X[:400], y3[:400], 300, valid=(X[400:], y3[400:]), early_stopping=10)
    assert 1 <= m.best_iteration <= 300 and m.predict_proba(X[400:]).shape == (200, 3)


# ------------------------------------------------------------------ tuner
def _small_cfg(trials=4) -> MLConfig:
    raw = raw_cfg()
    raw["tuning"].update({"max_trials": trials, "n_startup_trials": 2, "max_rounds": 60, "early_stopping_rounds": 5})
    return MLConfig(raw, "t")


def test_tuner_records_every_trial_is_seeded_and_only_sees_the_blocks_it_is_given():
    X, y3, _ = _xy(500)
    args = (X[:350], y3[:350], X[350:], y3[350:], [f"x{i}" for i in range(4)], [], 11)
    a = tune(_small_cfg(), "1x2", *args)
    b = tune(_small_cfg(), "1x2", *args)
    strip = lambda t: {k: v for k, v in t.items() if k != "wallclock_seconds_excluded_from_hashes"}  # noqa: E731
    assert strip(a) == strip(b)  # same seed, same trials
    assert a["trials_run"] == 4 == len(a["trials"]) and a["stopped_by"] == "max_trials"
    best = min(t["validation_log_loss"] for t in a["trials"])
    assert a["best_validation_log_loss"] == best and a["fit_rows"] == 350 and a["validation_rows"] == 150
    assert set(a["best_params"]) == set(raw_cfg()["tuning"]["space"])
    assert all(a["best_params"][k] in v for k, v in raw_cfg()["tuning"]["space"].items())  # inside the declared grid


def test_a_tuner_whose_every_trial_fails_is_not_trained():
    X, _, _ = _xy(200)
    with pytest.raises(NotTrainedML):
        tune(_small_cfg(2), "btts", X[:100], np.ones(100, int), X[100:], np.ones(100, int), [f"x{i}" for i in range(4)], [], 1)


# ------------------------------------------------------------------ local eligibility and the blend
def test_local_models_follow_the_volume_tiers_and_refuse_one_class_targets():
    cfg = MLConfig(raw_cfg(), "x")
    y_ok = {"1x2": np.array([0, 1, 2] * 200), "over_under": np.array([0, 1] * 300), "btts": np.array([0, 1] * 300)}
    assert local_eligibility(cfg, 499, y_ok)[0] is False and "LOCAL_INELIGIBLE" in local_eligibility(cfg, 499, y_ok)[1]
    ok, why = local_eligibility(cfg, 800, y_ok)
    assert ok and why.startswith("ELIGIBLE_EVIDENCE_ONLY")
    assert local_eligibility(cfg, 1600, y_ok) == (True, "ELIGIBLE")
    one_class = {**y_ok, "btts": np.ones(600, int)}
    ok, why = local_eligibility(cfg, 1600, one_class)
    assert not ok and "btts" in why


def test_blend_weight_is_zero_without_data_or_gain_and_shrunk_toward_zero_with_gain():
    cfg = MLConfig(raw_cfg(), "x")
    rng = np.random.default_rng(0)
    n = 300
    y = rng.integers(0, 2, n)
    p_global = np.full(n, 0.5)
    p_good_local = np.where(y == 1, 0.8, 0.2)
    assert blend_weight(cfg, "btts", p_global[:30], p_good_local[:30], y[:30])["weight"] == 0.0           # too few validation rows
    assert "weight 0" in blend_weight(cfg, "btts", p_global, p_global, y)["reason"]                         # no gain
    b = blend_weight(cfg, "btts", p_global, p_good_local, y)
    assert 0 < b["weight"] < b["best_raw_weight"] == 1.0 and b["weight"] == pytest.approx(1.0 * n / (n + cfg.raw["blend"]["shrink_k"]))
    bad_local = np.where(y == 1, 0.2, 0.8)
    assert blend_weight(cfg, "btts", p_global, bad_local, y)["weight"] == 0.0                              # a worse local model gets nothing


def test_mix_stays_on_the_simplex_and_w_zero_is_the_global_model():
    rng = np.random.default_rng(1)
    g = rng.dirichlet(np.ones(3), 50)
    l = rng.dirichlet(np.ones(3), 50)  # noqa: E741
    w = rng.uniform(0, 1, 50)
    m = mix(g, l, w)
    assert np.allclose(m.sum(axis=1), 1.0) and (m >= 0).all() and np.array_equal(mix(g, l, np.zeros(50)), g)
    gb, lb = rng.uniform(0.05, 0.95, 50), rng.uniform(0.05, 0.95, 50)
    assert ((mix(gb, lb, w) >= 0) & (mix(gb, lb, w) <= 1)).all()


# ------------------------------------------------------------------ inner split
def _fold(train, inner):
    return Fold("2024-25", tuple(sorted({r.season for r in train})), inner, utc(2024, 8, 1), tuple(train), ())


def test_inner_split_is_the_last_training_season_with_a_purge_and_never_overlaps_in_time():
    a = league(n=100, season="2022-23", start=utc(2022, 9, 1, 18))
    b = [dataclasses.replace(r, fixture_id="g" + r.fixture_id) for r in league(n=100, seed=9, season="2023-24", start=utc(2023, 9, 1, 18))]
    fr = ml_benchmark.FoldRun(MLConfig(raw_cfg(), "x"), None, None, _fold(a + b, "2023-24"), None, "snap", None, 3)
    fit, val, kind = fr.inner_masks(a + b)
    assert kind.startswith("LAST_TRAINING_SEASON") and fit[:100].all() and not fit[100:].any() and val[100:].all() and not (fit & val).any()
    # an earlier-season match that is still unpublished when validation starts is purged from the inner fit
    late = dataclasses.replace(a[-1], fixture_id="late", kickoff_utc=b[0].kickoff_utc - timedelta(hours=1))
    fit2, _, kind2 = ml_benchmark.FoldRun(MLConfig(raw_cfg(), "x"), None, None, _fold([*a, late, *b], "2023-24"), None, "snap", None, 3).inner_masks([*a, late, *b])
    assert not fit2[100] and "purged 1" in kind2


def test_a_single_training_season_is_cut_in_time_with_a_purge_not_borrowed_from_the_future():
    one = league(n=200, season="2022-23", start=utc(2022, 9, 1, 18))
    fr = ml_benchmark.FoldRun(MLConfig(raw_cfg(), "x"), None, None, _fold(one, "2022-23"), None, "snap", None, 3)
    fit, val, kind = fr.inner_masks(one)
    assert kind.startswith("WITHIN_SEASON_CHRONOLOGICAL_PURGED") and fit.any() and val.any() and not (fit & val).any()
    assert max(r.kickoff_utc for r, f in zip(one, fit, strict=True) if f) < min(r.kickoff_utc for r, v in zip(one, val, strict=True) if v)
    assert 0.6 < fit.mean() < 0.75


# ------------------------------------------------------------------ end to end on a synthetic audit (TEST_ONLY)
@pytest.fixture
def e2e(tmp_path):
    bench = (ROOT / "configs" / "league_models" / "benchmark.yaml").read_text(encoding="utf-8")
    bench = bench.replace("min_train_matches: 500", "min_train_matches: 50").replace("samples: 1000", "samples: 60")
    stat = (ROOT / "configs" / "league_models" / "stat_models.yaml").read_text(encoding="utf-8")
    stat = stat.replace("min_uefa_matches_total: 200", "min_uefa_matches_total: 100").replace("min_local_fit_matches: 100", "min_local_fit_matches: 40")
    stat = stat.replace("min_validation_matches: 60", "min_validation_matches: 20")
    raw = raw_cfg()
    raw["tuning"].update({"max_trials": 3, "n_startup_trials": 2, "max_rounds": 40, "early_stopping_rounds": 5})
    raw["gbm"]["rounds_default"] = 20
    raw["xgb_cross_check"]["rounds"] = 20
    raw["local"].update({"min_train_matches": 120, "proof_required_below": 400, "min_inner_fit_matches": 60, "min_minority_class": 3, "fixed_rounds": 15})
    raw["blend"].update({"min_validation_matches": 20})
    raw["features"].update({"min_prior_matches": 20})
    (tmp_path / "configs").mkdir()
    shutil.copy(ROOT / "configs" / "model.yaml", tmp_path / "configs" / "model.yaml")
    (tmp_path / "bench.yaml").write_text(bench, encoding="utf-8")
    (tmp_path / "stat.yaml").write_text(stat, encoding="utf-8")
    (tmp_path / "ml.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    return tmp_path


def _run(root, out, **kw):
    return ml_benchmark.run(root, utc(2026, 10, 20), out, bench_config_path=root / "bench.yaml", stat_config_path=root / "stat.yaml",
                            ml_config_path=root / "ml.yaml", audit=_synthetic_audit(), **kw)  # fmt: skip


def test_end_to_end_all_candidates_are_scored_on_identical_fixtures_with_decisions_per_target(e2e):
    out = e2e / "out"
    res = _run(e2e, out, docs_dir=e2e / "docs")
    assert res["registry_entry"]["status"] == "REGISTERED" and verify_run(out)["ok"] is True
    metrics = json.loads((out / "ml_metrics.json").read_text(encoding="utf-8"))
    one = metrics["metrics"]["1x2"]["ALL"]
    assert len({v["n"] for k, v in one.items() if k != "_label_rate"}) == 1  # every GBM, S3 candidate and reference on the same fixtures
    assert set(ml_benchmark.GBM_MODELS) <= set(one) and {"A_dc", "E_elo_bridge", "elo_s2", "A_dc_ridge_tuned", *ml_benchmark.ABLATIONS} <= set(one)
    assert "A_gbm_totals" in metrics["metrics"]["over_under"]["ALL"] and "D_gbm_blend_goals" in metrics["metrics"]["btts"]["ALL"]
    dec = json.loads((out / "league_decisions.json").read_text(encoding="utf-8"))
    assert set(dec["competitions"]) == {"ITA1", "UEFA_UCL"} and dec["multiple_comparisons"]["comparisons_evaluated"] > 0
    for v in dec["competitions"].values():
        assert set(v["by_target"]) == {"1x2", "over_under", "btts"}
        for x in v["by_target"].values():
            assert x["decision"].startswith(("GLOBAL", "CANDIDATE"))
            if x["decision"] == "GLOBAL_A_gbm":
                assert all(c["blocked_by"] for c in x["candidates_vs_A_gbm"].values())  # a global verdict always names why each candidate lost
            else:
                assert x["candidates_vs_A_gbm"][x["decision"][len("CANDIDATE_"):]]["blocked_by"] == []
    assert (e2e / "docs" / "s4_model_cards.md").is_file() and "NOT_AVAILABLE" in (out / "model_cards.md").read_text(encoding="utf-8")
    assert "H11_market_odds" in (out / "hypotheses.json").read_text(encoding="utf-8") and (out / "models").is_dir() and any((out / "models").iterdir())


def test_tuning_is_recorded_per_trial_and_never_touches_the_eval_season(e2e):
    out = e2e / "t"
    _run(e2e, out, eval_start="2024-25")
    trials = json.loads((out / "tuning_trials.json").read_text(encoding="utf-8"))
    fold = trials["folds"]["2024-25"]
    for key in ("B_gbm_league", "A_gbm_tuned_control"):
        for tn in fold[key].values():
            assert tn["trials_run"] == len(tn["trials"]) == 3 and tn["stopped_by"] == "max_trials" and tn["objective"].startswith("validation log loss on the inner block")
    runs = {(r["model_family"], r["league"], r["target"]): r for r in json.loads((out / "training_runs.json").read_text(encoding="utf-8"))["runs"]}
    b = runs[("B_gbm_league", "GLOBAL", "1x2")]
    assert b["train_end_exclusive_utc"].startswith("2024-") and b["feature_schema_sha256"] == make_schema((5, 10)).sha256 and "artifact_sha256" not in b
    with_hash = json.loads((out / "training_runs_with_artifact_hashes.json").read_text(encoding="utf-8"))["runs"]
    r0 = next(r for r in with_hash if r["model_family"] == "B_gbm_league" and r["target"] == "1x2")
    assert hashlib.sha256((out / r0["artifact_file"]).read_bytes()).hexdigest() == r0["artifact_sha256"]  # the registered hash is the real file's


def test_end_to_end_is_deterministic_and_dry_run_writes_nothing(e2e):
    a = _run(e2e, e2e / "a")["run_manifest"]["output_hashes"]
    b = _run(e2e, e2e / "b")["run_manifest"]["output_hashes"]
    assert a == b
    plan = _run(e2e, None, dry_run=True)["plan"]
    assert plan["dry_run"] is True and len(plan["folds"]) == 5 and all(v == 0 for v in plan["replay_violations_per_fold"].values())


def test_predictions_are_immutable_records_and_never_include_frozen_seasons(e2e):
    out = e2e / "p"
    _run(e2e, out, eval_start="2024-25")
    recs = [json.loads(x) for x in (out / "fold_predictions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {r["track"] for r in recs} == {"historical_backtest"} and {r["as_of_quality"] for r in recs} == {"BACKTEST_LIMITED"}
    assert not any(r["season"] in ("2025-26", "2026-27") for r in recs)
    gbm = next(r for r in recs if r["model"] == "D_gbm_blend")
    assert abs(sum(gbm["p_1x2"]) - 1) < 1e-6 and 0 <= gbm["p_over"] <= 1 and 0 <= gbm["p_btts"] <= 1
    leak = json.loads((out / "leakage_audit.json").read_text(encoding="utf-8"))
    assert leak["feature_perturbation_audit"]["violations"] == 0 and "never read" in leak["holdout"]


def test_ineligible_and_failing_local_models_fall_back_explicitly_never_silently(e2e, monkeypatch):
    out = e2e / "f"
    _run(e2e, out, eval_start="2020-21", eval_end="2020-21")
    m = json.loads((out / "ml_metrics.json").read_text(encoding="utf-8"))
    local = m["local_models"]["2020-21"]
    assert all(v["status"] == "LOCAL_INELIGIBLE" and v["eligibility"].startswith("LOCAL_INELIGIBLE") for v in local.values())  # 110 matches < 120: no independent model
    assert all(k.startswith("C_gbm_local:") for k in m["fallbacks"]["2020-21"]) and all(w == 0 for t in m["blend_weights"]["2020-21"].values() for w in t.values())

    real = GBM.fit

    def failing(self, X, y, rounds, valid=None, early_stopping=None):
        if self.backend == "xgboost":
            raise NotTrainedML("forced failure (TEST_ONLY)")
        return real(self, X, y, rounds, valid, early_stopping)

    monkeypatch.setattr(GBM, "fit", failing)
    out2 = e2e / "g"
    _run(e2e, out2, eval_start="2024-25")
    fb = json.loads((out2 / "ml_metrics.json").read_text(encoding="utf-8"))["fallbacks"]["2024-25"]
    assert any(k.startswith("B_xgb_league:") for k in fb) and all("forced failure" in v for k, v in fb.items() if k.startswith("B_xgb_league"))


def test_reference_failure_is_fail_closed(e2e, monkeypatch):
    real = GBM.fit

    def failing(self, X, y, rounds, valid=None, early_stopping=None):
        if self.names and "league_code" not in self.names and valid is None:  # the frozen league-blind reference A_gbm
            raise NotTrainedML("forced (TEST_ONLY)")
        return real(self, X, y, rounds, valid, early_stopping)

    monkeypatch.setattr(GBM, "fit", failing)
    with pytest.raises(SystemExit, match="did not train"):
        _run(e2e, None, eval_start="2024-25")


def test_folds_must_match_sprint2_or_the_run_refuses(e2e):
    (e2e / "docs" / "league_models").mkdir(parents=True)
    (e2e / "docs" / "league_models" / "s3_protocol_manifest.json").write_text(json.dumps({"folds": [{"eval_season": "2020-21", "train_matches": 1}]}), encoding="utf-8")
    with pytest.raises(SystemExit, match="differ from the committed Sprint 3 folds"):
        _run(e2e, None, dry_run=True)


def test_licence_gate_and_cli_verify(e2e, monkeypatch):
    out = e2e / "v"
    _run(e2e, out, eval_start="2024-25")
    assert ml_benchmark.main(["--root", str(e2e), "--verify", "v"]) == 0
    (out / "league_decisions.json").write_text("{}", encoding="utf-8")
    assert ml_benchmark.main(["--root", str(e2e), "--verify", "v"]) == 1
    monkeypatch.setattr(ml_benchmark, "LICENSED_PROVIDERS", ["openfootball_domestic", "football_data_co_uk"])
    from src.league_models.licenses import LicenseGateError

    with pytest.raises(LicenseGateError):
        _run(e2e, None, dry_run=True)
    with pytest.raises(SystemExit):
        ml_benchmark.main(["--as-of", "2026-10-20T00:00:00", "--dry-run"])  # naive timestamps are refused


def test_compare_to_committed_tolerates_noise_and_flags_real_changes(e2e):
    out, docs = e2e / "c", e2e / "docs_copy"
    _run(e2e, out)
    docs.mkdir()
    for name in (*ml_benchmark.EXACT_FILES, *ml_benchmark.NUMERIC_FILES):
        shutil.copy(out / name, docs / f"s4_{name}")
    assert ml_benchmark.compare_to_committed(out, docs, 1e-3)["ok"] is True
    committed = json.loads((docs / "s4_ml_metrics.json").read_text(encoding="utf-8"))
    committed["metrics"]["1x2"]["ALL"]["A_gbm"]["log_loss"] += 5e-5
    (docs / "s4_ml_metrics.json").write_text(json.dumps(committed), encoding="utf-8")
    assert ml_benchmark.compare_to_committed(out, docs, 1e-3)["ok"] is True
    committed["metrics"]["1x2"]["ALL"]["A_gbm"]["log_loss"] += 0.05
    (docs / "s4_ml_metrics.json").write_text(json.dumps(committed), encoding="utf-8")
    assert ml_benchmark.compare_to_committed(out, docs, 1e-3)["ok"] is False
    (docs / "s4_feature_schema.json").write_text("{}", encoding="utf-8")
    assert "feature_schema.json" in ml_benchmark.compare_to_committed(out, docs, 1e-3)["exact_files_differing"]


def test_artifact_hashes_and_the_git_sha_are_stripped_from_the_committed_copy_only():
    runs = [{"model_family": "x", "artifact_sha256": "abc", "git_sha": "deadbeef", "rounds": 3}]
    assert ml_benchmark.strip_artifact_hashes(runs) == [{"model_family": "x", "rounds": 3}] and runs[0]["artifact_sha256"] == "abc" and runs[0]["git_sha"] == "deadbeef"


def test_the_os_dependent_xgboost_check_is_not_compared_across_machines_but_everything_else_is(e2e):
    out, docs = e2e / "x", e2e / "docs_x"
    _run(e2e, out)
    docs.mkdir()
    for name in (*ml_benchmark.EXACT_FILES, *ml_benchmark.NUMERIC_FILES):
        shutil.copy(out / name, docs / f"s4_{name}")
    committed = json.loads((docs / "s4_ml_metrics.json").read_text(encoding="utf-8"))
    committed["metrics"]["1x2"]["ALL"]["B_xgb_league"]["log_loss"] += 0.2  # a different operating system gave different XGBoost trees
    (docs / "s4_ml_metrics.json").write_text(json.dumps(committed), encoding="utf-8")
    report = ml_benchmark.compare_to_committed(out, docs, 1e-3)
    assert report["ok"] is True and "B_xgb_league" in report["not_compared"]
    committed["metrics"]["1x2"]["ALL"]["B_gbm_league"]["log_loss"] += 0.2  # a LightGBM result must still match
    (docs / "s4_ml_metrics.json").write_text(json.dumps(committed), encoding="utf-8")
    assert ml_benchmark.compare_to_committed(out, docs, 1e-3)["ok"] is False
    dec = json.loads((out / "league_decisions.json").read_text(encoding="utf-8"))["competitions"]["ITA1"]["by_target"]["1x2"]["candidates_vs_A_gbm"]
    assert "B_xgb_league" not in dec  # a cross-check, never a decision candidate


# ------------------------------------------------------------------ real cached data and committed outputs
@needs_cache
def test_real_dry_run_uses_the_sprint2_and_3_folds_without_replay_violations():
    plan = ml_benchmark.run(ROOT, utc(2026, 10, 20), None, dry_run=True)["plan"]
    assert plan["sprint2_3_fold_parity"]["status"] == "IDENTICAL_TO_SPRINT_2_AND_3"
    assert [f["eval_season"] for f in plan["folds"]] == ["2020-21", "2021-22", "2022-23", "2023-24", "2024-25"]
    assert all(v == 0 for v in plan["replay_violations_per_fold"].values())


def test_committed_sprint4_outputs_are_internally_consistent():
    manifest = OUT / "s4_run_manifest.json"
    if not manifest.is_file():
        pytest.skip("Sprint 4 outputs not generated")
    run = json.loads(manifest.read_text(encoding="utf-8"))
    for name, digest in run["output_hashes"].items():
        committed = OUT / f"s4_{name}"
        if committed.is_file():
            assert hashlib.sha256(committed.read_bytes()).hexdigest() == digest, name
    dec = json.loads((OUT / "s4_league_decisions.json").read_text(encoding="utf-8"))
    assert set(dec["competitions"]) == {"ENG1", "ESP1", "FRA1", "GER1", "ITA1", "TUR1", "UEFA_UCL", "UEFA_UEL", "UEFA_UECL"}
    for c, v in dec["competitions"].items():
        for t, x in v["by_target"].items():
            if x["decision"] == "GLOBAL_A_gbm":
                assert all(e["blocked_by"] for e in x["candidates_vs_A_gbm"].values()), (c, t)
            else:
                assert x["candidates_vs_A_gbm"][x["decision"][len("CANDIDATE_"):]]["blocked_by"] == [], (c, t)
    assert dec["multiple_comparisons"]["comparisons_evaluated"] > 100
    s3 = json.loads((OUT / "s3_protocol_manifest.json").read_text(encoding="utf-8"))
    s4 = json.loads((OUT / "s4_protocol_manifest.json").read_text(encoding="utf-8"))
    assert s4["folds"] == s3["folds"] and s4["sprint2_3_fold_parity"]["status"] == "IDENTICAL_TO_SPRINT_2_AND_3"
    leak = json.loads((OUT / "s4_leakage_audit.json").read_text(encoding="utf-8"))
    assert leak["feature_perturbation_audit"]["violations"] == 0 and all(v == 0 for v in leak["replay_violations_per_fold"].values())
    assert leak["feature_schema_sha256"] == make_schema((5, 10)).sha256 == run["feature_schema_sha256"]
    metrics = json.loads((OUT / "s4_ml_metrics.json").read_text(encoding="utf-8"))
    assert metrics["no_single_winner"] is True and metrics["reference_model"] == "A_gbm"
    assert run["tuning_stopped_by_wallclock"] == [] and "no GPU" in run["device"]
    hyp = {h["id"]: h for h in json.loads((OUT / "s4_hypotheses.json").read_text(encoding="utf-8"))["hypotheses"]}
    assert hyp["H7_super_lig_squad_continuity"]["verdict"] == hyp["H11_market_odds"]["verdict"] == "NOT_TESTABLE"
    cards = (OUT / "s4_model_cards.md").read_text(encoding="utf-8")
    assert "Odds-aware model card: NOT_AVAILABLE" in cards
