"""REAL football-data.org error paths: an invalid token is answered with a 4xx by the real server, and a
burst beyond the free tier's 10 requests/minute is answered 429. Run:
pytest -m live tests/integration/test_fdorg_errors_live.py"""

import os
import time

import pytest

from src.cli_utils import load_dotenv
from src.ingestion.football_data_org import FootballDataOrgProvider
from src.ingestion.provider import ProviderError, RateLimitedError

pytestmark = pytest.mark.live


def test_invalid_token_is_rejected_by_the_real_server_as_a_provider_error():
    with pytest.raises(ProviderError) as e:
        FootballDataOrgProvider("0" * 32).list_leagues()
    # the real server answers 403 for a well-formed unknown token and 400 for a malformed one
    assert ("HTTP 403" in str(e.value) or "HTTP 400" in str(e.value)) and not isinstance(
        e.value, RateLimitedError
    )


def test_burst_beyond_the_free_tier_limit_is_a_real_429():
    load_dotenv()
    key = os.environ.get("FOOTBALL_DATA_ORG_API_KEY")
    if not key:
        pytest.fail("FOOTBALL_DATA_ORG_API_KEY not set -> NOT_CONFIGURED")
    provider = FootballDataOrgProvider(key)
    try:
        with pytest.raises(RateLimitedError):
            for _ in range(40):  # free tier: 10 requests per minute; stops at the first real 429
                provider.list_seasons("PL")
    finally:
        time.sleep(65)  # let the per-minute quota reset so the other live tests are not starved
