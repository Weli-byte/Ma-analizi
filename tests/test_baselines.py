from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from src.config import EvaluationConfig
from src.evaluation.dataset import EvalRow
from src.evaluation.runner import assert_chronological, evaluate
from src.models import (
    AlwaysHome,
    HistoricalPrior,
    MarketImplied,
    RecentFormNaive,
    default_baselines,
)

T0 = datetime(2023, 8, 1, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[1]


def row(i, outcome, league="EPL", season="2023-24", day=0, feats=None, odds=None):
    return EvalRow(
        f"f{i}", league, season, T0 + timedelta(days=day + i), outcome, feats or {}, odds or {}
    )


def test_always_home():
    p = AlwaysHome().predict_proba([row(1, 2), row(2, 0)])
    assert p.tolist() == [[1, 0, 0], [1, 0, 0]]


def test_historical_prior_per_league_uses_only_training():
    train = [row(i, o, "EPL") for i, o in enumerate([0, 0, 1, 2])] + [
        row(10 + i, o, "LALIGA") for i, o in enumerate([2, 2, 2, 1])
    ]
    m = HistoricalPrior().fit(train)
    p = m.predict_proba([row(50, 0, "EPL"), row(51, 0, "LALIGA"), row(52, 0, "BUND")])
    assert p[0].tolist() == [0.5, 0.25, 0.25]
    assert p[1].tolist() == [0, 0.25, 0.75]
    assert p[2].tolist() == pytest.approx([0.25, 0.25, 0.5])  # unseen league -> global prior
    assert np.allclose(p.sum(axis=1), 1)


def test_recent_form_naive_is_fixed_rule_with_fallback():
    train = [row(i, o) for i, o in enumerate([0, 1, 2, 0, 1])]  # draw rate 0.4
    m = RecentFormNaive().fit(train)
    strong = row(1, 0, feats={"home_form_points_5": 15.0, "away_form_points_5": 0.0})
    even = row(2, 0, feats={"home_form_points_5": 6.0, "away_form_points_5": 6.0})
    missing = row(3, 0, feats={"home_form_points_5": None, "away_form_points_5": 4.0})
    p = m.predict_proba([strong, even, missing])
    assert p[0].tolist() == pytest.approx([0.6 * 16 / 17, 0.4, 0.6 * 1 / 17])
    assert p[1][0] == pytest.approx(p[1][2])
    assert p[2].tolist() == pytest.approx([0.4, 0.4, 0.2])  # historical prior
    assert m.diagnostics["prior_fallback_rows"] == 1
    assert np.allclose(p.sum(axis=1), 1)


def test_market_implied_devig_and_source_preference():
    odds = {"closing:Avg": (2.0, 4.0, 4.0), "pre_match_unspecified:Avg": (1.5, 4.0, 8.0)}
    r_close = row(1, 0, odds=odds)
    r_pre = row(2, 0, odds={"pre_match_unspecified:B365": (2.0, 4.0, 4.0)})
    r_none = row(3, 0)
    m = MarketImplied()
    p = m.predict_proba([r_close, r_pre, r_none])
    assert p[0].tolist() == pytest.approx([0.5, 0.25, 0.25])  # overround removed, closing first
    assert p[1].tolist() == pytest.approx([0.5, 0.25, 0.25])
    assert np.isnan(p[2]).all()
    assert m.diagnostics["no_odds_rows"] == 1
    overround = 1 / 2 + 1 / 4 + 1 / 4
    assert overround == pytest.approx(1.0)  # sanity for the example above
    vig = row(4, 0, odds={"closing:Avg": (1.8, 3.6, 4.5)})
    assert MarketImplied().predict_proba([vig]).sum() == pytest.approx(1.0)


def test_runner_common_set_and_groups():
    train = [row(i, i % 3, day=-400) for i in range(9)]
    test = [
        row(100, 0, "EPL", "2023-24", odds={"closing:Avg": (2.0, 3.5, 4.0)}),
        row(101, 1, "EPL", "2023-24"),  # no odds -> dropped from common set
        row(102, 2, "LALIGA", "2023-24", odds={"closing:Avg": (2.5, 3.2, 3.0)}),
        row(103, 0, "LALIGA", "2023-24", odds={"closing:Avg": (1.7, 3.8, 5.0)}),
    ]
    rep = evaluate(default_baselines(), train, test)
    assert rep.n_eval_rows == 4 and rep.n_common_rows == 3
    assert [r.model_id for r in rep.results] == [
        "always_home",
        "historical_prior",
        "recent_form_naive",
        "market_implied",
    ]
    for r in rep.results:
        assert r.metrics["n"] == 3  # identical fixture set for every model
        assert set(r.by_league) == {"EPL", "LALIGA"}
        assert r.by_league["LALIGA"]["n"] == 2
        assert r.unpredictable_rows == (1 if r.model_id == "market_implied" else 0)
        for k in ("log_loss", "brier", "rps", "accuracy"):
            assert np.isfinite(r.metrics[k])


def test_runner_rejects_non_chronological_split():
    train = [row(1, 0, day=10)]
    test = [row(2, 0, day=0)]
    with pytest.raises(ValueError, match="chronological"):
        assert_chronological(train, test)
    with pytest.raises(ValueError):
        evaluate([AlwaysHome()], train, test)
    with pytest.raises(ValueError):
        assert_chronological([], test)


def test_runner_rejects_invalid_model_output():
    class Bad(AlwaysHome):
        model_id = "bad"

        def predict_proba(self, rows):
            return np.tile([0.5, 0.5, 0.5], (len(rows), 1))

    with pytest.raises(ValueError):
        evaluate([Bad()], [row(1, 0, day=-100)], [row(2, 0, day=5)])


def test_eval_config_split_rules():
    ok = dict(split_strategy="expanding", min_train_seasons=1, metrics=[], calibration_bins=10)
    EvaluationConfig(
        **ok,
        train_seasons=["2019-20"],
        validation_seasons=["2020-21"],
        final_test_seasons=["2021-22"],
    )
    with pytest.raises(ValueError):  # overlap
        EvaluationConfig(**ok, train_seasons=["2019-20"], validation_seasons=["2019-20"])
    with pytest.raises(ValueError):  # not chronological
        EvaluationConfig(**ok, train_seasons=["2021-22"], validation_seasons=["2019-20"])


@pytest.mark.skipif(
    not (ROOT / "data/features/fv1/features.parquet").exists(), reason="fv1 not built"
)
def test_real_data_baselines_reproducible_and_final_test_untouched():
    from src.config import load_config
    from src.evaluation.dataset import load_rows

    ev = load_config("evaluation")
    proc = ROOT / "data/processed/dv1/football.duckdb"
    feats = ROOT / "data/features/fv1/features.parquet"
    train = load_rows(proc, feats, ev.train_seasons)
    test = load_rows(proc, feats, ev.validation_seasons)
    assert {r.season for r in test}.isdisjoint(ev.final_test_seasons)
    assert {r.season for r in train}.isdisjoint(ev.final_test_seasons)
    a, b = evaluate(default_baselines(), train, test), evaluate(default_baselines(), train, test)
    assert a == b  # replay: same input -> same output
    by_id = {r.model_id: r.metrics for r in a.results}
    # sanity ordering: informed baselines beat the degenerate one on every proper score
    assert by_id["historical_prior"]["log_loss"] < by_id["always_home"]["log_loss"]
    assert by_id["market_implied"]["log_loss"] < by_id["historical_prior"]["log_loss"]
