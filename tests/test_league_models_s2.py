"""League-model programme, Sprint 2 (ADR 0064): common universe, folds, frozen holdout, metrics, bootstrap, claims.

Tests marked TEST_ONLY use minimal synthetic data for logic; they are NOT verification of any real data. The real-data
checks read the repository's cached source files (skipped when the cache is absent, as in CI) and the committed outputs."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from src.evaluation.dataset import EvalRow
from src.league_models import bench_metrics as bm
from src.league_models import benchmark, claims, splits
from src.league_models.identity import IdentityRow
from src.league_models.licenses import LicenseGateError, load_manifest, training_gate
from src.snapshot.stages import SnapshotStage

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "league_models"
CFG = splits.load_bench_config()
CACHE = ROOT / "artifacts" / "markets" / "_cache" / "openfootball"
needs_cache = pytest.mark.skipif(
    not CACHE.is_dir(), reason="cached openfootball source files are absent (CI)"
)


def utc(*a: int) -> datetime:
    return datetime(*a, tzinfo=UTC)


# ------------------------------------------------------------------ binary metrics (TEST_ONLY values)
def test_binary_log_loss_brier_and_clipping_are_exact():
    p, y = np.array([0.9, 0.2, 0.5]), np.array([1, 0, 1])
    assert bm.binary_log_loss_rows(p, y).mean() == pytest.approx(
        -(np.log(0.9) + np.log(0.8) + np.log(0.5)) / 3
    )
    assert bm.binary_brier_rows(p, y).mean() == pytest.approx((0.01 + 0.04 + 0.25) / 3)
    degenerate = bm.binary_log_loss_rows(
        np.array([1.0]), np.array([0])
    )  # p=1 on a miss: finite through the clip
    assert np.isfinite(degenerate).all() and degenerate[0] == pytest.approx(-np.log(bm.EPS))


def test_binary_accuracy_gives_fractional_credit_on_exact_half():
    assert bm.binary_accuracy(np.array([0.5, 0.5]), np.array([1, 0])) == pytest.approx(0.5)
    assert bm.binary_accuracy(np.array([0.7, 0.3]), np.array([1, 0])) == 1.0


def test_auc_is_defined_only_with_two_classes_and_handles_ties():
    assert bm.binary_auc(np.array([0.2, 0.9]), np.array([1, 1])) is None  # single class: undefined, not 0.5
    assert bm.binary_auc(np.array([0.1, 0.4, 0.35, 0.8]), np.array([0, 0, 1, 1])) == pytest.approx(0.75)
    assert bm.binary_auc(np.array([0.5, 0.5, 0.5, 0.5]), np.array([0, 1, 0, 1])) == pytest.approx(0.5)


def test_binary_metrics_reject_bad_input():
    for p, y in (
        (np.array([1.2]), np.array([1])),
        (np.array([np.nan]), np.array([1])),
        (np.array([0.5]), np.array([2])),
    ):
        with pytest.raises(ValueError):
            bm.binary_log_loss_rows(p, y)
    with pytest.raises(ValueError):
        bm.binary_log_loss_rows(np.array([]), np.array([]))  # empty is an error, never a silent 0


def test_binary_ece_is_zero_for_perfectly_calibrated_bins_and_positive_otherwise():
    p = np.array([0.8] * 10)
    assert bm.binary_ece(p, np.array([1] * 8 + [0] * 2)) == pytest.approx(0.0, abs=1e-9)
    assert bm.binary_ece(p, np.array([0] * 10)) == pytest.approx(0.8)


def test_reliability_bins_partition_all_rows():
    p = np.linspace(0.01, 0.99, 50)
    y = (p > 0.5).astype(int)
    assert sum(b["n"] for b in bm.reliability_bins(p, y)) == 50
    probs = np.tile([0.5, 0.3, 0.2], (7, 1))
    assert sum(b["n"] for b in bm.reliability_bins_1x2(probs, np.zeros(7, dtype=int))) == 7


def test_1x2_row_losses_match_the_shared_metric_engine():
    from src.evaluation import metrics as m

    rng = np.random.default_rng(1)
    p = rng.dirichlet([2, 1, 2], 40)
    y = rng.integers(0, 3, 40)
    assert bm.rows_log_loss_1x2(p, y).mean() == pytest.approx(m.log_loss(p, y))
    assert bm.rows_brier_1x2(p, y).mean() == pytest.approx(m.brier(p, y))
    assert bm.rows_rps_1x2(p, y).mean() == pytest.approx(m.rps(p, y))


def test_normalise_rejects_nan_and_rescales_rows():
    with pytest.raises(ValueError):
        benchmark._normalise(np.array([[np.nan, 0.5, 0.5]]))
    out = benchmark._normalise(np.array([[2.0, 1.0, 1.0]]))
    assert out.sum() == pytest.approx(1.0)


# ------------------------------------------------------------------ date-block bootstrap
def test_iso_week_block_groups_a_week_and_splits_the_next():
    assert bm.iso_week_block(utc(2024, 3, 4)) == bm.iso_week_block(
        utc(2024, 3, 10)
    )  # Mon and Sun of one week
    assert bm.iso_week_block(utc(2024, 3, 10)) != bm.iso_week_block(utc(2024, 3, 11))


def test_block_bootstrap_is_deterministic_and_seed_sensitive():
    rng = np.random.default_rng(3)
    loss = rng.random(200)
    blocks = [f"b{i // 10}" for i in range(200)]
    a = bm.block_bootstrap_mean(loss, blocks, 300, 7)
    b = bm.block_bootstrap_mean(loss, blocks, 300, 7)
    c = bm.block_bootstrap_mean(loss, blocks, 300, 8)
    assert a == b and a != c
    assert a.lower <= a.mean <= a.upper and a.n_blocks == 20


def test_paired_delta_is_zero_for_identical_models_and_signed_otherwise():
    loss = np.random.default_rng(4).random(120)
    blocks = [f"b{i // 6}" for i in range(120)]
    same = bm.paired_block_bootstrap(loss, loss, blocks, 200, 1)
    assert same.delta == 0.0 and same.lower == 0.0 and same.upper == 0.0
    better = bm.paired_block_bootstrap(loss - 0.2, loss, blocks, 200, 1)
    assert better.delta == pytest.approx(-0.2) and better.upper < 0 and better.share_below_zero == 1.0


def test_paired_bootstrap_rejects_misaligned_inputs():
    with pytest.raises(ValueError):
        bm.paired_block_bootstrap(np.ones(3), np.ones(4), ["a"] * 3, 10, 1)


# ------------------------------------------------------------------ roles, holdout, folds
def test_season_role_precedence_holdout_first_then_blocked_reserved_guard_dev():
    r = lambda comp, season, ok=True: splits.season_role(CFG, comp, season, ok)  # noqa: E731
    assert r("ENG1", "2025-26") == splits.HOLDOUT  # ADR 0063 decision 2
    assert r("TUR1", "2024-25") == splits.HOLDOUT  # Süper Lig override
    assert r("TUR1", "2025-26", ok=False) == splits.BLOCKED  # blocked source: never a holdout
    assert r("ENG1", "2026-27") == splits.RESERVED
    assert (
        r("ENG1", "2024-25") == splits.GUARD and r("ESP1", "2024-25") == splits.GUARD
    )  # global benchmark FINAL seasons
    assert r("ITA1", "2024-25") == splits.DEV  # the guard concerns EPL and La Liga only
    assert r("ENG1", "2021-22") == splits.DEV and r("ENG1", "2021-22", ok=False) == splits.BLOCKED


def _synthetic_rows(
    seasons=("2019-20", "2020-21", "2021-22", "2022-23"), teams=8, per=24, seed=0
) -> list[EvalRow]:
    """TEST_ONLY: deterministic fixtures, one league. Not real matches."""
    rng = np.random.default_rng(seed)
    rows = []
    for si, season in enumerate(seasons):
        start = utc(2019 + si, 8, 10, 15)
        for i in range(per):
            h, a = rng.choice(teams, 2, replace=False)
            hg, ag = int(rng.poisson(1.5)), int(rng.poisson(1.1))
            rows.append(
                EvalRow(
                    f"{season}-{i:03d}",
                    "ENG1",
                    season,
                    start + timedelta(days=3 * i),
                    f"T{h}",
                    f"T{a}",
                    0 if hg > ag else (1 if hg == ag else 2),
                    home_goals=hg,
                    away_goals=ag,
                )  # fmt: skip
            )
    return rows


def _universe(rows, **kw) -> splits.Universe:
    return splits.Universe(
        tuple(rows), kw.get("holdout", ()), kw.get("reserved", ()), kw.get("guard", ()), {}, {}
    )


def test_folds_are_expanding_chronological_and_disjoint():
    cfg = splits.load_bench_config()
    object.__setattr__(cfg, "min_train_matches", 10)
    uni = _universe(_synthetic_rows())
    folds = splits.make_folds(cfg, uni, lag_hours=3)
    assert [f.eval_season for f in folds] == ["2020-21", "2021-22", "2022-23"]
    assert [len(f.train_seasons) for f in folds] == [1, 2, 3]
    splits.assert_disjoint(folds, uni)
    for f in folds:
        assert all(r.kickoff_utc < f.fit_cutoff_utc for r in f.train_rows)
        assert all(r.season == f.eval_season for r in f.eval_rows)


def test_embargo_drops_train_matches_not_yet_available_at_the_cutoff():
    cfg = splits.load_bench_config()
    object.__setattr__(cfg, "min_train_matches", 1)
    late = EvalRow("late", "ENG1", "2019-20", utc(2020, 8, 10, 12), "T1", "T2", 0, home_goals=1, away_goals=0)
    first_eval = EvalRow(
        "e0", "ENG1", "2020-21", utc(2020, 8, 10, 14), "T3", "T4", 1, home_goals=0, away_goals=0
    )
    folds = splits.make_folds(cfg, _universe([late, first_eval]), lag_hours=3)
    assert (
        folds == []
    )  # `late` is only available at 15:00 > 14:00 cutoff: no train row is left, so the fold is refused
    ok = EvalRow("ok", "ENG1", "2019-20", utc(2020, 8, 10, 10), "T1", "T2", 0, home_goals=1, away_goals=0)
    assert [
        r.fixture_id for r in splits.make_folds(cfg, _universe([ok, late, first_eval]), 3)[0].train_rows
    ] == ["ok"]


def test_fold_is_refused_when_training_history_is_too_small():
    cfg = splits.load_bench_config()
    assert (
        splits.make_folds(cfg, _universe(_synthetic_rows()), lag_hours=3) == []
    )  # 24 matches < min_train_matches


def test_assert_disjoint_catches_overlap_and_frozen_leaks():
    cfg = splits.load_bench_config()
    object.__setattr__(cfg, "min_train_matches", 10)
    rows = _synthetic_rows()
    folds = splits.make_folds(cfg, _universe(rows), 3)
    with pytest.raises(AssertionError, match="frozen"):
        splits.assert_disjoint(folds, _universe(rows, holdout=(folds[0].eval_rows[0].fixture_id,)))
    bad = splits.Fold(folds[0].eval_season, folds[0].train_seasons, folds[0].inner_validation_season,
                      folds[0].fit_cutoff_utc, folds[0].train_rows, folds[0].train_rows)  # fmt: skip
    with pytest.raises(AssertionError, match="both train and eval"):
        splits.assert_disjoint([bad], _universe(rows))


def test_inner_split_puts_validation_strictly_after_fit():
    cfg = splits.load_bench_config()
    object.__setattr__(cfg, "min_train_matches", 10)
    fold = splits.make_folds(cfg, _universe(_synthetic_rows()), 3)[-1]
    fit, val = splits.inner_split(fold)
    assert {r.season for r in val} == {fold.inner_validation_season}
    assert max(r.kickoff_utc for r in fit) < min(r.kickoff_utc for r in val)
    assert fold.eval_season not in {r.season for r in fit + val}


def test_history_leak_check_flags_a_team_playing_inside_the_stage_window():
    a = EvalRow("m1", "ENG1", "2021-22", utc(2021, 9, 1, 19), "A", "B", 0)
    ok = EvalRow("m2", "ENG1", "2021-22", utc(2021, 9, 5, 15), "A", "C", 0)
    soon = EvalRow(
        "m3", "ENG1", "2021-22", utc(2021, 9, 2, 12), "B", "D", 0
    )  # B played 17 h earlier: not available at t-24h
    assert splits.history_leak_check([a, ok], 3, SnapshotStage.T_24H) == []
    bad = splits.history_leak_check([a, soon], 3, SnapshotStage.T_24H)
    assert [b["team"] for b in bad] == ["B"]
    assert splits.history_leak_check([a, soon], 3, SnapshotStage.KICKOFF) == []  # at kick-off it is available


def _identity(fid, league, season, finished=True, score=(1, 0), day=1):
    return IdentityRow(fid, "openfootball_domestic", fid, league, season, "H" + fid, "A" + fid, utc(2021, 9, day, 15), True,
                       "FINISHED" if finished else "SCHEDULED", score if finished else None, None, "x.json")  # fmt: skip


def test_universe_keeps_holdout_scores_out_of_dev_rows_and_never_double_assigns():
    cov = [
        SimpleNamespace(
            canonical_league_id="ENG1", season=s, training_status="TRAINABLE_COMPLETE", source_status="OK"
        )
        for s in ("2021-22", "2025-26", "2026-27", "2024-25")
    ]
    ids = [
        _identity("dev", "ENG1", "2021-22", day=1), _identity("hold", "ENG1", "2025-26", day=2),
        _identity("res", "ENG1", "2026-27", day=3), _identity("guard", "ENG1", "2024-25", day=4),
        _identity("future", "ENG1", "2021-22", finished=False, day=5),
    ]  # fmt: skip
    uni = splits.build_universe(CFG, SimpleNamespace(coverage=cov, identity_rows=ids))
    assert [r.fixture_id for r in uni.dev] == ["dev"]
    assert uni.holdout_ids == ("hold",) and uni.reserved_ids == ("res",) and uni.guard_ids == ("guard",)
    assert not hasattr(uni, "holdout")  # no attribute carries holdout scores
    dup = ids + [_identity("dev", "ENG1", "2021-22", day=1)]
    with pytest.raises(ValueError, match="duplicate"):
        splits.build_universe(CFG, SimpleNamespace(coverage=cov, identity_rows=dup))


def test_label_mapping_home_draw_away_and_binary_targets():
    r = lambda hg, ag: splits.to_eval_row(_identity("x", "ENG1", "2021-22", score=(hg, ag)))  # noqa: E731
    assert (r(2, 1).outcome, r(1, 1).outcome, r(0, 3).outcome) == (0, 1, 2)
    y = benchmark.outcomes_for([r(2, 1), r(0, 0), r(1, 1), r(3, 0)], 2.5)
    assert y["over_under"].tolist() == [1, 0, 0, 1]  # total 3 > 2.5, 0, 2, 3
    assert y["btts"].tolist() == [1, 0, 1, 0]
    with pytest.raises(ValueError):
        splits.to_eval_row(_identity("x", "ENG1", "2021-22", finished=False))


def test_inner_ids_hash_is_order_independent():
    assert splits.ids_hash(["b", "a"]) == splits.ids_hash(["a", "b"]) != splits.ids_hash(["a", "c"])


# ------------------------------------------------------------------ models on a synthetic fold (TEST_ONLY)
def _small_fold() -> splits.Fold:
    cfg = splits.load_bench_config()
    object.__setattr__(cfg, "min_train_matches", 10)
    return splits.make_folds(cfg, _universe(_synthetic_rows(per=60, seed=5)), 3)[-1]


def test_run_fold_probabilities_are_valid_deterministic_and_aligned():
    fold = _small_fold()
    a, b = benchmark.run_fold(CFG, fold), benchmark.run_fold(CFG, fold)
    n = len(fold.eval_rows)
    for name, p in a.probs_1x2.items():
        assert p.shape == (n, 3) and np.allclose(p.sum(axis=1), 1.0), name
        assert np.array_equal(p, b.probs_1x2[name]), name  # deterministic
    for probs in (a.probs_over, a.probs_btts):
        for name, p in probs.items():
            assert p.shape == (n,) and ((p >= 0) & (p <= 1)).all(), name
            assert np.array_equal(p, (b.probs_over | b.probs_btts)[name]), name
    assert set(a.probs_1x2) == set(CFG.models_1x2) and set(a.probs_over) == set(CFG.models_over_under)


def test_every_model_is_scored_on_exactly_the_same_fixtures():
    res = [benchmark.run_fold(CFG, _small_fold())]
    ev = benchmark.collect_eval(res, CFG)
    n = len(ev["rows"])
    assert all(len(p) == n for t in benchmark.TARGETS for p in ev["probs"][t].values())  # benchmark parity
    metrics, deltas, reliab = benchmark.score_all(ev, CFG)
    for t in benchmark.TARGETS:
        assert {v["n"] for k, v in metrics[t]["ALL"].items() if k != "_label_rate"} == {n}


def test_goal_distribution_targets_match_the_closed_form_for_independent_poisson():
    from math import exp, factorial

    from src.models import PoissonModel

    fold = _small_fold()
    model = PoissonModel().fit(list(fold.train_rows))
    rows = list(fold.eval_rows)[:6]
    over, both = benchmark.goal_target_probs(model, rows, 2.5)
    for i, r in enumerate(rows):
        lh, la, _, _ = model.expected_goals(r.home_id, r.away_id)
        assert both[i] == pytest.approx((1 - exp(-lh)) * (1 - exp(-la)), abs=2e-3)  # grid truncation only
        under = sum(exp(-lh - la) * (lh**a) * (la**b) / (factorial(a) * factorial(b))
                    for a in range(3) for b in range(3) if a + b <= 2)  # fmt: skip
        assert over[i] == pytest.approx(1 - under, abs=2e-3)


def test_degenerate_baselines_are_the_documented_hard_guesses():
    res = benchmark.run_fold(CFG, _small_fold())
    assert (res.probs_over["always_over"] == 1).all() and (res.probs_over["always_under"] == 0).all()
    assert (res.probs_btts["always_yes"] == 1).all() and (res.probs_btts["always_no"] == 0).all()
    assert np.allclose(res.probs_1x2["always_home"], [1, 0, 0])


def test_single_class_period_metrics_do_not_crash():
    p, y = np.full(5, 0.7), np.ones(5, dtype=int)
    out = bm.binary_metrics(p, y)
    assert out["auc"] is None and out["log_loss"] > 0


# ------------------------------------------------------------------ claims (TEST_ONLY table)
TABLE_TEXT = """# t
| Tarih | Lig | Maç | Skor | 1X2 | | A/Ü | | KG | |
|---|---|---|---|---|---|---|---|---|---|
| 10-09 | EPL | A - B | 2-2 | H %71 | ✗ | Üst | ✓ | Var | ✓ |
| 10-10 | EPL | C - D | 1-0 | H %41 | ✓ | Üst | ✗ | Var | ✗ |
"""


def test_claims_table_is_recounted_and_a_mismatch_is_unverified(tmp_path):
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "track_record_table.md").write_text(TABLE_TEXT, encoding="utf-8")
    ev = {"rows": [_synthetic_rows()[0]]}
    out = claims.claims_report(
        tmp_path,
        ev,
        CFG,
        {"over_under": {"ALL": {"_label_rate": 0.5}}, "btts": {"ALL": {"_label_rate": 0.5}}},
    )
    chk = out["table_check"]
    assert chk["rows"] == 2 and chk["hits_1x2_ou_btts"] == [1, 1, 1] and chk["always_over_hits"] == 1
    assert (
        chk["status"] == "UNVERIFIED_COUNTS_DIFFER" and chk["counts_match_reported"] is False
    )  # 2 != 138 reported
    assert out["licensed_universe_comparison"]["same_fixture_set_as_the_138"] is False


def test_claims_without_the_table_is_not_verified_never_assumed(tmp_path):
    ev = {"rows": [_synthetic_rows()[0]]}
    out = claims.claims_report(tmp_path, ev, CFG, {})
    assert out["table_check"]["status"] == "NOT_VERIFIED"


def test_claims_pre_match_provenance_counts_only_artifacts_from_an_earlier_day(tmp_path):
    base = tmp_path / "artifacts" / "markets"
    for key, cutoff in (("A__B__2026-10-10", "2026-10-09T10:00:00+00:00"), ("C__D__2026-10-10", "2026-10-10T08:00:00+00:00"),
                        ("E__F__2026-11-01", "2026-10-01T08:00:00+00:00")):  # fmt: skip
        d = base / key
        d.mkdir(parents=True)
        (d / "intel-20261009T100000Z.json").write_text(json.dumps({"information_cutoff": cutoff}), encoding="utf-8")
    (base / "G__H__2026-10-10").mkdir()  # a directory without artifacts is not counted
    got = claims._pre_match_artifacts(tmp_path, "2026-10-09", "2026-10-19")
    assert got == {"fixture_dirs_in_window": 2, "with_artifact_generated_on_an_earlier_day": 1}


# ------------------------------------------------------------------ licence gate and config
def test_licensed_providers_pass_the_production_gate_and_blocked_ones_fail_closed():
    manifest = load_manifest()
    training_gate(manifest, benchmark.LICENSED_PROVIDERS, "production_ml")
    for pid in ("football_data_co_uk", "api_football", "football_data_org", "not_in_manifest"):
        with pytest.raises(LicenseGateError):
            training_gate(manifest, [pid], "production_ml")


def test_bench_config_is_consumed_and_validated(tmp_path):
    assert CFG.reference_model in CFG.models_1x2 and CFG.holdout_default == ("2025-26",)
    assert CFG.guard_seasons == ("2024-25", "2025-26")  # read from configs/evaluation.yaml, not retyped
    text = (
        (ROOT / "configs" / "league_models" / "benchmark.yaml")
        .read_text(encoding="utf-8")
        .replace("iso_week", "matchday")
    )
    bad = tmp_path / "b.yaml"
    bad.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="iso_week"):
        splits.load_bench_config(bad)


def test_run_refuses_before_reading_anything_when_a_source_fails_the_licence_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(benchmark, "LICENSED_PROVIDERS", ["openfootball_domestic", "football_data_co_uk"])
    with pytest.raises(LicenseGateError):  # raised before any cache file or alias file is opened (tmp_path is empty)
        benchmark.run(tmp_path, utc(2026, 10, 20), None, dry_run=True)


def _fake_manifest(hashes: dict[str, str]) -> dict:
    return {
        "protocol_version": "lm-s2-v1", "snapshot_id": "snap-x", "data_snapshot_sha256": "d", "config_fingerprint_sha256": "c",
        "output_hashes": hashes, "stage": "t-24h", "as_of": "2026-10-20T00:00:00+00:00", "git_sha": "abc",
    }  # fmt: skip


def test_registry_is_append_only_idempotent_and_refuses_a_conflicting_entry(tmp_path):
    run_dir = tmp_path / "run1"
    run_dir.mkdir()
    first = benchmark.register_run(run_dir, _fake_manifest({"a.json": "1" * 64}))
    again = benchmark.register_run(run_dir, _fake_manifest({"a.json": "1" * 64}))
    other = benchmark.register_run(run_dir, _fake_manifest({"a.json": "2" * 64}))
    assert (first["status"], again["status"], other["status"]) == ("REGISTERED", "ALREADY_REGISTERED_IDENTICAL", "REGISTERED")
    assert first["run_id"] == again["run_id"] != other["run_id"]  # the id derives from the hashes
    lines = (tmp_path / "registry.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    tampered = json.loads(lines[0])
    tampered["output_hashes"] = {"a.json": "9" * 64}
    (tmp_path / "registry.jsonl").write_text(json.dumps(tampered) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="registry conflict"):
        benchmark.register_run(run_dir, _fake_manifest({"a.json": "1" * 64}))


def test_verify_run_rehashes_files_and_flags_changed_or_missing_ones(tmp_path):
    import hashlib

    (tmp_path / "a.json").write_text("{}", encoding="utf-8")
    good = {"a.json": hashlib.sha256(b"{}").hexdigest(), "b.json": "0" * 64}
    (tmp_path / "run_manifest.json").write_text(json.dumps({"output_hashes": good}), encoding="utf-8")
    res = benchmark.verify_run(tmp_path)
    assert res["ok"] is False and res["mismatches"] == {"b.json": "MISSING"}
    (tmp_path / "a.json").write_text("{ }", encoding="utf-8")  # a file that exists but changed is not success
    assert "a.json" in benchmark.verify_run(tmp_path)["mismatches"]


# ------------------------------------------------------------------ end-to-end on a synthetic audit (TEST_ONLY)
def _synthetic_audit(per_season: int = 90):
    """TEST_ONLY: one league, seasons 2019-20 to 2026-27, deterministic fake matches. Not real data."""
    rng = np.random.default_rng(11)
    rows, cov = [], []
    for si, season in enumerate(["2019-20", "2020-21", "2021-22", "2022-23", "2023-24", "2024-25", "2025-26", "2026-27"]):
        cov.append(SimpleNamespace(canonical_league_id="ITA1", season=season, training_status="TRAINABLE_COMPLETE", source_status="OK"))
        start = utc(2019 + si, 8, 20, 18)
        for i in range(per_season):
            h, a = rng.choice(10, 2, replace=False)
            rows.append(
                IdentityRow(f"{season}-{i:03d}", "openfootball_domestic", f"p{si}-{i}", "ITA1", season, f"T{h}", f"T{a}",
                            start + timedelta(days=3 * i), True, "FINISHED", (int(rng.poisson(1.4)), int(rng.poisson(1.1))), None, "x.json")
            )  # fmt: skip
    files = [SimpleNamespace(sha256="a" * 64), SimpleNamespace(sha256="b" * 64)]
    return SimpleNamespace(coverage=cov, identity_rows=rows, source_files=files)


@pytest.fixture
def e2e_root(tmp_path):
    import shutil

    (tmp_path / "configs" / "league_models").mkdir(parents=True)
    shutil.copy(ROOT / "configs" / "model.yaml", tmp_path / "configs" / "model.yaml")
    text = (ROOT / "configs" / "league_models" / "benchmark.yaml").read_text(encoding="utf-8")
    text = text.replace("min_train_matches: 500", "min_train_matches: 50").replace("samples: 1000", "samples: 60")
    cfg_path = tmp_path / "configs" / "league_models" / "benchmark.yaml"
    cfg_path.write_text(text, encoding="utf-8")
    return tmp_path, cfg_path


def _run_e2e(root, cfg_path, out, **kw):
    return benchmark.run(root, utc(2026, 10, 20), out, bench_config_path=cfg_path, audit=_synthetic_audit(), **kw)


def test_end_to_end_run_writes_verified_files_registry_and_keeps_the_holdout_out(e2e_root):
    root, cfg_path = e2e_root
    out = root / "artifacts" / "run1"
    res = _run_e2e(root, cfg_path, out, docs_dir=root / "docs")
    assert benchmark.verify_run(out)["ok"] is True
    assert res["registry_entry"]["status"] == "REGISTERED"
    run_manifest = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    assert run_manifest["folds"] == ["2020-21", "2021-22", "2022-23", "2023-24", "2024-25"]
    assert (root / "docs" / "s2_run_manifest.json").is_file() and (out / "timings.json").is_file()
    proto = json.loads((out / "protocol_manifest.json").read_text(encoding="utf-8"))
    assert proto["universe_counts"]["HOLDOUT"] == 90 and proto["universe_counts"]["RESERVED"] == 90
    predicted = {json.loads(x)["fixture_id"] for x in (out / "fold_predictions.jsonl").read_text(encoding="utf-8").splitlines()}
    assert not any(f.startswith(("2025-26", "2026-27")) for f in predicted)  # frozen fixtures never reach a model
    leaks = json.loads((out / "leakage_audit.json").read_text(encoding="utf-8"))
    assert leaks["stage"] == "t-24h" and "folds" in leaks
    health = json.loads((out / "benchmark_metrics.json").read_text(encoding="utf-8"))["fit_health"]
    assert set(health) == set(run_manifest["folds"]) and "ipf_converged" in health["2024-25"]["dixon_coles"]


def test_end_to_end_run_is_deterministic_and_dry_run_writes_nothing(e2e_root):
    root, cfg_path = e2e_root
    a = _run_e2e(root, cfg_path, root / "artifacts" / "a")["run_manifest"]["output_hashes"]
    b = _run_e2e(root, cfg_path, root / "artifacts" / "b")["run_manifest"]["output_hashes"]
    assert a == b  # same inputs, same files
    plan = _run_e2e(root, cfg_path, None, dry_run=True)["plan"]
    assert plan["dry_run"] is True and len(plan["folds"]) == 5 and not (root / "artifacts" / "c").exists()


def test_window_and_target_filters_and_the_refusal_when_no_fold_remains(e2e_root):
    root, cfg_path = e2e_root
    out = root / "artifacts" / "w"
    res = _run_e2e(root, cfg_path, out, eval_start="2023-24", eval_end="2023-24", targets=("1x2",))
    assert list(res["headline"]) == ["1x2"]
    assert json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))["folds"] == ["2023-24"]
    with pytest.raises(SystemExit, match="fail closed"):
        _run_e2e(root, cfg_path, None, eval_start="2031-32")


def test_global_artifact_audit_matches_a_crlf_written_hash_and_reports_a_missing_directory(tmp_path):
    import hashlib

    assert benchmark.global_artifact_audit(tmp_path)["status"] == "NOT_VERIFIED"
    run_dir = tmp_path / "artifacts" / "walk_forward" / "x_walkforward"
    run_dir.mkdir(parents=True)
    text = '{"model_id": "elo", "data_version": "dv-1", "feature_version": "fv2"}\n{"model_id": "poisson"}'
    (run_dir / "predictions.jsonl").write_bytes(text.replace("\n", "\r\n").encode())  # Windows line ends on disk
    (run_dir / "hashes.json").write_text(json.dumps({"predictions": hashlib.sha256(text.encode()).hexdigest()}), encoding="utf-8")
    item = benchmark.global_artifact_audit(tmp_path)["runs"][0]
    assert item["predictions_hash_matches"] is True and item["predictions_hash_basis"] == "LF-normalised text"
    assert item["models"] == ["elo", "poisson"] and item["data_version"] == "dv-1"
    (run_dir / "hashes.json").write_text(json.dumps({"predictions": "0" * 64}), encoding="utf-8")
    assert benchmark.global_artifact_audit(tmp_path)["runs"][0]["predictions_hash_basis"] == "NO MATCH"


def test_cli_dry_run_and_verify_exit_codes(e2e_root, capsys):
    root, cfg_path = e2e_root
    out = root / "artifacts" / "v"
    _run_e2e(root, cfg_path, out, eval_start="2024-25", targets=("1x2",))
    assert benchmark.main(["--root", str(root), "--verify", "artifacts/v"]) == 0
    (out / "protocol_manifest.json").write_text("{}", encoding="utf-8")  # tamper
    assert benchmark.main(["--root", str(root), "--verify", "artifacts/v"]) == 1
    assert "protocol_manifest.json" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        benchmark.main(["--as-of", "2026-10-20T00:00:00", "--dry-run"])  # no UTC offset: refused


# ------------------------------------------------------------------ real cached data (skipped in CI)
@needs_cache
def test_real_dry_run_matches_the_committed_protocol_and_leaks_nothing():
    out = benchmark.run(ROOT, utc(2026, 10, 20), None, dry_run=True)["plan"]
    committed = json.loads((OUT / "s2_protocol_manifest.json").read_text(encoding="utf-8"))
    assert out["folds"] == committed["folds"]
    assert [f["eval_season"] for f in out["folds"]] == ["2020-21", "2021-22", "2022-23", "2023-24", "2024-25"]
    assert committed["frozen"]["holdout"]["count"] > 0
    assert committed["universe_counts"]["HOLDOUT"] == committed["frozen"]["holdout"]["count"]


@needs_cache
def test_real_universe_never_assigns_a_fixture_to_two_roles():
    from src.data.teams import TeamDirectory
    from src.league_models.competitions import load_config as lcfg
    from src.league_models.coverage import collect

    league_cfg = lcfg()
    audit = collect(
        ROOT,
        league_cfg,
        TeamDirectory.load(ROOT / "configs" / "team_aliases.yaml"),
        load_manifest(),
        utc(2026, 10, 20),
    )
    uni = splits.build_universe(CFG, audit)
    dev = {r.fixture_id for r in uni.dev}
    frozen = [set(uni.holdout_ids), set(uni.reserved_ids), set(uni.guard_ids)]
    assert all(not (dev & f) for f in frozen) and not (frozen[0] & frozen[1]) and not (frozen[0] & frozen[2])
    folds = splits.make_folds(CFG, uni, league_cfg.result_lag_hours)
    splits.assert_disjoint(folds, uni)
    assert (
        benchmark.leakage_audit(CFG, uni, folds, league_cfg.result_lag_hours, SnapshotStage.T_24H)[
            "total_replay_violations"
        ]
        == 0
    )


def test_committed_outputs_are_internally_consistent():
    manifest_path = OUT / "s2_run_manifest.json"
    if not manifest_path.is_file():
        pytest.skip("Sprint 2 outputs not generated")
    run = json.loads(manifest_path.read_text(encoding="utf-8"))
    import hashlib

    for name, digest in run["output_hashes"].items():
        committed = OUT / f"s2_{name}"
        if committed.is_file():
            assert hashlib.sha256(committed.read_bytes()).hexdigest() == digest, name
    metrics = json.loads((OUT / "s2_benchmark_metrics.json").read_text(encoding="utf-8"))
    for target in benchmark.TARGETS:
        ns = {v["n"] for k, v in metrics["metrics"][target]["ALL"].items() if k != "_label_rate"}
        assert len(ns) == 1  # every model scored the identical fixture set
    assert metrics["no_single_winner"] is True
    leaks = json.loads((OUT / "s2_leakage_audit.json").read_text(encoding="utf-8"))
    assert leaks["total_replay_violations"] == 0


def test_expect_manifest_exit_codes(tmp_path, monkeypatch, capsys):
    """TEST_ONLY stub of run(): checks only the comparison and the exit code used by the CI job."""
    monkeypatch.setattr(benchmark, "run", lambda *a, **k: {"run_manifest": {"output_hashes": {"a.json": "1", "b.json": "2"}}})
    same = tmp_path / "same.json"
    same.write_text(json.dumps({"output_hashes": {"a.json": "1", "b.json": "2"}}), encoding="utf-8")
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"output_hashes": {"a.json": "1", "b.json": "9"}}), encoding="utf-8")
    args = ["--root", str(tmp_path), "--as-of", "2026-10-20T00:00:00Z"]
    assert benchmark.main([*args, "--expect-manifest", str(same)]) == 0
    assert benchmark.main([*args, "--expect-manifest", str(other)]) == 1
    assert '"b.json"' in capsys.readouterr().out


def test_compare_to_committed_tolerates_float_noise_and_catches_real_changes(e2e_root):
    import shutil

    root, cfg_path = e2e_root
    out, docs = root / "artifacts" / "cmp", root / "docs_copy"
    _run_e2e(root, cfg_path, out, targets=("1x2",))
    docs.mkdir()
    for name in (*benchmark.EXACT_FILES, *benchmark.NUMERIC_FILES):
        shutil.copy(out / name, docs / f"s2_{name}")
    assert benchmark.compare_to_committed(out, docs, 1e-3)["ok"] is True

    committed = json.loads((docs / "s2_benchmark_metrics.json").read_text(encoding="utf-8"))
    leaf = committed["metrics"]["1x2"]["ALL"]["elo"]
    leaf["log_loss"] += 5e-5  # last-digit noise between platforms: accepted
    committed["fit_health"]["2024-25"]["dixon_coles"]["iterations_used"] += 7  # solver trace: reported, not failed
    (docs / "s2_benchmark_metrics.json").write_text(json.dumps(committed), encoding="utf-8")
    res = benchmark.compare_to_committed(out, docs, 1e-3)
    assert res["ok"] is True and res["solver_trace_differences_reported_only"]["benchmark_metrics.json"]

    leaf["log_loss"] += 0.05  # a changed result: refused
    (docs / "s2_benchmark_metrics.json").write_text(json.dumps(committed), encoding="utf-8")
    res = benchmark.compare_to_committed(out, docs, 1e-3)
    assert res["ok"] is False and res["numeric_differences"]["benchmark_metrics.json"][0]["abs_diff"] > 0.04

    (docs / "s2_protocol_manifest.json").write_text("{}", encoding="utf-8")  # a structural file must match exactly
    assert benchmark.compare_to_committed(out, docs, 1e-3)["exact_files_differing"] == ["protocol_manifest.json"]
