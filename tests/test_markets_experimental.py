"""Experimental goal-market model shown to users (ADR 0072). TEST_ONLY synthetic matches; not real data."""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from src.league_models import s4c_benchmark
from src.markets.data import StatMatch
from src.markets.experimental import LABEL, ExperimentalModel
from src.web import pages

NOW = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
EMPTY = {"corners": None, "yellow_cards": None, "shots_on_target": None}


def history(n_seasons=4, per_season=260, seed=5):
    rng = np.random.default_rng(seed)
    att = rng.normal(0, 0.3, 8)
    out, i = [], 0
    for k in range(n_seasons):
        start = datetime(2022 + k, 8, 20, 18, tzinfo=UTC)
        for j in range(per_season):
            h, a = rng.choice(8, 2, replace=False)
            gh, ga = int(rng.poisson(np.exp(0.25 + 0.2 + att[h] - att[a]))), int(rng.poisson(np.exp(0.25 + att[a] - att[h])))
            ko = start + timedelta(hours=30 * j)
            season = f"{ko.year if ko.month >= 7 else ko.year - 1}-{str((ko.year if ko.month >= 7 else ko.year - 1) + 1)[2:]}"
            out.append(StatMatch(f"m{i}", season, "EPL", ko, ko + timedelta(hours=3), f"ENG_t{h}", f"ENG_t{a}", gh, ga, EMPTY, EMPTY, None))
            i += 1
    return out


@pytest.fixture(autouse=True)
def small_config(monkeypatch):
    real = s4c_benchmark.load_s4c

    def load(*a, **k):
        raw = real(*a, **k)
        raw["production_experimental"] = {**raw["production_experimental"], "min_rows": 300, "min_cross_country_rows": 10**9, "grid": {"l2_team": [8.0, 64.0], "l2_country": [8.0], "half_life_days": [None]}}
        raw["nested"] = {**raw["nested"], "min_inner_fit_matches": 60}
        return raw

    monkeypatch.setattr(s4c_benchmark, "load_s4c", load)


def test_the_model_is_fitted_on_available_history_and_predicts_valid_goal_markets():
    model, status = ExperimentalModel.fit(history(), NOW)
    assert status == "OK" and model.rows == 4 * 260
    x = model.predict("ENG_t1", "ENG_t2", "EPL", NOW + timedelta(days=3))
    assert x["label"] == LABEL == "Deneysel model (beta)" and x["model"] == "H_dc_hier"
    assert all(0 < r["over"] < 1 and abs(r["over"] + r["under"] - 1) < 1e-3 for r in x["over_under"]) and 0 < x["btts_yes"] < 1
    overs = [r["over"] for r in x["over_under"]]
    assert overs == sorted(overs, reverse=True)                                           # more goals needed, less likely
    assert x["expected_goals"]["total"] == pytest.approx(x["expected_goals"]["home"] + x["expected_goals"]["away"], abs=2e-3)
    assert len(x["scorelines"]) == 6 and "geliştirme" in x["evidence"] and x["selected"].keys() == {"1x2", "over_under", "btts"}
    assert model.predict("ENG_t1", "ENG_t2", "EPL", NOW, neutral=True) is None            # no home-advantage switch: a neutral-venue final gets no block


def test_a_match_that_is_not_yet_a_result_at_now_is_not_history():
    ms = history()
    future = StatMatch("late", "2026-27", "EPL", NOW - timedelta(hours=1), NOW + timedelta(hours=2), "ENG_t1", "ENG_t2", 9, 0, EMPTY, EMPTY, None)
    a, _ = ExperimentalModel.fit(ms, NOW)
    b, _ = ExperimentalModel.fit([*ms, future], NOW)
    assert a.rows == b.rows                                                               # the 9-0 is not available yet


def test_too_little_history_gives_no_model_and_says_why():
    model, status = ExperimentalModel.fit(history(n_seasons=1, per_season=100), NOW)
    assert model is None and status.startswith("NOT_ENOUGH_HISTORY")


def test_an_unseen_club_is_flagged_not_hidden():
    model, _ = ExperimentalModel.fit(history(), NOW)
    x = model.predict("ENG_t1", "ENG_brand_new", "EPL", NOW + timedelta(days=3))
    assert x["unseen_club"] is True


def _intel(experimental):
    res = {"headline_probs": {"home": 0.5, "draw": 0.25, "away": 0.25}}
    goals = {"over_under": [{"line": 2.5, "over": 0.55, "under": 0.45}], "btts_yes": 0.5, "btts_no": 0.5}
    return {"home": "Arsenal", "away": "Chelsea", "result": res, "goals": goals, "experimental": experimental}


def test_the_card_is_shown_with_its_evidence_and_nothing_appears_without_a_block():
    model, _ = ExperimentalModel.fit(history(), NOW)
    x = model.predict("ENG_t1", "ENG_t2", "EPL", NOW + timedelta(days=3))
    html = pages.experimental_card(_intel(x))
    assert "Deneysel model (beta)" in html and "Maç sonucu (1-X-2) tahmini bu modelden gelmez" in html and "Henüz canlı maç sonuçlarıyla doğrulanmadı" in html
    assert "2.5 gol" in html and "Mevcut model" in html
    assert pages.experimental_card(_intel(None)) == "" and pages.experimental_card({"goals": {}, "home": "a", "away": "b"}) == ""     # older artifact: no card


def test_the_english_page_translates_the_card():
    from src.web import i18n

    model, _ = ExperimentalModel.fit(history(), NOW)
    html = i18n.translate_html(pages.experimental_card(_intel(model.predict("ENG_t1", "ENG_t2", "EPL", NOW + timedelta(days=3)))))
    assert "Experimental model (beta)" in html and "has not yet been verified on live match results" in html and "Deneysel" not in html


def test_the_owner_can_switch_the_experimental_model_off(monkeypatch):
    monkeypatch.setenv("MARKETS_EXPERIMENTAL", "0")
    model, status = ExperimentalModel.fit(history(), NOW)
    assert model is None and status.startswith("DISABLED")
