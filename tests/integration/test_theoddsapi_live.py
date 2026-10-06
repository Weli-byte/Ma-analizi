"""REAL The Odds API call. Fails (never skips) while THE_ODDS_API_KEY is absent: the adapter is
UNVERIFIED until a real response was received and parsed. Run: pytest -m live tests/integration/test_theoddsapi_live.py"""

import os
from datetime import UTC, datetime

import pytest

from src.cli_utils import load_dotenv
from src.odds.theoddsapi import KEY_ENV, TheOddsApiFeed

pytestmark = pytest.mark.live


def test_theoddsapi_live_returns_exact_timestamped_h2h_quotes():
    load_dotenv()
    key = os.environ.get(KEY_ENV)
    if not key:
        pytest.fail(f"${KEY_ENV} not set -> NOT_CONFIGURED (adapter unverified)")
    events = TheOddsApiFeed(key).fetch("PL", datetime.now(UTC))
    quotes = [q for e in events for q in e["quotes"]]
    assert quotes, "no h2h quotes returned"
    exact = [q for q in quotes if q.timestamp_quality == "exact"]
    assert exact and all(
        q.provider_timestamp and q.source_latency_s is not None and q.source_latency_s >= 0 for q in exact
    )
    assert key not in repr(quotes[0].provenance)
