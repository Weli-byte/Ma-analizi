"""REAL API-Football free-plan calls. Fails (never skips) without API_FOOTBALL_KEY.
Run: pytest -m live tests/integration/test_apifootball_live.py"""

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.cli_utils import load_dotenv
from src.data.teams import TeamDirectory
from src.ingestion.api_football import ApiFootballProvider
from src.ingestion.provider import ProviderError

pytestmark = pytest.mark.live

DIRECTORY = TeamDirectory.load(Path(__file__).resolve().parents[2] / "configs" / "team_aliases.yaml")


def _key() -> str:
    load_dotenv()
    key = os.environ.get("API_FOOTBALL_KEY")
    if not key:
        pytest.fail("API_FOOTBALL_KEY not set -> NOT_CONFIGURED")
    return key


def test_injuries_for_today_are_returned_for_a_date_inside_the_free_window():
    prov = ApiFootballProvider(_key(), DIRECTORY)
    ps = prov.injuries_for_date(datetime.now(UTC).date())  # today is always inside [yesterday, tomorrow]
    assert prov.last_raw_sha256 and all(p.source == "api-football" for p in ps)


def test_a_date_outside_the_free_window_is_a_provider_error_not_zero_injuries():
    with pytest.raises(ProviderError, match="Free plans do not have access"):
        ApiFootballProvider(_key(), DIRECTORY).injuries_for_date(datetime(2026, 12, 25).date())
