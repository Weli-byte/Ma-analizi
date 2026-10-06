"""REAL live-feed calls. Run: pytest -m live tests/integration/test_live_feeds.py
Polls two real, already finished matches (stable results) through the same code the live runner uses."""

import os
from datetime import UTC, datetime

import pytest

from src.cli_utils import load_dotenv
from src.live.events import EventType, MatchStatus
from src.live.feeds import FootballDataOrgLiveFeed, OpenLigaDBFeed

pytestmark = pytest.mark.live


def test_openligadb_real_poll_of_a_finished_bundesliga_match():
    snap = OpenLigaDBFeed().poll("83183", datetime.now(UTC))  # Bayern 7-0 Union Berlin (2026-09-18)
    assert snap.status == MatchStatus.FINISHED and snap.score == (7, 0) and len(snap.events) == 7
    assert any(e.type == EventType.PENALTY_GOAL and e.minute == 39 for e in snap.events)
    assert len(snap.raw_sha256) == 64


def test_football_data_org_real_poll_of_a_finished_match():
    load_dotenv()
    key = os.environ.get("FOOTBALL_DATA_ORG_API_KEY")
    if not key:
        pytest.fail("FOOTBALL_DATA_ORG_API_KEY not set -> NOT_CONFIGURED")
    snap = FootballDataOrgLiveFeed(key).poll("560542", datetime.now(UTC))
    assert snap.status == MatchStatus.FINISHED and snap.score == (3, 0) and snap.events == ()
