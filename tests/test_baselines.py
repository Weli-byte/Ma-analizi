from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from src.evaluation.dataset import EvalRow
from src.evaluation.runner import EvalSettings, assert_chronological, evaluate
from src.features.availability import FeatureAvailabilityError
from src.models import (
    REGISTRY,
    AlwaysHome,
    HistoricalPrior,
    MarketImplied,
    RecentFormNaive,
    build_models,
    default_baselines,
)
from src.runmode import RunMode

T0 = datetime(2023, 8, 1, tzinfo=UTC)
METRICS = ["log_loss", "brier", "rps", "ece", "accuracy"]


def settings(mode=RunMode.RESEARCH, max_rate=1.0, samples=20):
    return EvalSettings(mode, METRICS, 5, samples, 3, max_rate)


def row(i, outcome, league="EPL", season="2023-24", day=0, feats=None, odds=None, reasons=None):
    return EvalRow(
        f"f{i}", league, season, T0 + timedelta(days=day + i), f"h{i}", f"a{i}", outcome,
        feats or {}, reasons or {}, odds or {},
    )  # fmt: skip


def test_always_home():
    assert AlwaysHome().predict_proba([row(1, 2), row(2, 0)]).tolist() == [[1, 0, 0], [1, 0, 0]]


def test_historical_prior_per_league_uses_only_training():
    train = [row(i, o, "EPL") for i, o in enumerate([0, 0, 1, 2])] + [
        row(10 + i, o, "LALIGA") for i, o in enumerate([2, 2, 2, 1])
    ]
    m = HistoricalPrior().fit(train)
    p = m.predict_proba([row(50, 0, "EPL"), row(51, 0, "LALIGA"), row(52, 0, "BUND")])
    assert p[0].tolist() == [0.5, 0.25, 0.25] and p[1].tolist() == [0, 0.25, 0.75]
    assert p[2].tolist() == pytest.approx([0.25, 0.25, 0.5])  # unseen league -> global prior
    assert np.allclose(p.sum(axis=1), 1)


def test_recent_form_naive_is_fixed_rule_with_counted_fallback():
    train = [row(i, o) for i, o in enumerate([0, 1, 2, 0, 1])]  # draw rate 0.4
    m = RecentFormNaive().fit(train)
    strong = row(1, 0, feats={"home_form_points_5": 15.0, "away_form_points_5": 0.0})
    even = row(2, 0, feats={"home_form_points_5": 6.0, "away_form_points_5": 6.0})
    missing = row(3, 0, feats={"home_form_points_5": None, "away_form_points_5": 4.0})
    p = m.predict_proba([strong, even, missing])
    assert p[0].tolist() == pytest.approx([0.6 * 16 / 17, 0.4, 0.6 * 1 / 17])
    assert p[1][0] == pytest.approx(p[1][2])
    assert p[2].tolist() == pytest.approx([0.4, 0.4, 0.2])  # historical prior
    assert m.diagnostics["prior_fallback_rows"] == 1 and np.allclose(p.sum(axis=1), 1)
    assert m.required_features == ("home_form_points_5", "away_form_points_5")


def test_market_implied_is_a_labelled_reference_baseline_with_devig():
    odds = {"closing:agg_avg": (2.0, 4.0, 4.0), "pre_match:agg_avg": (1.5, 4.0, 8.0)}
    r_close = row(1, 0, odds=odds)
    r_pre = row(2, 0, odds={"pre_match:B365": (2.0, 4.0, 4.0)})
    m = MarketImplied()
    p = m.predict_proba([r_close, r_pre, row(3, 0)])
    assert p[0].tolist() == pytest.approx([0.5, 0.25, 0.25])  # closing preferred
    assert p[1].tolist() == pytest.approx([0.5, 0.25, 0.25]) and np.isnan(p[2]).all()
    assert m.diagnostics["no_odds_rows"] == 1 and m.diagnostics["timestamp_quality"] == "unknown"
    assert m.model_class == "reference_market_baseline"
    vig = row(4, 0, odds={"closing:agg_avg": (1.8, 3.6, 4.5)})
    assert MarketImplied().predict_proba([vig]).sum() == pytest.approx(1.0)
    assert all(c.model_class == "baseline" for c in (AlwaysHome, HistoricalPrior, RecentFormNaive))


def test_model_registry_and_build_models():
    assert set(REGISTRY) == {"always_home", "historical_prior", "recent_form_naive", "market_implied"}
    assert [m.model_id for m in build_models(["market_implied", "always_home"])] == [
        "market_implied",
        "always_home",
    ]
    with pytest.raises(KeyError, match="unknown baseline"):
        build_models(["mystery"])
    assert len(default_baselines()) == 4


def test_runner_common_set_groups_ci_and_availability():
    train = [row(i, i % 3, day=-400) for i in range(9)]
    feats = {"home_form_points_5": 5.0, "away_form_points_5": 5.0}
    test = [
        row(100, 0, "EPL", odds={"closing:agg_avg": (2.0, 3.5, 4.0)}, feats=feats),
        row(101, 1, "EPL", feats=feats),  # no odds -> dropped from the common set
        row(102, 2, "LALIGA", odds={"closing:agg_avg": (2.5, 3.2, 3.0)}, feats=feats),
        row(103, 0, "LALIGA", odds={"closing:agg_avg": (1.7, 3.8, 5.0)}, feats=feats),
    ]
    rep = evaluate(default_baselines(), train, test, settings())
    assert rep.n_eval_rows == 4 and rep.n_common_rows == 3
    assert [r.model_id for r in rep.results] == list(REGISTRY)
    for r in rep.results:
        assert r.metrics["n"] == 3  # identical fixture set for every model
        assert set(r.by_league) == {"EPL", "LALIGA"} and r.by_league["LALIGA"]["n"] == 2
        assert r.unpredictable_rows == (1 if r.model_id == "market_implied" else 0)
        assert set(r.confidence_intervals) == set(METRICS)
        for k in METRICS:
            assert np.isfinite(r.metrics[k])
            ci = r.confidence_intervals[k]
            assert ci["lower"] <= ci["upper"] and ci["random_seed"] == 3
    naive = next(r for r in rep.results if r.model_id == "recent_form_naive")
    assert naive.availability["fallback_rate"] == 0.0 and naive.availability["total_fixtures"] == 4


def test_runner_can_disable_bootstrap():
    train = [row(i, i % 3, day=-400) for i in range(9)]
    test = [
        row(
            100,
            0,
            odds={"closing:agg_avg": (2.0, 3.5, 4.0)},
            feats={"home_form_points_5": 1.0, "away_form_points_5": 2.0},
        )
    ]
    rep = evaluate([AlwaysHome()], train, test, settings(samples=0))
    assert rep.results[0].confidence_intervals == {}


def test_runner_rejects_non_chronological_split():
    train, test = [row(1, 0, day=10)], [row(2, 0, day=0)]
    with pytest.raises(ValueError, match="chronological"):
        assert_chronological(train, test)
    with pytest.raises(ValueError):
        evaluate([AlwaysHome()], train, test, settings())
    with pytest.raises(ValueError):
        assert_chronological([], test)


def test_runner_rejects_invalid_model_output():
    class Bad(AlwaysHome):
        model_id = "bad"

        def predict_proba(self, rows):
            return np.tile([0.5, 0.5, 0.5], (len(rows), 1))

    with pytest.raises(ValueError):
        evaluate([Bad()], [row(1, 0, day=-100)], [row(2, 0, day=5)], settings())


# ------------------------------------------- no silent fallbacks: availability enforcement
def form_rows(n_missing, declared=True, n=20):
    rows = []
    for i in range(n):
        if i < n_missing:
            why = {"home_form_points_5": "new_team"} if declared else {}
            rows.append(
                row(100 + i, 0, feats={"home_form_points_5": None, "away_form_points_5": 5.0}, reasons=why)
            )
        else:
            rows.append(row(100 + i, 0, feats={"home_form_points_5": 5.0, "away_form_points_5": 5.0}))
    return rows


def test_fallback_rate_threshold_is_enforced_from_config_value():
    train = [row(i, i % 3, day=-400) for i in range(9)]
    test = form_rows(4)  # 20% fallback
    with pytest.raises(FeatureAvailabilityError, match="exceeds"):
        evaluate([RecentFormNaive()], train, test, settings(RunMode.RESEARCH, max_rate=0.10))
    rep = evaluate([RecentFormNaive()], train, test, settings(RunMode.RESEARCH, max_rate=0.25))
    assert (
        rep.results[0].availability["fallback_rows"] == 4
        and rep.results[0].diagnostics["prior_fallback_rows"] == 4
    )


def test_unexpected_missing_features_fail_strict_and_count_in_research():
    train = [row(i, i % 3, day=-400) for i in range(9)]
    test = form_rows(2, declared=False)  # missing with NO declared reason = possible bug
    with pytest.raises(FeatureAvailabilityError, match="unexpected"):
        evaluate([RecentFormNaive()], train, test, settings(RunMode.STRICT, max_rate=0.5))
    assert (
        evaluate([RecentFormNaive()], train, test, settings(RunMode.RESEARCH, max_rate=0.5)).n_common_rows
        == 20
    )
    with pytest.raises(FeatureAvailabilityError):  # research: still bounded by the threshold
        evaluate([RecentFormNaive()], train, test, settings(RunMode.RESEARCH, max_rate=0.05))


def test_declared_startup_nans_are_allowed_in_strict_within_threshold():
    train = [row(i, i % 3, day=-400) for i in range(9)]
    rep = evaluate(
        [RecentFormNaive()], train, form_rows(2, declared=True), settings(RunMode.STRICT, max_rate=0.5)
    )
    assert rep.results[0].availability["unexpected_missing_rows"] == 0


def test_missing_feature_record_is_unexpected():
    train = [row(i, i % 3, day=-400) for i in range(9)]
    test = form_rows(0)
    test[5] = row(105, 0)  # no features at all for this fixture
    with pytest.raises(FeatureAvailabilityError, match="unexpected"):
        evaluate([RecentFormNaive()], train, test, settings(RunMode.STRICT, max_rate=0.5))
    rep = evaluate([RecentFormNaive()], train, test, settings(RunMode.DEVELOPMENT, max_rate=0.5))
    a = rep.results[0].availability
    assert a["missing_feature_rows"] == 1 and a["reasons"] == {"missing_record": 1}
