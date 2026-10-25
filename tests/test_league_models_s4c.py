"""League-model programme, Sprint 4c (ADR 0071): nested walk-forward selection, per-league separate models, the hierarchical model, the extension features and the exact-score audit.

Tests marked TEST_ONLY use minimal synthetic data for logic; they are NOT verification of any real data."""

import dataclasses
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
import yaml

from src.evaluation.dataset import EvalRow
from src.league_models.extra_features import ExtraConfig, build_extra, column_names, perturbation_audit
from src.league_models.nested import dc_grid, inner_folds, matrix_losses, select_dc, tune_nested
from src.league_models.per_league import (
    HierarchicalDC,
    PerLeagueDC,
    constant_scoreline_matrices,
    exact_score_metrics,
    grid_edges,
)
from src.league_models.stat_models import DCSpec, NotTrained
from src.snapshot.stages import SnapshotStage

ROOT = Path(__file__).resolve().parents[1]
S4C = yaml.safe_load((ROOT / "configs" / "league_models" / "s4c.yaml").read_text(encoding="utf-8"))
ML = yaml.safe_load((ROOT / "configs" / "league_models" / "ml_models.yaml").read_text(encoding="utf-8"))


def utc(*a: int) -> datetime:
    return datetime(*a, tzinfo=UTC)


def season_of(d: datetime) -> str:
    y = d.year if d.month >= 7 else d.year - 1
    return f"{y}-{str(y + 1)[2:]}"


def synth(comp="ITA1", prefix="ITA", seasons=(2018, 2019, 2020, 2021), per_season=260, teams=8, seed=3, offset=0):
    """TEST_ONLY: goals from known strengths over several seasons. Not real matches."""
    rng = np.random.default_rng(seed)
    att = rng.normal(0, 0.35, teams)
    rows, i = [], offset
    for y in seasons:
        start = utc(y, 8, 20, 18)
        for k in range(per_season):
            h, a = rng.choice(teams, 2, replace=False)
            gh = int(rng.poisson(np.exp(0.2 + 0.2 + att[h] - att[a])))
            ga = int(rng.poisson(np.exp(0.2 + att[a] - att[h])))
            kick = start + timedelta(hours=33 * k)
            rows.append(EvalRow(f"s{i:05d}", comp, season_of(kick), kick, f"{prefix}_t{h}", f"{prefix}_t{a}", 0 if gh > ga else (1 if gh == ga else 2), home_goals=gh, away_goals=ga))
            i += 1
    return rows


def ecfg(**over) -> ExtraConfig:
    return dataclasses.replace(ExtraConfig.from_raw(S4C, 3, SnapshotStage.T_24H), **over)


# ------------------------------------------------------------------ extension features
def test_extension_columns_are_named_once_and_missing_values_are_nan_with_a_flag():
    cfg = ecfg()
    names = column_names(cfg)
    assert len(set(names)) == len(names) and "x_extra_available" in names and "x_adj_strength_diff" in names
    rows = synth(seasons=(2020,), per_season=40)
    X = build_extra(rows, cfg)
    col = {n: i for i, n in enumerate(names)}
    assert np.isnan(X[0]).sum() == len(names) - 5            # the first row has no history: every value is NaN, only the 4 match counts and the flag are 0
    assert X[0, col["x_extra_available"]] == 0.0
    assert not np.isnan(X[-1, col["x_h_gf90d"]]) or X[-1, col["x_h_n90d"]] < cfg.min_matches
    assert X[:, col["x_h_n90d"]].min() == 0.0                 # counts exist even when the value is NaN


def test_window_means_are_hand_computable_and_use_only_matches_inside_the_window():
    cfg = ecfg(min_matches=1)
    base = utc(2020, 9, 1, 18)
    rows = [EvalRow(f"w{i}", "ITA1", "2020-21", base + timedelta(days=d), "ITA_a", "ITA_b" if i % 2 == 0 else "ITA_c", 0, home_goals=gf, away_goals=ga)
            for i, (d, gf, ga) in enumerate([(0, 2, 0), (30, 1, 1), (150, 3, 2)])]
    probe = EvalRow("probe", "ITA1", "2020-21", base + timedelta(days=170), "ITA_a", "ITA_z", 0, home_goals=0, away_goals=0)
    X = build_extra([*rows, probe], cfg)
    col = {n: i for i, n in enumerate(column_names(cfg))}
    last = X[-1]
    # 90 days before day 170 = day 80: only the day-150 match (3-2) is inside; 365 days: all three (mean gf (2+1+3)/3 = 2, mean ga (0+1+2)/3 = 1)
    assert last[col["x_h_gf90d"]] == 3.0 and last[col["x_h_ga90d"]] == 2.0 and last[col["x_h_n90d"]] == 1.0
    assert last[col["x_h_gf365d"]] == pytest.approx(2.0) and last[col["x_h_ga365d"]] == pytest.approx(1.0) and last[col["x_h_n365d"]] == 3.0


def test_a_result_published_after_the_stage_cutoff_is_not_used_and_future_results_never_leak():
    cfg = ecfg(min_matches=1)
    base = utc(2020, 9, 1, 18)
    early = EvalRow("e", "ITA1", "2020-21", base, "ITA_a", "ITA_b", 0, home_goals=2, away_goals=0)
    late = EvalRow("l", "ITA1", "2020-21", base + timedelta(days=5), "ITA_a", "ITA_c", 0, home_goals=4, away_goals=0)
    probe = EvalRow("p", "ITA1", "2020-21", base + timedelta(days=5, hours=2), "ITA_a", "ITA_d", 0, home_goals=0, away_goals=0)   # kicks off 2 h after `late`: its result (kick-off + 3 h lag) is not out at the t-24h cutoff anyway
    X = build_extra([early, late, probe], cfg)
    col = {n: i for i, n in enumerate(column_names(cfg))}
    assert X[2, col["x_h_n90d"]] == 1.0                        # `late` kicked off 2 h earlier: its result is not out at the t-24h cutoff, so only `early` is history
    later = dataclasses.replace(probe, fixture_id="p2", kickoff_utc=late.kickoff_utc + timedelta(days=2))
    assert build_extra([early, late, later], cfg)[2, col["x_h_n90d"]] == 2.0       # two days on, both results are out
    same_kick = dataclasses.replace(probe, kickoff_utc=late.kickoff_utc)
    Y = build_extra([early, late, same_kick], cfg)
    assert Y[2, col["x_h_n90d"]] == 1.0                        # a fixture with the same kick-off never sees `late`
    rows = synth(seasons=(2019, 2020), per_season=120)
    report = perturbation_audit(rows, ecfg(), [utc(2019, 12, 1), utc(2020, 11, 1)])
    assert report["violations"] == 0


def test_opponent_adjustment_rewards_goals_against_a_strong_defence():
    cfg = ecfg(min_matches=3, shrink_k=1.0, adjust_matches=10)
    base = utc(2020, 9, 1, 18)
    rows, i = [], 0
    # a stingy defence "D" concedes 0 in six matches against fillers; then team S and team W each score 1 against D-like opposition in their own matches
    for k in range(6):
        rows.append(EvalRow(f"d{i}", "ITA1", "2020-21", base + timedelta(days=k), "ITA_D", f"ITA_f{k}", 0, home_goals=2, away_goals=0))
        i += 1
    for k in range(3):                     # S scores 1 against the stingy D; W scores 1 against a leaky L (concedes 3 a game)
        rows.append(EvalRow(f"s{i}", "ITA1", "2020-21", base + timedelta(days=10 + k), "ITA_S", "ITA_D", 0, home_goals=1, away_goals=0))
        i += 1
        rows.append(EvalRow(f"w{i}", "ITA1", "2020-21", base + timedelta(days=10 + k), "ITA_W", "ITA_L", 0, home_goals=1, away_goals=3))
        i += 1
    for k in range(6):
        rows.append(EvalRow(f"l{i}", "ITA1", "2020-21", base + timedelta(days=k), "ITA_L", f"ITA_g{k}", 0, home_goals=2, away_goals=3))
        i += 1
    probe = EvalRow("probe", "ITA1", "2020-21", base + timedelta(days=40), "ITA_S", "ITA_W", 0, home_goals=0, away_goals=0)
    X = build_extra([*rows, probe], cfg)
    col = {n: j for j, n in enumerate(column_names(cfg))}
    # both averaged 1 goal a game, but S did it against the better defence, so S's adjusted attack must be higher than W's
    assert X[-1, col["x_h_adj_att"]] > X[-1, col["x_a_adj_att"]]


# ------------------------------------------------------------------ nested walk-forward
def test_inner_folds_predict_each_late_season_from_strictly_earlier_ones_with_the_lag_embargo():
    rows = synth(seasons=(2017, 2018, 2019, 2020), per_season=200)
    folds, dropped = inner_folds(rows, 3, 3, 50)
    assert [f.season for f in folds] == ["2018-19", "2019-20", "2020-21"] and not dropped
    for f in folds:
        assert max(r.kickoff_utc for r in f.fit) + timedelta(hours=3) <= min(r.kickoff_utc for r in f.val)
        assert not {r.fixture_id for r in f.fit} & {r.fixture_id for r in f.val}
        assert all(r.season == f.season for r in f.val)
    assert [len(f.fit) for f in folds] == sorted(len(f.fit) for f in folds)          # expanding window


def test_a_season_without_enough_earlier_matches_is_dropped_and_reported_not_silently_skipped():
    rows = synth(seasons=(2019, 2020), per_season=60)
    folds, dropped = inner_folds(rows, 3, 3, 100)
    assert folds == [] and len(dropped) == 2 and all("fit matches" in d["reason"] for d in dropped)


def test_a_single_training_season_is_cut_in_time_with_a_purge():
    rows = synth(seasons=(2020,), per_season=150)
    folds, _ = inner_folds(rows, 3, 3, 50)
    assert len(folds) == 1 and folds[0].kind == "WITHIN_SEASON_CHRONOLOGICAL_PURGED"
    assert max(r.kickoff_utc for r in folds[0].fit) + timedelta(hours=3) <= min(r.kickoff_utc for r in folds[0].val)


def test_dc_grid_is_a_deterministic_cartesian_product_and_edges_are_reported():
    base = DCSpec()
    g = dc_grid({"l2_team": [1.0, 4.0], "half_life_days": [None, 365]}, base)
    assert [n for n, _ in g] == ["l2_team=1.0|half_life_days=none", "l2_team=1.0|half_life_days=365", "l2_team=4.0|half_life_days=none", "l2_team=4.0|half_life_days=365"]
    assert dict(g)["l2_team=4.0|half_life_days=365"].half_life_days == 365
    assert grid_edges({"l2_team": [1.0, 4.0, 16.0]}, dataclasses.replace(base, l2_team=16.0)) == ["l2_team=16.0 is the largest grid value"]
    assert grid_edges({"l2_team": [1.0, 4.0, 16.0]}, dataclasses.replace(base, l2_team=4.0)) == []


def test_select_dc_chooses_per_target_records_every_candidate_and_is_deterministic():
    rows = synth(seasons=(2017, 2018, 2019, 2020), per_season=220)
    folds, _ = inner_folds(rows, 3, 2, 50)
    grid = dc_grid({"l2_team": [0.5, 8.0, 128.0]}, DCSpec())
    a, b = select_dc(grid, folds, 2.5), select_dc(grid, folds, 2.5)
    assert a["chosen"] == b["chosen"] and set(a["chosen"]) == {"1x2", "over_under", "btts"} and a["status"] == "SELECTED"
    assert [t["candidate"] for t in a["table"]] == [n for n, _ in grid]
    assert all(t["mean_loss"] is not None and len(t["inner_folds"]) == 2 for t in a["table"])
    # strengths exist in the generating process, so the weakest ridge beats full pooling on the 1X2 loss
    assert a["chosen"]["1x2"] != "l2_team=128.0"


def test_select_dc_records_a_failing_candidate_and_never_chooses_it(monkeypatch):
    import src.league_models.nested as nested

    real = nested.RidgePoissonDC

    class Broken(real):
        def fit(self, rows, *a, **k):
            if self.spec.l2_team == 8.0:
                raise NotTrained("forced failure")
            return super().fit(rows, *a, **k)

    monkeypatch.setattr(nested, "RidgePoissonDC", Broken)
    rows = synth(seasons=(2018, 2019, 2020), per_season=200)
    folds, _ = inner_folds(rows, 3, 2, 50)
    sel = select_dc(dc_grid({"l2_team": [1.0, 8.0]}, DCSpec()), folds, 2.5)
    broken = [t for t in sel["table"] if t["candidate"].startswith("l2_team=8.0")][0]
    assert broken["mean_loss"] is None and broken["failures"] and set(sel["chosen"].values()) == {"l2_team=1.0"}


def test_matrix_losses_are_finite_for_a_trained_model():
    from src.league_models.stat_models import RidgePoissonDC

    rows = synth(seasons=(2019, 2020), per_season=200)
    mod = RidgePoissonDC(DCSpec(), "t").fit(rows[:300])
    ls = matrix_losses(mod.predict(rows[300:]), rows[300:], 2.5)
    assert set(ls) == {"1x2", "over_under", "btts"} and all(0.2 < v < 2.0 for v in ls.values())


# ------------------------------------------------------------------ per-league separate models
def test_each_competition_gets_its_own_model_fitted_only_on_its_own_matches():
    a = synth("ITA1", "ITA", seed=1)
    b = synth("ENG1", "ENG", seed=2, offset=10000)
    small = synth("TUR1", "TUR", seasons=(2020,), per_season=120, seed=3, offset=20000)
    train = sorted([*a, *b, *small], key=lambda r: (r.kickoff_utc, r.fixture_id))
    pl = PerLeagueDC(S4C, DCSpec(), 2.5, 3).fit(train)
    assert pl.info["TUR1"]["status"] == "LOCAL_INELIGIBLE" and "TUR1" not in {c for c, _ in pl.models}
    for c, prefix in (("ITA1", "ITA"), ("ENG1", "ENG")):
        assert pl.info[c]["status"] == "TRAINED"
        for t in ("1x2", "over_under", "btts"):
            assert all(team.startswith(prefix) for team in pl.models[(c, t)].teams)           # no team of another competition ever enters this model
    probe = [EvalRow("p1", "ITA1", "2022-23", utc(2022, 9, 1), "ITA_t1", "ITA_t2", 0), EvalRow("p2", "TUR1", "2022-23", utc(2022, 9, 1), "TUR_t1", "TUR_t2", 0)]
    pred, covered = pl.predict(probe, "1x2")
    assert covered.tolist() == [True, False] and pred.matrices[0].sum() == pytest.approx(1.0) and pred.matrices[1].sum() == 0.0      # the uncovered row is left to the caller's visible fallback
    ex = pl.export()
    assert set(ex) == {"ITA1", "ENG1"} and set(ex["ITA1"]) == {"1x2", "over_under", "btts"} and ex["ITA1"]["1x2"]["info"]["parameter_digest"]


def test_a_per_league_model_changes_when_only_another_league_changes_nothing():
    a = synth("ITA1", "ITA", seed=1)
    b = synth("ENG1", "ENG", seed=2, offset=10000)
    b2 = synth("ENG1", "ENG", seed=9, offset=10000)
    p1 = PerLeagueDC(S4C, DCSpec(), 2.5, 3).fit(sorted([*a, *b], key=lambda r: (r.kickoff_utc, r.fixture_id)))
    p2 = PerLeagueDC(S4C, DCSpec(), 2.5, 3).fit(sorted([*a, *b2], key=lambda r: (r.kickoff_utc, r.fixture_id)))
    assert p1.info["ITA1"]["per_target"]["1x2"]["parameter_digest"] == p2.info["ITA1"]["per_target"]["1x2"]["parameter_digest"]       # ITA1 is untouched by ENG1 data
    assert p1.info["ENG1"]["per_target"]["1x2"]["parameter_digest"] != p2.info["ENG1"]["per_target"]["1x2"]["parameter_digest"]


def test_hierarchical_model_learns_variance_components_per_target_and_reports_them():
    rows = sorted([*synth("ITA1", "ITA", seed=1), *synth("ENG1", "ENG", seed=2, offset=10000)], key=lambda r: (r.kickoff_utc, r.fixture_id))
    h = HierarchicalDC(S4C, DCSpec(), 2.5, 3, bridge=False).fit(rows)
    assert h.info["status"] == "TRAINED" and set(h.info["per_target"]) == {"1x2", "over_under", "btts"}
    vc = h.info["per_target"]["1x2"]["variance_components"]
    assert vc["team"] == pytest.approx(1.0 / float(h.info["per_target"]["1x2"]["chosen"].split("l2_team=")[1].split("|")[0]), rel=1e-5)
    p = h.predict(rows[-5:], "over_under")
    assert p.matrices.shape[0] == 5 and np.allclose(p.matrices.sum(axis=(1, 2)), 1.0)


def test_hierarchical_model_without_any_inner_fold_refuses_instead_of_guessing():
    rows = synth(seasons=(2020,), per_season=60)
    with pytest.raises(NotTrained):
        HierarchicalDC(S4C, DCSpec(), 2.5, 3, bridge=False).fit(rows)


# ------------------------------------------------------------------ exact score
def test_exact_score_metrics_are_hand_computable_and_count_scores_outside_the_grid():
    g = 4
    m = np.zeros((3, g, g))
    m[0, 1, 0] = 0.5; m[0, 0, 0] = 0.5          # noqa: E702
    m[1, 2, 2] = 1.0
    m[2, 0, 0] = 1.0
    rows = [EvalRow("a", "X", "s", utc(2020, 1, 1), "h", "a", 0, home_goals=1, away_goals=0), EvalRow("b", "X", "s", utc(2020, 1, 2), "h", "a", 1, home_goals=2, away_goals=2),
            EvalRow("c", "X", "s", utc(2020, 1, 3), "h", "a", 0, home_goals=6, away_goals=0)]
    r = exact_score_metrics(m, rows, 2)
    assert r["outside_grid"] == 1 and r["top1_hit_rate"] == pytest.approx(1 / 3) and r["top2_hit_rate"] == pytest.approx(2 / 3)
    assert r["log_score"] == pytest.approx((-np.log(0.5) + -np.log(1.0) + -np.log(1e-12)) / 3)


def test_the_constant_scoreline_baseline_is_a_smoothed_distribution():
    rows = synth(seasons=(2020,), per_season=100)
    c = constant_scoreline_matrices(rows, 4, 6)
    assert c.shape == (4, 6, 6) and c[0].sum() == pytest.approx(1.0) and (c > 0).all() and np.array_equal(c[0], c[3])


# ------------------------------------------------------------------ nested boosted-tree tuner
def test_nested_tuner_scores_trials_over_all_inner_folds_and_records_every_one():
    rng = np.random.default_rng(5)
    X = rng.normal(size=(900, 4))
    y = (X[:, 0] + rng.normal(0, 0.8, 900) > 0).astype(int)
    sets = [(X[:300], y[:300], X[300:500], y[300:500]), (X[:500], y[:500], X[500:700], y[500:700])]
    names = ["a", "b", "c", "d"]
    out = tune_nested(ML, "btts", sets, names, [], 7, 4, 2)
    again = tune_nested(ML, "btts", sets, names, [], 7, 4, 2)
    assert out["trials_run"] == 4 and out["inner_folds"] == 2 and all(t["inner_folds"] == 2 for t in out["trials"])
    assert out["best_params"] == again["best_params"] and out["best_mean_validation_log_loss"] == again["best_mean_validation_log_loss"]
    assert out["best_mean_validation_log_loss"] < 0.69


# ------------------------------------------------------------------ committed outputs
def test_the_committed_sprint_4c_outputs_are_consistent_and_never_claim_an_approval():
    import json

    d = ROOT / "docs" / "league_models"
    if not (d / "s4c_decisions.json").is_file():
        pytest.skip("Sprint 4c outputs are not present")
    dec = json.loads((d / "s4c_decisions.json").read_text(encoding="utf-8"))
    paired = json.loads((d / "s4c_paired.json").read_text(encoding="utf-8"))
    man = json.loads((d / "s4c_run_manifest.json").read_text(encoding="utf-8"))
    audit = json.loads((d / "s4c_feature_audit.json").read_text(encoding="utf-8"))
    nested = json.loads((d / "s4c_nested_selection.json").read_text(encoding="utf-8"))
    assert dec["comparisons"] == len(paired) and "NO candidate can be approved" in dec["rule"] and "not read" in man["holdout"]
    assert audit["perturbation_audit"]["violations"] == 0 and man["folds"] == ["2020-21", "2021-22", "2022-23", "2023-24", "2024-25"]
    assert all("timings" not in x for x in nested)                      # wall-clock numbers would make the committed file irreproducible
    assert all(p["verdict"] in ("SUPPORTED", "WORSE", "INCONCLUSIVE") for p in paired)
    for p in paired:                                                    # verdicts follow the interval rule exactly
        assert p["verdict"] == ("SUPPORTED" if p["ci95"][1] < 0 else ("WORSE" if p["ci95"][0] > 0 else "INCONCLUSIVE"))
    assert S4C["protocol_version"] == man["protocol"]


# ------------------------------------------------------------------ end to end on a synthetic audit (TEST_ONLY)
@pytest.fixture
def e2e(tmp_path):
    import shutil

    bench = (ROOT / "configs" / "league_models" / "benchmark.yaml").read_text(encoding="utf-8").replace("min_train_matches: 500", "min_train_matches: 50").replace("samples: 1000", "samples: 60")
    stat = (ROOT / "configs" / "league_models" / "stat_models.yaml").read_text(encoding="utf-8").replace("min_uefa_matches_total: 200", "min_uefa_matches_total: 100").replace("min_local_fit_matches: 100", "min_local_fit_matches: 40")
    ml = yaml.safe_load((ROOT / "configs" / "league_models" / "ml_models.yaml").read_text(encoding="utf-8"))
    ml["tuning"].update({"max_trials": 2, "n_startup_trials": 2, "max_rounds": 30, "early_stopping_rounds": 5})
    ml["features"].update({"min_prior_matches": 20})
    s4c = yaml.safe_load(yaml.safe_dump(S4C))
    s4c["nested"].update({"inner_folds": 2, "min_inner_fit_matches": 40})
    s4c["per_league_dc"].update({"min_train_matches": 120, "grid": {"l2_team": [1.0, 16.0], "half_life_days": [None]}})
    s4c["hierarchical_dc"]["grid"] = {"l2_team": [2.0, 32.0], "l2_country": [2.0], "half_life_days": [None]}
    s4c["per_league_gbm"].update({"min_train_matches": 120, "max_trials": 2, "n_startup_trials": 2})
    (tmp_path / "configs").mkdir()
    shutil.copy(ROOT / "configs" / "model.yaml", tmp_path / "configs" / "model.yaml")
    for name, text in (("bench.yaml", bench), ("stat.yaml", stat)):
        (tmp_path / name).write_text(text, encoding="utf-8")
    (tmp_path / "ml.yaml").write_text(yaml.safe_dump(ml), encoding="utf-8")
    (tmp_path / "s4c.yaml").write_text(yaml.safe_dump(s4c), encoding="utf-8")
    return tmp_path


def _run_s4c(root, **kw):
    from src.league_models import s4c_benchmark
    from tests.test_league_models_s3 import _synthetic_audit

    return s4c_benchmark.run(root, utc(2026, 10, 20), audit=_synthetic_audit(), bench_config_path=root / "bench.yaml", stat_config_path=root / "stat.yaml", ml_config_path=root / "ml.yaml",
                             s4c_config_path=root / "s4c.yaml", **kw)  # fmt: skip


def test_end_to_end_every_candidate_is_scored_on_identical_fixtures_and_nothing_is_approved(e2e):
    out = _run_s4c(e2e, docs_dir=e2e / "docs", export_models=True)
    names = {"B_dc_league", "production_global", "H_dc_hier", "P_dc_sep", "G_base", "G_extra", "P_gbm_sep"}
    for t in ("1x2", "over_under", "btts"):
        assert names <= set(out["analysis"]["metrics"][t])
        assert len({m["ALL"]["n"] for m in out["analysis"]["metrics"][t].values()}) == 1          # one fixture set for every model
    assert out["decisions"]["comparisons"] == len(out["analysis"]["paired"]) and "NO candidate can be approved" in out["decisions"]["rule"]
    assert out["extra_features"]["perturbation_audit"]["violations"] == 0 and "not read" in out["holdout"]
    for p in out["analysis"]["paired"]:
        assert p["verdict"] == ("SUPPORTED" if p["ci95"][1] < 0 else ("WORSE" if p["ci95"][0] > 0 else "INCONCLUSIVE"))
    ex = out["analysis"]["exact_score"]
    assert set(ex) == {"B_dc_league", "constant_scoreline", "H_dc_hier", "P_dc_sep"} and ex["B_dc_league"]["ALL"]["outside_grid"] == 0
    d = out["diagnostics"][-1]
    assert d["fallbacks"]["P_dc_sep_rows_covered_by_a_separate_model"]["eval_rows"] > 0 and d["per_league_export"] is not None
    assert all(f.is_file() for f in (e2e / "docs").glob("s4c_*.json")) and len(list((e2e / "docs").glob("s4c_*.json"))) == 7
    assert "timings" not in (e2e / "docs" / "s4c_nested_selection.json").read_text(encoding="utf-8")


def test_end_to_end_is_deterministic(e2e):
    a = _run_s4c(e2e, docs_dir=e2e / "a")
    b = _run_s4c(e2e, docs_dir=e2e / "b")
    for f in ("s4c_metrics.json", "s4c_paired.json", "s4c_nested_selection.json", "s4c_exact_score.json"):
        assert (e2e / "a" / f).read_bytes() == (e2e / "b" / f).read_bytes()
    assert a["decisions"] == b["decisions"]


def test_quick_mode_writes_nothing(e2e):
    from src.league_models import s4c_benchmark

    out = _run_s4c(e2e, docs_dir=e2e / "q", quick=True)
    assert not (e2e / "q").exists() and out["quick"] is True and len(out["folds"]) == 2
    assert s4c_benchmark.load_s4c()["_fingerprint"] and s4c_benchmark.group_masks([])["ALL"].shape == (0,)


# ------------------------------------------------------------------ Sprint 4d: time-varying team strength (random walk across seasons)
def test_the_random_walk_objective_has_a_correct_analytic_gradient_and_the_fit_converges():
    from scipy.optimize import approx_fprime

    from src.league_models.dynamic_dc import DynamicPoissonDC, DynSpec

    rows = synth(seasons=(2018, 2019, 2020), per_season=120)
    m = DynamicPoissonDC(DynSpec(l2_walk=6.0, l2_team=3.0)).fit(rows)
    assert m.diagnostics["converged"] and m.status == "TRAINED" and m.diagnostics["team_seasons"] == 3 * 8
    rng = np.random.default_rng(0)
    theta = rng.normal(0, 0.2, m._n)
    num = approx_fprime(theta, lambda t: m._objective(t)[0], 1e-6)
    assert np.allclose(m._objective(theta)[1], num, rtol=1e-4, atol=1e-4)


def test_a_stiff_walk_is_the_static_model_and_a_free_walk_follows_a_team_that_changes():
    from src.league_models.dynamic_dc import DynamicPoissonDC, DynSpec

    rng = np.random.default_rng(4)
    rows, i = [], 0
    for y in (2019, 2020, 2021):             # team 0 becomes much stronger in the last training season
        start = utc(y, 8, 20, 18)
        for k in range(240):
            h, a = rng.choice(6, 2, replace=False)
            sh = 1.2 if h == 0 and y == 2021 else 0.0
            sa = 1.2 if a == 0 and y == 2021 else 0.0
            gh, ga = int(rng.poisson(np.exp(0.3 + 0.2 + sh - sa))), int(rng.poisson(np.exp(0.3 + sa - sh)))
            kick = start + timedelta(hours=30 * k)
            rows.append(EvalRow(f"d{i}", "ITA1", season_of(kick), kick, f"ITA_t{h}", f"ITA_t{a}", 0 if gh > ga else (1 if gh == ga else 2), home_goals=gh, away_goals=ga))
            i += 1
    probe = [EvalRow("p", "ITA1", "2022-23", utc(2022, 9, 1), "ITA_t0", "ITA_t1", 1)]
    free = DynamicPoissonDC(DynSpec(l2_walk=0.5, l2_team=4.0)).fit(rows).predict(probe)
    stiff = DynamicPoissonDC(DynSpec(l2_walk=400.0, l2_team=4.0)).fit(rows).predict(probe)
    assert free.lam_home[0] > stiff.lam_home[0] + 0.15                                 # the free walk has moved to the new level, the stiff one averages over three seasons
    assert free.one_x_two()[0, 0] > stiff.one_x_two()[0, 0]


def test_the_dynamic_model_predicts_unseen_clubs_from_the_country_level_and_refuses_empty_data():
    from src.league_models.dynamic_dc import DynamicPoissonDC, DynSpec

    rows = synth(seasons=(2019, 2020, 2021), per_season=120)
    m = DynamicPoissonDC(DynSpec()).fit(rows)
    p = m.predict([EvalRow("u", "ITA1", "2022-23", utc(2022, 9, 1), "ITA_t1", "ITA_new", 1)])
    assert not p.seen_away[0] and p.seen_home[0] and p.matrices[0].sum() == pytest.approx(1.0)
    with pytest.raises(NotTrained):
        DynamicPoissonDC(DynSpec()).fit([])
    with pytest.raises(NotTrained):
        DynamicPoissonDC(DynSpec()).predict([])


def test_the_nested_selection_builds_the_model_class_from_the_spec_and_the_grid_accepts_the_walk_dimension():
    from src.league_models.dynamic_dc import DynamicPoissonDC, DynSpec
    from src.league_models.nested import make_model
    from src.league_models.stat_models import RidgePoissonDC

    assert isinstance(make_model(DynSpec(), "x"), DynamicPoissonDC) and isinstance(make_model(DCSpec(), "x"), RidgePoissonDC)
    g = dc_grid({"l2_team": [4.0], "l2_walk": [4.0, 64.0], "half_life_days": [None]}, DynSpec())
    assert [n for n, _ in g] == ["l2_team=4.0|l2_walk=4.0|half_life_days=none", "l2_team=4.0|l2_walk=64.0|half_life_days=none"] and dict(g)[g[1][0]].l2_walk == 64.0
    rows = synth(seasons=(2017, 2018, 2019, 2020), per_season=200)
    folds, _ = inner_folds(rows, 3, 2, 50)
    sel = select_dc(g, folds, 2.5)
    assert sel["status"] == "SELECTED" and set(sel["chosen"].values()) <= {n for n, _ in g}


def test_sprint_4d_experiment_compares_the_extensions_with_the_base_model_on_identical_matches(e2e):
    import yaml as _yaml

    from src.league_models import s4d_experiment
    from tests.test_league_models_s3 import _synthetic_audit

    s4c = _yaml.safe_load((e2e / "s4c.yaml").read_text(encoding="utf-8"))
    s4c["hierarchical_dc"]["grid"] = {"l2_team": [2.0, 32.0], "l2_country": [2.0], "half_life_days": [None]}
    s4c["hierarchical_extended"]["grid"] = {"l2_team": [2.0, 5000.0], "l2_country": [2.0], "half_life_days": [None]}
    s4c["hierarchical_dynamic"]["grid"] = {"l2_team": [8.0], "l2_country": [8.0], "l2_walk": [16.0, 256.0], "half_life_days": [None]}
    (e2e / "s4c_small.yaml").write_text(_yaml.safe_dump(s4c), encoding="utf-8")
    out = s4d_experiment.run(e2e, utc(2026, 10, 20), e2e / "docs", audit=_synthetic_audit(), bench_config_path=e2e / "bench.yaml", stat_config_path=e2e / "stat.yaml", s4c_config_path=e2e / "s4c_small.yaml")
    assert out["not_compared"] == {} and {p["candidate"] for p in out["paired"]} == {"H_ext", "H_dyn"} and {p["reference"] for p in out["paired"]} == {"H_base"}
    assert len({p["n"] for p in out["paired"] if p["group"] == "ALL"}) == 1 and "not read" in out["holdout"]
    for p in out["paired"]:
        assert p["verdict"] == ("SUPPORTED" if p["ci95"][1] < 0 else ("WORSE" if p["ci95"][0] > 0 else "INCONCLUSIVE"))
    assert set(out["selections_on_grid_edge"]) == {"H_base", "H_ext", "H_dyn"} and (e2e / "docs" / "s4d_extension.json").is_file()
    assert "wall_seconds" not in (e2e / "docs" / "s4d_extension.json").read_text(encoding="utf-8")        # the committed file carries no wall-clock number


def test_the_neutral_prediction_removes_the_home_advantage_and_the_default_is_unchanged():
    from src.league_models.stat_models import RidgePoissonDC

    rows = synth(seasons=(2019, 2020, 2021), per_season=150)
    m = RidgePoissonDC(DCSpec(competition_terms=True, home_dev=True), "t").fit(rows)
    pair = [EvalRow("p", "ITA1", "2022-23", utc(2022, 9, 1), "ITA_t1", "ITA_t2", 1)]
    mirror = [EvalRow("q", "ITA1", "2022-23", utc(2022, 9, 1), "ITA_t2", "ITA_t1", 1)]
    default, neutral, swapped = m.predict(pair), m.predict(pair, neutral=True), m.predict(mirror, neutral=True)
    assert np.array_equal(default.matrices, m.predict(pair, neutral=False).matrices)                      # the default path is the old one
    assert neutral.lam_home[0] < default.lam_home[0] and neutral.lam_away[0] > default.lam_away[0]        # half of the home advantage moves from the nominal home side to the other
    assert neutral.lam_home[0] == pytest.approx(swapped.lam_away[0]) and neutral.lam_away[0] == pytest.approx(swapped.lam_home[0])
    assert neutral.matrices[0].sum() == pytest.approx(1.0)
