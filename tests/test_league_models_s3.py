"""League-model programme, Sprint 3 (ADR 0065): ridge Dixon-Coles, time-aware Elo, UEFA bridge, candidates, decisions.

Tests marked TEST_ONLY use minimal synthetic data for logic; they are NOT verification of any real data. The real-data
checks read the repository's cached source files (skipped when the cache is absent, as in CI) and the committed outputs."""

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from src.evaluation.dataset import EvalRow
from src.league_models import splits, stat_benchmark
from src.league_models.identity import IdentityRow
from src.league_models.stat_models import (
    NOT_TRAINED,
    TRAINED,
    DCSpec,
    EloSpec,
    MatrixPrediction,
    NotTrained,
    RidgePoissonDC,
    TimeAwareElo,
    country_of,
    rest_features,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "league_models"
CACHE = ROOT / "artifacts" / "markets" / "_cache" / "openfootball"
needs_cache = pytest.mark.skipif(not CACHE.is_dir(), reason="cached openfootball source files are absent (CI)")
UEFA = frozenset({"UEFA_UCL", "UEFA_UEL", "UEFA_UECL"})


def utc(*a: int) -> datetime:
    return datetime(*a, tzinfo=UTC)


def _row(i, comp, season, kick, h, a, hg, ag) -> EvalRow:
    return EvalRow(f"f{i}", comp, season, kick, h, a, 0 if hg > ag else (1 if hg == ag else 2), home_goals=hg, away_goals=ag)


def _league(n_teams=8, n_matches=400, seed=3, prefix="ITA", comp="ITA1", season="2020-21", start=utc(2020, 9, 1, 18)):
    """TEST_ONLY: goals drawn from known strengths, so recovery can be checked. Not real matches."""
    rng = np.random.default_rng(seed)
    att = rng.normal(0, 0.25, n_teams)
    dfn = rng.normal(0, 0.25, n_teams)
    rows = []
    for i in range(n_matches):
        h, a = rng.choice(n_teams, 2, replace=False)
        lh, la = np.exp(0.2 + 0.25 + att[h] - dfn[a]), np.exp(0.2 + att[a] - dfn[h])
        rows.append(_row(i, comp, season, start + timedelta(days=i), f"{prefix}_t{h}", f"{prefix}_t{a}", int(rng.poisson(lh)), int(rng.poisson(la))))
    return rows, att, dfn


# ------------------------------------------------------------------ Poisson / Dixon-Coles mathematics
def test_dc_fit_is_converged_and_predictions_are_valid_distributions():
    rows, _, _ = _league()
    model = RidgePoissonDC(DCSpec(), "A").fit(rows[:300])
    assert model.status == TRAINED and model.diagnostics["converged"] is True
    pred = model.predict(rows[300:])
    assert (pred.lam_home > 0).all() and (pred.lam_away > 0).all() and np.isfinite(pred.matrices).all()
    assert (pred.matrices >= 0).all()
    assert np.allclose(pred.matrices.sum(axis=(1, 2)), 1.0, atol=1e-12)
    p = pred.one_x_two()
    assert np.allclose(p.sum(axis=1), 1.0, atol=1e-12) and (p >= 0).all()
    assert pred.tail_mass.max() < 0.01 and (pred.raw_sum > 0.9).all()  # tail mass and the pre-normalisation sum are reported


def test_one_matrix_gives_consistent_1x2_over_btts_and_exact_score():
    rows, _, _ = _league()
    pred = RidgePoissonDC(DCSpec(), "A").fit(rows[:300]).predict(rows[300:310])
    m = pred.matrices
    i, j = np.indices(m.shape[1:])
    assert np.allclose(pred.over(2.5) + m[:, (i + j) <= 2].sum(axis=1), 1.0)
    assert np.allclose(pred.btts(), 1.0 - m[:, 0, :].sum(axis=1) - m[:, :, 0].sum(axis=1) + m[:, 0, 0])
    assert np.allclose(pred.one_x_two()[:, 1], np.trace(m, axis1=1, axis2=2))
    assert pred.top_score()[0] == f"{np.unravel_index(m[0].argmax(), m[0].shape)[0]}-{np.unravel_index(m[0].argmax(), m[0].shape)[1]}"
    assert np.allclose(pred.p_score(0, 0), m[:, 0, 0])


def test_dc_recovers_the_ordering_of_true_strengths():
    rows, att, dfn = _league(n_teams=8, n_matches=900, seed=5)
    model = RidgePoissonDC(DCSpec(l2_team=0.5), "A").fit(rows)
    est = np.array([model.team_effects(f"ITA_t{t}")[0] - model.team_effects(f"ITA_t{t}")[1] for t in range(8)])
    assert np.corrcoef(est, att - dfn)[0, 1] > 0.8


def test_ridge_shrinks_strengths_toward_zero_and_a_huge_penalty_flattens_them():
    rows, _, _ = _league()
    weak = RidgePoissonDC(DCSpec(l2_team=0.1), "w").fit(rows)
    mid = RidgePoissonDC(DCSpec(l2_team=20.0), "m").fit(rows)
    strong = RidgePoissonDC(DCSpec(l2_team=20000.0), "s").fit(rows)
    spread = lambda m: float(np.std([m.team_effects(f"ITA_t{t}")[0] for t in range(8)]))  # noqa: E731
    assert spread(weak) > spread(mid) > spread(strong)  # monotone shrinkage
    assert spread(strong) < 0.1 * spread(weak) and spread(strong) < 0.01


def test_rho_stays_inside_the_positivity_interval_and_a_negative_tau_rejects_the_model():
    rows, _, _ = _league()
    model = RidgePoissonDC(DCSpec(), "A").fit(rows)
    lo, hi = model.diagnostics["rho_positivity_interval"]
    assert lo < model.rho < hi and lo < 0 < hi
    model.rho = 5.0  # forced outside any valid interval: tau(1,1) = 1 - rho < 0
    with pytest.raises(NotTrained, match="negative"):
        model.predict(rows[:5])


def test_dc_low_score_cells_are_the_only_cells_changed_by_rho():
    rows, _, _ = _league()
    with_rho = RidgePoissonDC(DCSpec(), "r").fit(rows)
    pred = with_rho.predict(rows[:20])
    plain = RidgePoissonDC(DCSpec(use_rho=False), "p").fit(rows).predict(rows[:20])
    ratio = pred.matrices / plain.matrices
    changed = np.abs(ratio - ratio[:, 5:6, 5:6]) > 1e-9  # cells whose ratio differs from the (uniform) far-cell ratio
    assert changed[:, :2, :2].any() and not changed[:, 2:, 2:].any() and not changed[:, :2, 2:].any() and not changed[:, 2:, :2].any()


def test_unseen_club_is_predicted_at_its_country_level_and_flagged():
    rows, _, _ = _league()
    model = RidgePoissonDC(DCSpec(country_levels=True, competition_terms=True), "B").fit(rows)
    new = _row(9999, "ITA1", "2021-22", utc(2021, 9, 1, 18), "ITA_never_seen", "ITA_t1", 1, 0)
    pred = model.predict([new])
    assert (not pred.seen_home[0]) and pred.seen_away[0] and np.isfinite(pred.matrices).all()


def test_country_levels_are_identified_only_by_cross_country_matches():
    ita, _, _ = _league(prefix="ITA", comp="ITA1", seed=1)
    eng, _, _ = _league(prefix="ENG", comp="ENG1", seed=2)
    rng = np.random.default_rng(9)
    cross = []
    for i in range(300):  # ENG clubs are clearly stronger in the cross-country (UEFA) matches
        h, a = rng.integers(0, 8, 2)
        cross.append(_row(5000 + i, "UEFA_UCL", "2020-21", utc(2021, 1, 1, 20) + timedelta(days=i), f"ENG_t{h}", f"ITA_t{a}", int(rng.poisson(2.2)), int(rng.poisson(0.7))))
    spec = DCSpec(country_levels=True, competition_terms=True, l2_country=0.5)
    sep = RidgePoissonDC(spec, "x").fit(ita + eng)  # no cross matches: nothing identifies the levels, ridge pins them near 0
    bridged = RidgePoissonDC(spec, "y").fit(ita + eng + cross)
    lvl = lambda m, c: float(m.theta[m._sl["alpha"]][m.c_idx[c]] + m.theta[m._sl["delta"]][m.c_idx[c]])  # noqa: E731
    assert abs(lvl(sep, "ENG") - lvl(sep, "ITA")) < 0.2
    assert lvl(bridged, "ENG") - lvl(bridged, "ITA") > 0.3


def test_dc_refuses_to_train_without_goals_or_rows():
    rows, _, _ = _league()
    with pytest.raises(NotTrained):
        RidgePoissonDC().fit([])
    broken = [EvalRow("x", "ITA1", "2020-21", utc(2020, 9, 1), "ITA_a", "ITA_b", 0)] + rows[:5]
    with pytest.raises(NotTrained, match="no goals"):
        RidgePoissonDC().fit(broken)
    with pytest.raises(NotTrained):
        RidgePoissonDC().predict(rows[:2])  # predict before a successful fit: status is NOT_TRAINED


def test_time_decay_and_rest_covariate_fit_and_use_rest_features_only_from_history():
    rows, _, _ = _league()
    rest = rest_features(rows, 14, 7)
    for spec in (DCSpec(half_life_days=200.0), DCSpec(use_rest=True)):
        m = RidgePoissonDC(spec, "z").fit(rows[:300], rest=rest)
        assert m.diagnostics["converged"] and np.isfinite(m.predict(rows[300:305], rest=rest).matrices).all()
    with pytest.raises(NotTrained, match="rest"):
        RidgePoissonDC(DCSpec(use_rest=True)).fit(rows[:300])


def test_rest_features_first_match_cap_and_same_kickoff_isolation():
    k1, k2 = utc(2021, 8, 1, 15), utc(2021, 8, 1, 15) + timedelta(days=30)
    rows = [
        _row(1, "ITA1", "2021-22", k1, "A", "B", 1, 0),
        _row(2, "ITA1", "2021-22", k1, "C", "D", 1, 1),
        _row(3, "ITA1", "2021-22", k1 + timedelta(days=3), "A", "C", 0, 0),
        _row(4, "ITA1", "2021-22", k2, "A", "D", 2, 2),
    ]
    f = rest_features(rows, cap=14.0, reference=7.0)
    assert f["f1"] == (0.0, 0.0) and f["f2"] == (0.0, 0.0)  # no history, and the other fixture of the same kick-off is not history
    ref = np.log1p(7.0)
    assert f["f3"][0] == pytest.approx(np.log1p(3.0) - ref)  # A last played 3 days earlier
    assert f["f4"][0] == pytest.approx(np.log1p(14.0) - ref)  # 27 days, capped at 14


def test_matrix_prediction_mixture_and_take_keep_a_valid_distribution():
    rows, _, _ = _league()
    a = RidgePoissonDC(DCSpec(), "a").fit(rows[:300]).predict(rows[300:310])
    b = RidgePoissonDC(DCSpec(l2_team=50.0), "b").fit(rows[:300]).predict(rows[300:310])
    mix = a.mix(b, np.linspace(0, 1, 10))
    assert np.allclose(mix.matrices.sum(axis=(1, 2)), 1.0) and np.allclose(mix.matrices[0], a.matrices[0]) and np.allclose(mix.matrices[-1], b.matrices[-1])
    assert isinstance(a.take(np.array([0, 2])), MatrixPrediction) and a.take(np.array([0, 2])).matrices.shape[0] == 2


# ------------------------------------------------------------------ time-aware Elo
def _elo_rows():
    rows, _, _ = _league(n_teams=8, n_matches=300, seed=7)
    return rows


def test_elo_predictions_are_valid_probabilities_and_deterministic():
    rows = _elo_rows()
    a = TimeAwareElo(EloSpec(), "e").fit(rows[:200])
    pa, ua = a.predict_proba(rows[200:])
    b = TimeAwareElo(EloSpec(), "e").fit(rows[:200])
    pb, _ = b.predict_proba(rows[200:])
    assert a.status == TRAINED and np.allclose(pa.sum(axis=1), 1.0) and (pa >= 0).all() and np.array_equal(pa, pb)
    assert len(ua) == 100 and a.diagnostics["mapping_optimizer"]["mean_abs_gradient"] < 1e-5


def test_elo_season_change_gives_back_part_of_the_distance_to_the_country_mean():
    spec = EloSpec(season_reversion=0.5, k_factor=40.0)
    e = TimeAwareElo(spec, "e")
    first = [_row(i, "ITA1", "2020-21", utc(2020, 9, 1) + timedelta(days=i), "ITA_a", f"ITA_b{i % 3}", 3, 0) for i in range(8)]
    e.replay(first)
    mean, before = e._country_mean("ITA"), e.state.ratings["ITA_a"]
    assert before > mean
    e.replay([_row(99, "ITA1", "2021-22", utc(2021, 9, 1), "ITA_a", "ITA_b0", 0, 0)], update=False)
    assert e.state.ratings["ITA_a"] < before  # reversion applied at the first match of the new season


def test_elo_promoted_club_enters_below_the_country_mean_and_is_flagged_uncertain():
    e = TimeAwareElo(EloSpec(promoted_penalty=50.0, min_matches_certain=10), "e")
    e.replay([_row(i, "ITA1", "2020-21", utc(2020, 9, 1) + timedelta(days=i), "ITA_a", "ITA_b", 1, 0) for i in range(5)])
    mean = e._country_mean("ITA")
    _, unc = e.replay([_row(50, "ITA1", "2021-22", utc(2021, 9, 1), "ITA_new", "ITA_a", 0, 0)], update=False)
    assert e.state.ratings["ITA_new"] == pytest.approx(mean - 50.0) and unc == [True]


def test_elo_uefa_ties_move_country_offsets_only_across_countries():
    e = TimeAwareElo(EloSpec(), "e", UEFA)
    e.replay([_row(1, "UEFA_UCL", "2020-21", utc(2020, 10, 1), "ENG_a", "ITA_b", 3, 0)])
    assert e.state.offsets["ENG"] > 0 > e.state.offsets["ITA"]
    e2 = TimeAwareElo(EloSpec(), "e", UEFA)
    e2.replay([_row(1, "UEFA_UCL", "2020-21", utc(2020, 10, 1), "ENG_a", "ENG_b", 3, 0)])
    assert e2.state.offsets == {}  # a same-country tie says nothing about the country levels
    local = TimeAwareElo(EloSpec(bridge=False), "l", frozenset())
    local.replay([_row(1, "ITA1", "2020-21", utc(2020, 10, 1), "ITA_a", "ITA_b", 1, 0)])
    assert local.state.offsets == {}


def test_elo_same_kickoff_fixtures_are_all_predicted_before_any_is_applied():
    k = utc(2021, 5, 1, 15)
    rows = [_row(1, "ITA1", "2020-21", k, "ITA_a", "ITA_b", 4, 0), _row(2, "ITA1", "2020-21", k, "ITA_c", "ITA_d", 0, 4)]
    e = TimeAwareElo(EloSpec(), "e")
    diffs, _ = e.replay(rows)
    assert diffs[0] == pytest.approx(diffs[1])  # both start from the same state: neither sees the other's result
    assert e.state.ratings["ITA_a"] > e.state.ratings["ITA_b"]


def test_elo_refuses_to_train_on_nothing_and_to_predict_untrained():
    with pytest.raises(NotTrained):
        TimeAwareElo(EloSpec(), "e").fit([])
    with pytest.raises(NotTrained):
        TimeAwareElo(EloSpec(), "e").predict_proba(_elo_rows()[:3])


def test_country_prefix():
    assert country_of("ENG_arsenal") == "ENG" and country_of("UNKNOWN") == "UNKNOWN"


# ------------------------------------------------------------------ end to end on a synthetic audit (TEST_ONLY)
SEASONS = ["2019-20", "2020-21", "2021-22", "2022-23", "2023-24", "2024-25", "2025-26", "2026-27"]


def strength(name: str) -> float:
    return ((sum(map(ord, name)) % 7) - 3) * 0.3


def _synthetic_audit():
    """TEST_ONLY: a domestic league plus a UEFA competition with clubs of three countries. Not real data."""
    rng = np.random.default_rng(21)
    rows, cov = [], []
    for comp in ("ITA1", "UEFA_UCL"):
        for si, season in enumerate(SEASONS):
            cov.append(SimpleNamespace(canonical_league_id=comp, season=season, training_status="TRAINABLE_COMPLETE", source_status="OK"))
            start = utc(2019 + si, 8, 20, 18)
            for i in range(110 if comp == "ITA1" else 60):
                if comp == "ITA1":
                    h, a = rng.choice(8, 2, replace=False)
                    hn, an = f"ITA_t{h}", f"ITA_t{a}"
                else:
                    ca, cb = rng.choice(["ITA", "ENG", "ESP"], 2, replace=False)
                    hn, an = f"{ca}_t{rng.integers(0, 4)}", f"{cb}_t{rng.integers(0, 4)}"
                gap = strength(hn) - strength(an)  # real signal in the goals, so the Elo outcome mapping has a positive slope
                score = (int(rng.poisson(np.exp(0.4 + 0.5 * gap))), int(rng.poisson(np.exp(0.1 - 0.5 * gap))))
                rows.append(IdentityRow(f"{comp}-{season}-{i:03d}", "openfootball_domestic", f"p{si}{i}", comp, season, hn, an,
                                        start + timedelta(days=3 * i, hours=0 if comp == "ITA1" else 36), True, "FINISHED",
                                        score, None, "x.json"))  # fmt: skip
    return SimpleNamespace(coverage=cov, identity_rows=rows, source_files=[SimpleNamespace(sha256="a" * 64)])


@pytest.fixture
def e2e(tmp_path):
    bench = (ROOT / "configs" / "league_models" / "benchmark.yaml").read_text(encoding="utf-8")
    bench = bench.replace("min_train_matches: 500", "min_train_matches: 50").replace("samples: 1000", "samples: 60")
    stat = (ROOT / "configs" / "league_models" / "stat_models.yaml").read_text(encoding="utf-8")
    stat = stat.replace("min_uefa_matches_total: 200", "min_uefa_matches_total: 100").replace("min_local_fit_matches: 100", "min_local_fit_matches: 40")
    stat = stat.replace("min_validation_matches: 60", "min_validation_matches: 20")
    (tmp_path / "configs").mkdir()
    shutil.copy(ROOT / "configs" / "model.yaml", tmp_path / "configs" / "model.yaml")
    (tmp_path / "bench.yaml").write_text(bench, encoding="utf-8")
    (tmp_path / "stat.yaml").write_text(stat, encoding="utf-8")
    return tmp_path


def _run(root, out, **kw):
    return stat_benchmark.run(root, utc(2026, 10, 20), out, bench_config_path=root / "bench.yaml", stat_config_path=root / "stat.yaml",
                              audit=_synthetic_audit(), **kw)  # fmt: skip


def test_end_to_end_outputs_decisions_hypotheses_and_matrix_validity(e2e):
    out = e2e / "out"
    res = _run(e2e, out, docs_dir=e2e / "docs")
    assert res["registry_entry"]["status"] == "REGISTERED"
    from src.league_models.benchmark import verify_run

    assert verify_run(out)["ok"] is True
    metrics = json.loads((out / "stat_metrics.json").read_text(encoding="utf-8"))
    ns = {v["n"] for k, v in metrics["metrics"]["1x2"]["ALL"].items() if k != "_label_rate"}
    assert len(ns) == 1  # every candidate and reference scored the identical fixtures
    assert {"A_dc", "B_dc_league", "C_dc_local", "D_blend", "E_elo_bridge", "F_elo_local", "elo_s2"} <= set(metrics["metrics"]["1x2"]["ALL"])
    dec = json.loads((out / "league_decisions.json").read_text(encoding="utf-8"))["competitions"]
    assert set(dec) == {"ITA1", "UEFA_UCL"} and all(v["decision"].startswith(("GLOBAL", "CANDIDATE")) for v in dec.values())
    hyp = {h["id"]: h for h in json.loads((out / "hypotheses.json").read_text(encoding="utf-8"))["hypotheses"]}
    assert hyp["H7_super_lig_squad_continuity"]["verdict"] == "NOT_TESTABLE" and hyp["H6_super_lig_home_away_gap"]["verdict"] == "NO_EVAL_ROWS"
    assert hyp["H1_serie_a_low_scoring_draws"]["verdict"] in ("SUPPORTED", "NOT_SUPPORTED", "INCONCLUSIVE")
    audit = json.loads((out / "score_audit.json").read_text(encoding="utf-8"))
    for v in audit["matrix_validity"].values():
        assert v["min_cell_probability"] >= 0 and v["max_abs_matrix_sum_deviation_after_normalisation"] < 1e-9
    assert audit["by_model"]["A_dc"]["ALL"]["p_0_0_observed"] >= 0
    uefa = json.loads((out / "uefa_bridge.json").read_text(encoding="utf-8"))
    assert any(f["status"] == "AVAILABLE" for f in uefa["folds"].values())
    assert (e2e / "docs" / "s3_model_cards.md").is_file() and (out / "fold_predictions.jsonl").stat().st_size > 0


def test_end_to_end_is_deterministic_and_dry_run_writes_nothing(e2e):
    a = _run(e2e, e2e / "a")["run_manifest"]["output_hashes"]
    b = _run(e2e, e2e / "b")["run_manifest"]["output_hashes"]
    assert a == b
    plan = _run(e2e, None, dry_run=True)["plan"]
    assert plan["dry_run"] is True and len(plan["folds"]) == 5 and all(v == 0 for v in plan["replay_violations_per_fold"].values())


def test_predictions_are_immutable_records_with_provenance_labels(e2e):
    out = e2e / "p"
    _run(e2e, out, eval_start="2024-25")
    recs = [json.loads(x) for x in (out / "fold_predictions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {r["track"] for r in recs} == {"historical_backtest"} and {r["as_of_quality"] for r in recs} == {"BACKTEST_LIMITED"}
    assert not any(r["season"] in ("2025-26", "2026-27") for r in recs)  # frozen seasons never predicted
    matrix = [r for r in recs if r["model"] == "A_dc"][0]
    assert abs(sum(matrix["p_1x2"]) - 1) < 1e-6 and 0 <= matrix["p_over"] <= 1 and "top_score" in matrix


def test_unconverged_or_failed_candidates_fall_back_explicitly_never_silently(e2e, monkeypatch):
    real_fit = stat_benchmark.RidgePoissonDC.fit

    def failing(self, rows, cutoff=None, rest=None):
        if self.model_id.startswith("C_ITA1"):
            raise NotTrained("forced failure (TEST_ONLY)")
        return real_fit(self, rows, cutoff=cutoff, rest=rest)

    monkeypatch.setattr(stat_benchmark.RidgePoissonDC, "fit", failing)
    out = e2e / "f"
    _run(e2e, out, eval_start="2024-25")
    health = json.loads((out / "stat_metrics.json").read_text(encoding="utf-8"))["fit_health"]["2024-25"]
    assert health["local_dc"]["ITA1"] == NOT_TRAINED and "C_dc_local:ITA1" in health["fallbacks"] and health["blend_weights"]["ITA1"] == 0.0
    dec = json.loads((out / "league_decisions.json").read_text(encoding="utf-8"))["competitions"]["ITA1"]
    assert "a fold fell back" in " ".join(dec["candidates_vs_A_dc"]["C_dc_local"]["blocked_by"])


def test_reference_failure_is_fail_closed(e2e, monkeypatch):
    def failing(self, rows, cutoff=None, rest=None):
        raise NotTrained("forced (TEST_ONLY)")

    monkeypatch.setattr(stat_benchmark.RidgePoissonDC, "fit", failing)
    with pytest.raises(SystemExit, match="A_dc did not train"):
        _run(e2e, None, eval_start="2024-25")


def test_folds_must_match_sprint2_or_the_run_refuses(e2e):
    (e2e / "docs" / "league_models").mkdir(parents=True)
    proto = {"folds": [{"eval_season": "2020-21", "train_matches": 1}]}
    (e2e / "docs" / "league_models" / "s2_protocol_manifest.json").write_text(json.dumps(proto), encoding="utf-8")
    with pytest.raises(SystemExit, match="differ from the committed Sprint 2 folds"):
        _run(e2e, None, dry_run=True)


def test_stat_compare_to_committed_tolerates_noise_and_flags_real_changes(e2e):
    out, docs = e2e / "c", e2e / "docs_copy"
    _run(e2e, out)
    docs.mkdir()
    for name in (*stat_benchmark.EXACT_FILES, *stat_benchmark.NUMERIC_FILES):
        shutil.copy(out / name, docs / f"s3_{name}")
    assert stat_benchmark.compare_to_committed(out, docs, 1e-3)["ok"] is True
    committed = json.loads((docs / "s3_stat_metrics.json").read_text(encoding="utf-8"))
    committed["metrics"]["1x2"]["ALL"]["A_dc"]["log_loss"] += 5e-5
    (docs / "s3_stat_metrics.json").write_text(json.dumps(committed), encoding="utf-8")
    assert stat_benchmark.compare_to_committed(out, docs, 1e-3)["ok"] is True
    committed["metrics"]["1x2"]["ALL"]["A_dc"]["log_loss"] += 0.05
    (docs / "s3_stat_metrics.json").write_text(json.dumps(committed), encoding="utf-8")
    assert stat_benchmark.compare_to_committed(out, docs, 1e-3)["ok"] is False
    (docs / "s3_league_decisions.json").write_text("{}", encoding="utf-8")
    assert "league_decisions.json" in stat_benchmark.compare_to_committed(out, docs, 1e-3)["exact_files_differing"]


def test_cli_verify_and_licence_gate(e2e, monkeypatch):
    out = e2e / "v"
    _run(e2e, out, eval_start="2024-25")
    assert stat_benchmark.main(["--root", str(e2e), "--verify", "v"]) == 0
    (out / "league_decisions.json").write_text("{}", encoding="utf-8")
    assert stat_benchmark.main(["--root", str(e2e), "--verify", "v"]) == 1
    monkeypatch.setattr(stat_benchmark, "LICENSED_PROVIDERS", ["openfootball_domestic", "football_data_co_uk"])
    from src.league_models.licenses import LicenseGateError

    with pytest.raises(LicenseGateError):
        _run(e2e, None, dry_run=True)
    with pytest.raises(SystemExit):
        stat_benchmark.main(["--as-of", "2026-10-20T00:00:00", "--dry-run"])


def test_blend_weight_is_zero_below_the_validation_minimum_and_shrunk_toward_zero(e2e):
    scfg = stat_benchmark.load_stat_config(e2e / "stat.yaml")
    bcfg = splits.load_bench_config(e2e / "bench.yaml")
    uni = splits.build_universe(bcfg, _synthetic_audit())
    fold = splits.make_folds(bcfg, uni, 3)[-1]
    w, info = stat_benchmark.learn_blend_weights(scfg, fold, {}, ["ITA1", "UEFA_UCL", "NOPE"])
    assert info["status"] == "LEARNED" and w["NOPE"] == 0.0
    for c in ("ITA1", "UEFA_UCL"):
        e = info["per_competition"][c]
        if "best_raw_weight" in e:
            n = e["validation_matches"]
            assert e["weight"] == pytest.approx(e["best_raw_weight"] * n / (n + scfg.raw["blend"]["shrink_k"])) and e["weight"] <= e["best_raw_weight"]


# ------------------------------------------------------------------ real cached data and committed outputs
@needs_cache
def test_real_dry_run_uses_the_sprint2_folds_without_replay_violations():
    plan = stat_benchmark.run(ROOT, utc(2026, 10, 20), None, dry_run=True)["plan"]
    assert plan["sprint2_fold_parity"]["status"] == "IDENTICAL_TO_SPRINT_2"
    assert [f["eval_season"] for f in plan["folds"]] == ["2020-21", "2021-22", "2022-23", "2023-24", "2024-25"]
    assert all(v == 0 for v in plan["replay_violations_per_fold"].values())


def test_committed_sprint3_outputs_are_internally_consistent():
    manifest = OUT / "s3_run_manifest.json"
    if not manifest.is_file():
        pytest.skip("Sprint 3 outputs not generated")
    import hashlib

    run = json.loads(manifest.read_text(encoding="utf-8"))
    for name, digest in run["output_hashes"].items():
        committed = OUT / f"s3_{name}"
        if committed.is_file():
            assert hashlib.sha256(committed.read_bytes()).hexdigest() == digest, name
    dec = json.loads((OUT / "s3_league_decisions.json").read_text(encoding="utf-8"))["competitions"]
    assert set(dec) == {"ENG1", "ESP1", "FRA1", "GER1", "ITA1", "TUR1", "UEFA_UCL", "UEFA_UEL", "UEFA_UECL"}  # a decision for each of the nine
    metrics = json.loads((OUT / "s3_stat_metrics.json").read_text(encoding="utf-8"))
    assert metrics["no_single_winner"] is True and metrics["reference_model"] == "A_dc"
    s2 = json.loads((OUT / "s2_protocol_manifest.json").read_text(encoding="utf-8"))
    s3 = json.loads((OUT / "s3_protocol_manifest.json").read_text(encoding="utf-8"))
    assert s3["folds"] == s2["folds"] and s3["sprint2_fold_parity"]["status"] == "IDENTICAL_TO_SPRINT_2"
