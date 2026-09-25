import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from src.data.pipeline import run_pipeline
from src.features import (
    RESULT_LAG,
    MatchHistory,
    MatchRecord,
    audit_leakage,
    build_snapshots,
    compute_features,
    load_matches,
    registry_hash,
)
from src.features.builder import write_features
from src.features.registry import AVAIL_SUFFIX, REGISTRY, produced_names, spec_for

T0 = datetime(2024, 1, 1, 15, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[1]


def m(i, home, away, hg, ag, days, hxg=None, axg=None):
    return MatchRecord(f"f{i}", T0 + timedelta(days=days), home, away, hg, ag, hxg, axg)


@pytest.fixture
def league():
    """Team A results by day: 0 W 2-0(h), 7 W 1-0(a), 14 D 1-1(h), 21 L 0-2(a), 28 W 3-1(h), 35 W 2-1(a)."""
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


def feats(league, fixture=FX, cutoff=None):
    return compute_features(fixture, MatchHistory(league), cutoff or fixture.kickoff_utc)


def test_known_values(league):
    r = feats(league).values
    assert r["home_form_points_3"] == 6  # L,W,W -> 0+3+3
    assert r["home_form_points_5"] == 10  # D,L,W,W... last five = W,D,L,W,W -> 3+1+0+3+3
    assert r["home_goals_for_avg_5"] == pytest.approx(7 / 5)  # 1,1,0,3,2
    assert r["home_goals_against_avg_5"] == pytest.approx(5 / 5)  # 0,1,2,1,1
    assert r["home_rest_days"] == pytest.approx(7.0)
    assert r["home_win_streak"] == 2 and r["home_loss_streak"] == 0
    assert r["away_rest_days"] == pytest.approx(28.0)  # D last played day 14
    assert r["away_win_streak"] == 0 and r["away_loss_streak"] == 0


def test_insufficient_history_is_nan_with_flag_not_zero(league):
    r = feats(league).values
    assert r["home_form_points_10"] is None and r["home_form_points_10" + AVAIL_SUFFIX] == 0.0
    assert r["away_form_points_3"] is None and r["away_form_points_3" + AVAIL_SUFFIX] == 0.0
    assert r["home_win_rate"] is None  # only 3 home matches (< 5)
    assert r["home_opp_ppg_5"] is None  # opponents lack 5 matches
    assert r["home_xg_avg_10"] is None  # no xG in data
    assert r["home_form_points_3" + AVAIL_SUFFIX] == 1.0
    empty = feats([], FX).values  # no history at all
    assert empty["home_rest_days"] is None and empty["home_win_streak"] is None


def test_result_available_only_after_lag(league):
    extra = m(50, "A", "B", 5, 0, 41)  # kicks off day 41 15:00
    avail = extra.kickoff_utc + RESULT_LAG
    before = feats([*league, extra], cutoff=avail - timedelta(minutes=1)).values
    at = feats([*league, extra], cutoff=avail).values
    assert before["home_rest_days"] == pytest.approx(7.0)  # extra not yet visible
    assert at["home_rest_days"] == pytest.approx(1.0)  # visible exactly at availability
    assert at["home_win_streak"] == 3


def test_future_and_current_fixture_do_not_matter(league):
    base = feats(league)
    future = m(60, "A", "D", 9, 9, 50)
    assert feats([*league, future]).values == base.values
    own = replace(FX, home_goals=7, away_goals=0)
    assert feats([*league, own], own).values == base.values
    assert feats([*league, replace(FX, home_goals=0, away_goals=8)]).values == base.values


def test_cutoff_after_kickoff_rejected(league):
    with pytest.raises(ValueError):
        feats(league, cutoff=FX.kickoff_utc + timedelta(seconds=1))


def test_available_at_never_exceeds_cutoff_and_snapshots_validate(league):
    snaps = build_snapshots(league + [FX])
    assert len(snaps) == 9
    for s in snaps:  # schema itself rejects available_at > cutoff
        assert all(t <= s.information_cutoff for t in s.available_at.values())
        assert s.generated_at == s.information_cutoff <= s.kickoff_utc
    assert build_snapshots(league + [FX]) == snaps  # deterministic


def test_opponent_strength_and_xg_when_available():
    # 8 teams x 8 rounds -> enough matches for opponent ppg and xG windows
    rng = random.Random(1)
    teams = [f"T{i}" for i in range(6)]
    rows, day, i = [], 0, 0
    for rnd in range(24):
        for a, b in [(0, 1), (2, 3), (4, 5)]:
            h, aw = (teams[a], teams[b]) if rnd % 2 else (teams[b], teams[a])
            rows.append(m(i, h, aw, rng.randint(0, 3), rng.randint(0, 3), day, 1.2, 0.9))
            i += 1
        day += 7
    fx = m(999, "T0", "T1", 0, 0, day)
    r = compute_features(fx, MatchHistory(rows), fx.kickoff_utc)
    assert r.values["home_xg_avg_10"] == pytest.approx(1.05)  # 5 home (1.2) + 5 away (0.9)
    # T1 is T0's only opponent -> opp_ppg_5 is exactly T1's ppg at the same cutoff
    expected = MatchHistory(rows).ppg("T1", fx.kickoff_utc, 5)[0]
    assert r.values["home_opp_ppg_5"] == pytest.approx(expected)
    assert r.values["home_win_rate"] is not None


def test_registry_contract_complete():
    for name in produced_names():
        spec = spec_for(name)
        assert spec.source and spec.window and spec.aggregation and spec.available_at
        assert spec.leakage_rule
    assert {s.name for s in REGISTRY} >= {
        "form_points_3", "form_points_5", "form_points_10", "goals_for_avg_5",
        "goals_against_avg_5", "xg_avg_10", "xga_avg_10", "home_win_rate", "away_win_rate",
        "rest_days", "win_streak", "loss_streak", "opp_ppg_5",
    }  # fmt: skip
    assert registry_hash() == registry_hash()
    with pytest.raises(KeyError):
        spec_for("mystery_feature")


def synthetic_season(n_rounds=30, seed=3):
    rng = random.Random(seed)
    teams = [f"T{i}" for i in range(8)]
    rows, day, i = [], 0, 0
    for _ in range(n_rounds):
        order = teams[:]
        rng.shuffle(order)
        for k in range(0, 8, 2):
            rows.append(
                m(
                    i,
                    order[k],
                    order[k + 1],
                    rng.randint(0, 4),
                    rng.randint(0, 4),
                    day + rng.random(),
                    rng.random() * 3,
                    rng.random() * 3,
                )
            )
            i += 1
        day += 7
    return rows


def test_leakage_audit_clean_on_real_implementation():
    assert audit_leakage(synthetic_season(), n_samples=150, seed=1) == []


def test_leakage_audit_catches_use_of_current_result():
    def leaky(fx, history, cutoff):
        res = compute_features(fx, history, cutoff)
        res.values["home_form_points_5"] = float(fx.home_goals)  # peeks at own result
        return res

    assert audit_leakage(synthetic_season(), n_samples=50, seed=1, feature_fn=leaky)


def test_leakage_audit_catches_ignoring_cutoff():
    def leaky(fx, history, cutoff):
        return compute_features(fx, history, fx.kickoff_utc)  # ignores requested cutoff

    assert audit_leakage(synthetic_season(), n_samples=100, seed=2, feature_fn=leaky)


def test_leakage_audit_catches_post_cutoff_records():
    def leaky(fx, history, cutoff):
        res = compute_features(fx, history, cutoff)
        later = history.eligible(fx.home_id, fx.kickoff_utc + timedelta(days=400))
        res.values["home_goals_for_avg_5"] = float(sum(x.gf for x in later[-5:]))
        return res

    assert audit_leakage(synthetic_season(), n_samples=50, seed=4, feature_fn=leaky)


def test_end_to_end_from_pipeline_dataset(tmp_path):
    header = "Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR"
    rows = [
        "E0,12/08/2023,15:00,Arsenal,Chelsea,2,1,H",
        "E0,19/08/2023,15:00,Chelsea,Liverpool,0,0,D",
        "E0,26/08/2023,15:00,Liverpool,Arsenal,1,3,A",
    ]
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "E0_2324.csv").write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    run_pipeline(
        raw, tmp_path / "out", "dv1", tmp_path / "rep", ROOT / "configs" / "team_aliases.yaml"
    )
    matches = load_matches(tmp_path / "out" / "dv1" / "football.duckdb")
    assert len(matches) == 3 and matches[0].kickoff_utc.tzinfo is not None
    snaps = build_snapshots(matches)
    assert snaps[0].values["home_form_points_3"] is None  # first match: no history
    assert snaps[2].values["away_rest_days"] == pytest.approx(14.0)  # Arsenal last played Aug 12
    target = write_features(snaps, tmp_path / "feat")
    con = duckdb.connect()
    assert (
        con.execute(f"SELECT count(*) FROM read_parquet('{target.as_posix()}')").fetchone()[0] == 3
    )


@pytest.mark.skipif(
    not (ROOT / "data/processed/dv1/football.duckdb").exists(), reason="dv1 not built"
)
def test_real_dataset_audit_clean_and_complete():
    matches = load_matches(ROOT / "data/processed/dv1/football.duckdb")
    assert len(matches) == 3800
    assert audit_leakage(matches, n_samples=60, seed=7) == []
    snaps = build_snapshots(matches)
    assert len(snaps) == 3800
    first = min(snaps, key=lambda s: s.kickoff_utc)
    assert first.values["home_form_points_3"] is None  # season-opening: nothing before it
