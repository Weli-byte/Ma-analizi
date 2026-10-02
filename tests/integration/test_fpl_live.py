"""REAL Fantasy Premier League call (keyless, free). Run: pytest -m live tests/integration/test_fpl_live.py"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.data.teams import TeamDirectory
from src.ingestion.fpl import FplInjuryProvider
from src.ingestion.interfaces import AvailabilityStatus

pytestmark = pytest.mark.live
ROOT = Path(__file__).resolve().parents[2]


def test_fpl_live_returns_real_dated_availability_records():
    directory = TeamDirectory.load(ROOT / "configs" / "team_aliases.yaml")
    provider = FplInjuryProvider(directory)
    players = provider.list_availability()
    assert len(players) > 20 and provider.last_raw_sha256
    now = datetime.now(UTC)
    assert all(p.status != AvailabilityStatus.AVAILABLE for p in players)
    dated = [p for p in players if p.effective_at is not None]
    assert dated and all(p.effective_at <= now for p in dated)  # the provider never dates the future
    assert sum(p.team_id is not None for p in players) / len(players) > 0.95  # clubs resolve
