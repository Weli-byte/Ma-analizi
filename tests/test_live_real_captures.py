"""football-data.org in-play payloads captured from REAL matches on 2026-10-11/12 (free tier, unedited apart from the
`captured_at_utc` wrapper added by `src.live.run`). They replace the docs-based assumption in `src/live/feeds.py` (ADR 0029)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.live.engine import MAX_CLOCK_MINUTE, PrematchRates, process_snapshot
from src.live.events import MatchStatus
from src.live.feeds import derive_goal_events, infer_minute, parse_fdorg_match
from src.live.store import LiveStore

CAPTURES = Path(__file__).parent / "fixtures" / "real_provider_captures"
OBSERVED = datetime(2026, 10, 12, 22, 40, tzinfo=UTC)


def _match(name: str) -> dict:
    return json.loads((CAPTURES / name).read_text(encoding="utf-8"))["match"]


def test_first_half_payload_has_a_running_score_and_no_half_time_score_yet():
    raw = _match("fdorg_in_play_first_half.json")
    assert raw["status"] == "IN_PLAY" and raw["score"]["halfTime"] == {"home": None, "away": None}
    snap = parse_fdorg_match(raw, OBSERVED)
    assert snap.status == MatchStatus.IN_PLAY and snap.status_source == "reported"
    assert snap.score == (0, 0)


def test_second_half_payload_carries_both_running_and_half_time_scores():
    raw = _match("fdorg_in_play_second_half.json")
    assert raw["score"]["halfTime"] == {"home": 1, "away": 0}
    snap = parse_fdorg_match(raw, OBSERVED)
    assert snap.status == MatchStatus.IN_PLAY and snap.score == (1, 0)


@pytest.mark.parametrize("name", ["fdorg_in_play_first_half.json", "fdorg_in_play_second_half.json"])
def test_the_free_tier_reports_no_minute_and_the_snapshot_says_so(name):
    raw = _match(name)
    assert "minute" not in raw and "injuryTime" not in raw  # real fact: the clock must be inferred
    snap = parse_fdorg_match(raw, OBSERVED)
    assert snap.capabilities["minute"] is False and snap.minute_reported is None
    assert snap.capabilities["goals"] is False  # no goal list either: goals are derived from score changes


def test_a_score_change_between_polls_becomes_one_derived_goal_event():
    first = parse_fdorg_match(_match("fdorg_in_play_first_half.json"), OBSERVED)
    later = parse_fdorg_match(_match("fdorg_in_play_second_half.json"), OBSERVED)
    (event,) = derive_goal_events(first.score, later)
    assert event.derived_from_score_change is True and event.minute is None and event.player is None


def test_the_free_feed_keeps_saying_in_play_long_after_full_time_so_the_inferred_minute_is_capped():
    # observed on all 45 captures: the last IN_PLAY poll came 117-131 minutes after kickoff (median 117)
    kickoff = datetime(2026, 10, 11, 22, 30, tzinfo=UTC)
    late = datetime(2026, 10, 12, 0, 40, tzinfo=UTC)  # 130 minutes after kickoff
    minute, source = infer_minute(kickoff, late, MatchStatus.IN_PLAY, None)
    assert source == "inferred_from_clock" and minute <= 120


def test_a_stale_in_play_status_long_after_kickoff_gets_no_forecast(tmp_path):
    raw = _match("fdorg_in_play_second_half.json")  # a real payload: IN_PLAY, score 1-0
    kickoff = datetime.fromisoformat(raw["utcDate"].replace("Z", "+00:00"))
    rates = PrematchRates(1.5, 1.2, "test input rates")
    late = kickoff + timedelta(minutes=130)  # the real feed still said IN_PLAY this late
    stale = process_snapshot(
        LiveStore(tmp_path, "stale"), parse_fdorg_match(raw, late), rates, late, "dv-0", "fv2"
    )
    assert stale.status == "STATE_ONLY" and "probably stale" in stale.reason and stale.forecast is None
    on_time = kickoff + timedelta(minutes=75)  # second half
    live = process_snapshot(
        LiveStore(tmp_path, "live"), parse_fdorg_match(raw, on_time), rates, on_time, "dv-0", "fv2"
    )
    assert live.status == "FORECAST" and MAX_CLOCK_MINUTE == 100
