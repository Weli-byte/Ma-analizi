import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import MappingProxyType

import pytest
from conftest import DV

from src.config import FeaturesConfig
from src.features import (
    MatchHistory,
    MatchRecord,
    audit_leakage,
    compute_features,
    registry_hash,
    spec_for,
)
from src.features import registry as reg
from src.features.builder import build_snapshots, content_hash
from src.features.compute import DEFAULT_CONFIG
from src.features.registry import AVAIL_SUFFIX, EXPERIMENTAL, REGISTRY, produced_names
from src.schemas import FixtureStatus

T0 = datetime(2024, 1, 1, 15, 0, tzinfo=UTC)
LAG = timedelta(hours=3)


def m(i, home, away, hg, ag, days, season="2023-24", extras=None, status=FixtureStatus.FINISHED):
    ko = T0 + timedelta(days=days)
    finished = status == FixtureStatus.FINISHED
    return MatchRecord(
        f"f{i}", season, ko, home, away, hg if finished else None, ag if finished else None,
        ko + LAG if finished else None, status, MappingProxyType(extras or {}),
    )  # fmt: skip


@pytest.fixture
def league():
    """Team A by day: 0 W 2-0(h), 7 W 1-0(a), 14 D 1-1(h), 21 L 0-2(a), 28 W 3-1(h), 35 W 2-1(a)."""
    return [
        m(1, "A", "B", 2, 0, 0),
        m(7, "B", "C", 1, 0, 3),
        m(2, "C", "A", 0, 1, 7),
        m(8, "D", "E", 2, 2, 10),
        m(3, "A", "D", 1, 1, 14),
        m(4, "E", "A", 2, 0, 21),
        m(5, "A", "B", 3, 1, 28),
        m(6, "C", "A", 1, 2, 35),
    ]


FX = m(99, "A", "D", 0, 0, 42)  # A at home vs D; its own score must never matter


def feats(league, fixture=FX, cutoff=None, cfg=DEFAULT_CONFIG):
    return compute_features(fixture, MatchHistory(league), cutoff or fixture.kickoff_utc, cfg)


# ----------------------------------------------------------------------- values
def test_known_values(league):
    r = feats(league).values
    assert r["home_form_points_3"] == 6  # L,W,W -> 0+3+3
    assert r["home_form_points_5"] == 10  # W,D,L,W,W
    assert r["home_goals_for_avg_5"] == pytest.approx(7 / 5)
    assert r["home_goals_against_avg_5"] == pytest.approx(5 / 5)
    assert r["home_rest_days_raw"] == pytest.approx(7.0)
    assert r["home_rest_days_capped"] == pytest.approx(7.0)
    assert r["home_season_break_flag"] == 0.0
    assert r["home_win_streak"] == 2 and r["home_loss_streak"] == 0
    assert r["away_rest_days_raw"] == pytest.approx(28.0)  # D last played day 14
    assert r["away_win_streak"] == 0 and r["away_loss_streak"] == 0


def test_every_feature_has_an_availability_flag_and_spec(league):
    values = feats(league).values
    for name in produced_names():
        assert name in values and name + AVAIL_SUFFIX in values
        assert values[name + AVAIL_SUFFIX] == (0.0 if values[name] is None else 1.0)
        assert spec_for(name).leakage_rule


def test_unavailable_reasons_distinguish_the_cases(league):
    r = feats(league)
    # some history but fewer matches than the window -> insufficient_history
    assert r.values["home_form_points_10"] is None
    assert r.reasons["home_form_points_10"] == "insufficient_history"
    assert r.reasons["away_form_points_3"] == "insufficient_history"  # D has only 2 matches
    assert r.reasons["home_win_rate"] == "insufficient_history"
    # the very first fixture of the dataset: nothing exists yet -> dataset_start
    first = feats(league, m(50, "A", "B", 0, 0, 0), cutoff=T0 - timedelta(days=1))
    assert set(first.reasons.values()) == {"dataset_start"}
    # a team that never played although an EARLIER SEASON exists -> new_team
    prev = [m(60, "A", "B", 1, 0, -300, season="2022-23"), m(61, "B", "A", 1, 1, -290, season="2022-23")]
    res = feats(prev, m(62, "Z", "A", 0, 0, 5))
    assert res.reasons["home_form_points_3"] == "new_team"
    assert res.values["home_form_points_3" + AVAIL_SUFFIX] == 0.0


def test_missing_history_is_none_with_flag_not_zero():
    empty = feats([], FX).values
    assert empty["home_rest_days_raw"] is None and empty["home_win_streak"] is None
    assert empty["home_win_streak" + AVAIL_SUFFIX] == 0.0


# ---------------------------------------------------------------- rest-day policy
def test_rest_days_are_capped_and_season_break_is_flagged():
    prev = [m(1, "A", "B", 1, 0, -200, season="2022-23")]
    nxt = m(2, "A", "B", 0, 0, 0, season="2023-24")  # 200 days later, new season
    r = compute_features(nxt, MatchHistory(prev), nxt.kickoff_utc, DEFAULT_CONFIG)
    assert r.values["home_rest_days_raw"] == pytest.approx(200.0)
    assert r.values["home_rest_days_capped"] == 30.0 and r.values["home_season_break_flag"] == 1.0
    tight = FeaturesConfig(feature_version="fv2", rest_days_cap=100, result_lag_hours=3)
    capped = compute_features(nxt, MatchHistory(prev), nxt.kickoff_utc, tight)
    assert capped.values["home_rest_days_capped"] == 100.0


# ----------------------------------------------------------- availability semantics
def test_result_visible_only_after_its_availability_time(league):
    extra = m(50, "A", "B", 5, 0, 41)
    avail = extra.kickoff_utc + LAG
    before = feats([*league, extra], cutoff=avail - timedelta(minutes=1)).values
    at = feats([*league, extra], cutoff=avail).values
    assert before["home_rest_days_raw"] == pytest.approx(7.0)  # not yet visible
    assert at["home_rest_days_raw"] == pytest.approx(1.0) and at["home_win_streak"] == 3


@pytest.mark.parametrize(
    "status",
    [
        FixtureStatus.POSTPONED, FixtureStatus.SCHEDULED, FixtureStatus.CANCELLED,
        FixtureStatus.ABANDONED, FixtureStatus.RESCHEDULED, FixtureStatus.IN_PROGRESS,
    ],
)  # fmt: skip
def test_non_finished_matches_never_enter_history(league, status):
    ghost = m(70, "A", "B", 9, 9, 40, status=status)
    # even carrying a score and an availability time, a non-finished match is ignored
    ghost = replace(ghost, home_goals=9, away_goals=0, result_available_at_utc=ghost.kickoff_utc + LAG)
    assert feats([*league, ghost]).values == feats(league).values


def test_matches_without_known_result_time_are_ignored(league):
    unknown = replace(m(71, "A", "B", 5, 0, 40), result_available_at_utc=None)
    assert feats([*league, unknown]).values == feats(league).values


def test_future_and_current_fixture_do_not_matter(league):
    base = feats(league)
    assert feats([*league, m(60, "A", "D", 9, 9, 50)]).values == base.values
    own = replace(FX, home_goals=7, away_goals=0, result_available_at_utc=FX.kickoff_utc + LAG)
    assert feats([*league, own], own).values == base.values


def test_extras_never_influence_features(league):
    rich = [replace(x, extras=MappingProxyType({"home_shots": 99.0, "closing_avg_H": 1.5})) for x in league]
    assert feats(rich).values == feats(league).values


def test_cutoff_after_kickoff_rejected(league):
    with pytest.raises(ValueError):
        feats(league, cutoff=FX.kickoff_utc + timedelta(seconds=1))


def test_snapshots_carry_versions_lineage_and_are_deterministic(league):
    snaps = build_snapshots(league + [FX], DV, DEFAULT_CONFIG)
    assert len(snaps) == 9
    for s in snaps:
        assert s.data_version == DV and s.feature_version == "fv2"
        assert all(t <= s.information_cutoff for t in s.available_at.values())
        assert s.generated_at == s.information_cutoff <= s.kickoff_utc
    again = build_snapshots(league + [FX], DV, DEFAULT_CONFIG)
    assert again == snaps and content_hash(again) == content_hash(snaps)


def test_opponent_strength_and_venue_rate_on_a_longer_season():
    rng = random.Random(1)
    teams = [f"T{i}" for i in range(6)]
    rows, day, i = [], 0, 0
    for rnd in range(24):
        for a, b in [(0, 1), (2, 3), (4, 5)]:
            h, aw = (teams[a], teams[b]) if rnd % 2 else (teams[b], teams[a])
            rows.append(m(i, h, aw, rng.randint(0, 3), rng.randint(0, 3), day))
            i += 1
        day += 7
    fx = m(999, "T0", "T1", 0, 0, day)
    r = compute_features(fx, MatchHistory(rows), fx.kickoff_utc)
    expected = MatchHistory(rows).ppg("T1", fx.kickoff_utc, 5)[0]  # T1 is T0's only opponent
    assert r.values["home_opp_ppg_5"] == pytest.approx(expected)
    assert r.values["home_win_rate"] is not None


# --------------------------------------------------------------------- registry
def test_registry_contract_and_xg_is_experimental_only():
    for spec in [*REGISTRY, *EXPERIMENTAL]:
        assert spec.source and spec.window and spec.aggregation
        assert spec.available_at and spec.leakage_rule
    assert {s.name for s in REGISTRY} >= {
        "form_points_3", "form_points_5", "form_points_10", "goals_for_avg_5", "goals_against_avg_5",
        "home_win_rate", "away_win_rate", "rest_days_raw", "rest_days_capped", "season_break_flag",
        "win_streak", "loss_streak", "opp_ppg_5",
    }  # fmt: skip
    assert all(s.status == "experimental" for s in EXPERIMENTAL)
    produced = " ".join(produced_names())
    assert "xg" not in produced  # 100%-NaN xG columns are not part of the active feature set
    assert "rest_days_raw" in produced
    with pytest.raises(KeyError):
        spec_for("mystery_feature")


def test_registry_hash_detects_definition_changes(monkeypatch):
    before = registry_hash()
    assert registry_hash() == before
    monkeypatch.setattr(reg, "FEATURE_VERSION", "fv99")
    assert registry_hash() != before
    monkeypatch.setattr(reg, "FEATURE_VERSION", "fv2")
    changed = REGISTRY[0].model_copy(update={"window": "last 4 matches"})
    monkeypatch.setattr(reg, "REGISTRY", [changed, *REGISTRY[1:]])
    assert registry_hash() != before


# ---------------------------------------------------------------- leakage audit
def synthetic_season(n_rounds=30, seed=3):
    rng = random.Random(seed)
    teams = [f"T{i}" for i in range(8)]
    rows, day, i = [], 0, 0
    for _ in range(n_rounds):
        order = teams[:]
        rng.shuffle(order)
        for k in range(0, 8, 2):
            extras = {"home_shots": rng.randint(5, 20) * 1.0, "closing_avg_H": 1 + rng.random() * 3}
            season = "2023-24" if day < 100 else "2024-25"
            rows.append(
                m(i, order[k], order[k + 1], rng.randint(0, 4), rng.randint(0, 4), day + rng.random(),
                  season=season, extras=extras)
            )  # fmt: skip
            i += 1
        day += 7
    return rows


def test_leakage_audit_clean_on_real_implementation():
    assert audit_leakage(synthetic_season(), n_samples=200, seed=1) == []


FAR = timedelta(days=400)


def _broken(patch):
    def fn(fx, history, cutoff):
        res = compute_features(fx, history, cutoff)
        patch(res, fx, history, cutoff)
        return res

    return fn


BROKEN = {
    "current_result": lambda r, fx, h, c: r.values.update(home_form_points_5=float(fx.home_goals)),
    "future_goals": lambda r, fx, h, c: r.values.update(
        home_goals_for_avg_5=float(sum(x.gf for x in h.eligible(fx.home_id, c + FAR)[-5:]))
    ),
    "future_fixtures_count": lambda r, fx, h, c: r.values.update(
        home_win_streak=float(len(h.eligible(fx.home_id, c + FAR)))
    ),
    "future_rolling_window": lambda r, fx, h, c: r.values.update(
        home_form_points_3=float(sum(x.points for x in h.eligible(fx.home_id, c + FAR)[-3:]))
    ),
    "future_opponent_strength": lambda r, fx, h, c: r.values.update(
        home_opp_ppg_5=(h.ppg(fx.away_id, c + FAR, 1) or (0.0,))[0]
    ),
    "future_standings": lambda r, fx, h, c: r.values.update(
        home_loss_streak=(h.ppg(fx.home_id, c + FAR, 1) or (0.0,))[0]
    ),
    "post_match_statistics": lambda r, fx, h, c: r.values.update(
        home_goals_against_avg_5=fx.extras["home_shots"]
    ),
    "closing_odds_not_available_at_cutoff": lambda r, fx, h, c: r.values.update(
        away_form_points_3=fx.extras["closing_avg_H"]
    ),
    "result_published_after_cutoff": lambda r, fx, h, c: r.values.update(
        away_win_streak=float(len(h.eligible(fx.away_id, c + timedelta(days=10))))
    ),
}


@pytest.mark.parametrize("name", sorted(BROKEN))
def test_leakage_audit_catches_intentionally_broken_implementation(name):
    fn = _broken(BROKEN[name])
    assert audit_leakage(synthetic_season(), n_samples=120, seed=5, feature_fn=fn), name


def test_leakage_audit_ignoring_the_requested_cutoff_is_caught():
    def fn(fx, history, cutoff):
        return compute_features(fx, history, fx.kickoff_utc)  # always uses kickoff instead of cutoff

    assert audit_leakage(synthetic_season(), n_samples=120, seed=2, feature_fn=fn)


def test_audit_is_clean_even_when_postponed_matches_are_present():
    matches = synthetic_season(10)
    ghost = replace(
        matches[3], fixture_id="ghost", status=FixtureStatus.POSTPONED, home_goals=None,
        away_goals=None, result_available_at_utc=None,
    )  # fmt: skip
    assert audit_leakage([*matches, ghost], n_samples=80, seed=9) == []
