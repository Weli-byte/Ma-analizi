"""S14 live engine (ADR 0029). Feed payloads are REAL captures (OpenLigaDB Bundesliga matches,
football-data.org matches). Replays truncate a real match's real goal list to what had happened
by a chosen minute; the clock and the pre-match goal rates are test INPUTS, nothing is invented."""

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from scipy.stats import skellam

from src.live.engine import PrematchRates, process_snapshot
from src.live.events import EventType, MatchStatus
from src.live.feeds import derive_goal_events, infer_minute, parse_fdorg_match, parse_openligadb_match
from src.live.inplay import inplay_probs
from src.live.store import LiveLedgerConflict, LiveStore
from src.schemas import LivePredictionRecord, PredictionLedger, PredictionRecord

CAP = Path(__file__).parent / "fixtures" / "real_provider_captures"
OLDB = {
    m["matchID"]: m
    for m in json.loads((CAP / "openligadb_bl1_matches.json").read_text(encoding="utf-8"))["matches"]
}
FDORG = {m["id"]: m for m in json.loads((CAP / "fdorg_matches.json").read_text(encoding="utf-8"))["matches"]}
PEN, OWN, UPCOMING = 83183, 83156, 83192
RATES = PrematchRates(2.3, 0.9, "test input rates")


def kickoff(m):
    return datetime.fromisoformat(m["matchDateTimeUTC"].replace("Z", "+00:00"))


def replay_payload(m, minute: int):
    """The REAL match as the feed would have shown it at match minute `minute`."""
    p = copy.deepcopy(m)
    p["goals"] = [g for g in p["goals"] if g["matchMinute"] <= minute]
    p["matchIsFinished"], p["matchResults"] = False, []
    return p


# ---------------------------------------------------------------------- real-event normalization
def test_real_penalty_match_normalizes_goals_penalty_team_minute_and_score():
    now = kickoff(OLDB[PEN]) + timedelta(hours=3)
    snap = parse_openligadb_match(OLDB[PEN], now, "sha")
    assert snap.status == MatchStatus.FINISHED and snap.status_source == "reported" and snap.score == (7, 0)
    assert len(snap.events) == 7 and all(e.team == "home" for e in snap.events)
    pen = [e for e in snap.events if e.type == EventType.PENALTY_GOAL]
    assert [(e.minute, e.player, e.score_after) for e in pen] == [(39, "H. Kane", (2, 0))]
    assert [e.score_after for e in snap.events][-1] == (7, 0)
    assert snap.capabilities["cards"] is False and snap.capabilities["substitutions"] is False


def test_real_own_goal_is_credited_to_the_side_whose_score_rose():
    snap = parse_openligadb_match(OLDB[OWN], kickoff(OLDB[OWN]) + timedelta(hours=3), "sha")
    own = next(e for e in snap.events if e.type == EventType.OWN_GOAL)
    assert (own.minute, own.team, own.score_after) == (57, "home", (3, 1))
    away = next(e for e in snap.events if e.score_after == (1, 1))
    assert away.team == "away" and away.type == EventType.GOAL


def test_not_started_match_has_no_score_no_events_and_inferred_status():
    m = OLDB[UPCOMING]
    snap = parse_openligadb_match(m, kickoff(m) - timedelta(hours=2), "sha")
    assert snap.status == MatchStatus.NOT_STARTED and snap.status_source == "inferred_from_clock"
    assert snap.score is None and snap.events == ()


def test_football_data_org_real_payloads_and_unknown_status_never_guessed():
    fin = parse_fdorg_match(FDORG[560542], datetime(2026, 10, 2, tzinfo=UTC), "sha")
    assert (fin.status, fin.score, fin.events) == (MatchStatus.FINISHED, (3, 0), ())
    sched = parse_fdorg_match(FDORG[560593], datetime(2026, 10, 2, tzinfo=UTC), "sha")
    assert sched.status == MatchStatus.NOT_STARTED and sched.score is None
    odd = copy.deepcopy(FDORG[560542])
    odd["status"] = "SUSPENDED"
    assert parse_fdorg_match(odd, datetime(2026, 10, 2, tzinfo=UTC), "sha").status == MatchStatus.UNKNOWN


def test_goals_derived_from_score_change_are_labelled_and_carry_no_invented_detail():
    snap = parse_fdorg_match(FDORG[560542], datetime(2026, 10, 2, tzinfo=UTC), "sha")  # real 3-0
    ev = derive_goal_events((1, 0), snap)
    assert [(e.team, e.score_after) for e in ev] == [("home", (2, 0)), ("home", (3, 0))]
    assert all(e.derived_from_score_change and e.minute is None and e.player is None for e in ev)
    assert derive_goal_events(None, snap) == [] and derive_goal_events((3, 0), snap) == []


def test_minute_is_inferred_from_the_clock_and_labelled():
    k = datetime(2026, 10, 3, 15, 0, tzinfo=UTC)
    assert infer_minute(k, k - timedelta(minutes=1), MatchStatus.IN_PLAY, None) == (None, None)
    assert infer_minute(k, k + timedelta(minutes=20), MatchStatus.IN_PLAY, None) == (
        20,
        "inferred_from_clock",
    )
    assert infer_minute(k, k + timedelta(minutes=50), MatchStatus.IN_PLAY, None) == (
        45,
        "inferred_from_clock",
    )
    assert infer_minute(k, k + timedelta(minutes=75), MatchStatus.IN_PLAY, None) == (
        60,
        "inferred_from_clock",
    )
    assert infer_minute(k, k + timedelta(minutes=75), MatchStatus.IN_PLAY, 70) == (70, "last_event")
    assert infer_minute(k, k + timedelta(minutes=50), MatchStatus.HALF_TIME, None) == (
        45,
        "inferred_from_clock",
    )


# ------------------------------------------------------------------------------ in-play model
def test_inplay_probabilities_match_an_independent_skellam_computation():
    ph, pd, pa = inplay_probs(1.6, 1.1, 0, 0, 0)
    assert ph + pd + pa == pytest.approx(1.0)
    assert ph == pytest.approx(1 - skellam.cdf(0, 1.6, 1.1), abs=1e-6)
    assert pd == pytest.approx(skellam.pmf(0, 1.6, 1.1), abs=1e-6)
    a, d, b = inplay_probs(1.6, 1.1, 60, 1, 0)  # 1-0 with 30 minutes left
    assert a == pytest.approx(1 - skellam.cdf(-1, 1.6 / 3, 1.1 / 3), abs=1e-6)


def test_inplay_behaves_sensibly_with_score_and_time():
    sym = inplay_probs(1.3, 1.3, 0, 0, 0)
    assert sym[0] == pytest.approx(sym[2])
    lead_early, lead_late = inplay_probs(1.3, 1.3, 20, 1, 0), inplay_probs(1.3, 1.3, 85, 1, 0)
    assert lead_late[0] > lead_early[0] > sym[0]  # a lead is worth more as time runs out
    assert inplay_probs(1.3, 1.3, 89, 0, 0)[1] > sym[1]  # a draw gets likelier with no goals late
    with pytest.raises(ValueError):
        inplay_probs(0.0, 1.0, 10, 0, 0)


# ----------------------------------------------------------------- engine on a replayed real match
def tick(store, m, minute, rates=RATES, dv="dv-000000000000"):
    now = kickoff(m) + timedelta(minutes=minute + (15 if minute > 45 else 0), seconds=30)
    snap = parse_openligadb_match(replay_payload(m, minute), now, "sha")
    return process_snapshot(store, snap, rates, now, dv, "fv2"), snap


def test_replayed_real_match_updates_state_and_issues_immutable_live_forecasts(tmp_path):
    m = OLDB[PEN]
    store = LiveStore(tmp_path, f"oldb-{m['matchID']}")
    r0, _ = tick(store, m, 10)  # no goal yet: 0-0 at minute 10
    assert r0.status == "FORECAST" and (r0.forecast.score_home, r0.forecast.score_away) == (0, 0)
    r1, _ = tick(store, m, 20)  # real goal at 18'
    assert r1.status == "FORECAST" and r1.n_new_events == 1 and r1.forecast.p_home > r0.forecast.p_home
    again, _ = tick(store, m, 20)  # identical poll: nothing changes
    assert again.status == "NO_CHANGE" and again.n_new_events == 0
    r2, _ = tick(store, m, 41)  # penalty at 39'
    assert r2.forecast.score_home == 2 and r2.forecast.p_home > r1.forecast.p_home
    assert r2.forecast.minute_source in ("inferred_from_clock", "last_event")
    assert [e["type"] for e in store.events()] == ["goal", "penalty_goal"]
    assert len(store.predictions()) == 3
    assert all(p.calibration_status == "NOT_CALIBRATED" and p.mode == "LIVE" for p in store.predictions())


def test_finished_and_not_started_do_not_forecast_and_missing_inputs_are_reported(tmp_path):
    m = OLDB[PEN]
    fin = parse_openligadb_match(m, kickoff(m) + timedelta(hours=3), "sha")
    res = process_snapshot(
        LiveStore(tmp_path, "a"), fin, RATES, kickoff(m) + timedelta(hours=3), "dv-0", "fv2"
    )
    assert res.status == "FINISHED" and res.forecast is None
    up = OLDB[UPCOMING]
    pre = parse_openligadb_match(up, kickoff(up) - timedelta(hours=1), "sha")
    assert (
        process_snapshot(LiveStore(tmp_path, "b"), pre, RATES, kickoff(up), "dv-0", "fv2").status
        == "STATE_ONLY"
    )
    store = LiveStore(tmp_path, "c")
    r, _ = tick(store, m, 30, rates=None)  # a league without pre-match goal rates
    assert r.status == "STATE_ONLY" and "prematch_rates" in r.reason and store.predictions() == []


def test_live_ledger_is_idempotent_and_rejects_a_changed_forecast(tmp_path):
    m = OLDB[PEN]
    store = LiveStore(tmp_path, "x")
    res, _ = tick(store, m, 20)
    rec = res.forecast
    assert store.append_prediction(rec) is False  # same content: idempotent
    changed = rec.model_copy(update={"p_home": rec.p_home - 0.01, "p_away": rec.p_away + 0.01})
    with pytest.raises(LiveLedgerConflict):
        store.append_prediction(changed)


def test_cards_are_never_assumed_state_has_no_card_counts(tmp_path):
    m = OLDB[OWN]
    store = LiveStore(tmp_path, "y")
    res, snap = tick(store, m, 60)
    assert snap.capabilities["cards"] is False
    last = store.last_state()
    assert not any("card" in k for k in last)  # no fabricated "0 red cards"


# ------------------------------------------------------------------------- PREMATCH / LIVE separation
def test_live_and_prematch_records_cannot_be_mixed():
    ko = datetime(2026, 10, 10, 15, 0, tzinfo=UTC)
    base = dict(
        fixture_id="f", model_id="inplay_poisson", model_version="1.0.0", data_version="dv-0",
        feature_version="fv2", kickoff_utc=ko, state_hash="a" * 16, status="IN_PLAY", score_home=0,
        score_away=0, prematch_rate_home=1.2, prematch_rate_away=1.0, p_home=0.4, p_draw=0.3, p_away=0.3,
    )  # fmt: skip
    with pytest.raises(ValidationError):  # state observed before kickoff is a PRE-MATCH matter
        LivePredictionRecord(**base, observed_at=ko - timedelta(minutes=5), generated_at=ko)
    live = LivePredictionRecord(
        **base, observed_at=ko + timedelta(minutes=30), generated_at=ko + timedelta(minutes=30)
    )
    with pytest.raises(ValidationError):  # a pre-match record cannot be generated after kickoff
        PredictionRecord(
            fixture_id="f", model_id="elo", model_version="1.0.0", feature_version="fv2",
            data_version="dv-000000000000", kickoff_utc=ko, information_cutoff=ko - timedelta(hours=1),
            generated_at=ko + timedelta(minutes=30), p_home=0.4, p_draw=0.3, p_away=0.3,
        )  # fmt: skip
    with pytest.raises((TypeError, AttributeError, ValueError)):
        PredictionLedger().append(live)  # the pre-match ledger refuses a live record
    assert LivePredictionRecord.from_json(live.model_dump_json()).prediction_id == live.prediction_id
    tampered = json.loads(live.model_dump_json())
    tampered["p_home"], tampered["p_draw"] = 0.5, 0.2
    with pytest.raises(ValueError):
        LivePredictionRecord.from_json(json.dumps(tampered))
