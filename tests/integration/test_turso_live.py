"""REAL Turso round trip (free tier). Needs FORECAST_TURSO_URL and FORECAST_TURSO_TOKEN (a missing one FAILS the test).
Run: pytest -m live tests/integration/test_turso_live.py. Uses a throw-away user id and deletes it at the end."""

import os
import uuid
from pathlib import Path

import pytest

from src.cli_utils import load_dotenv
from src.web.store import UserStore

pytestmark = pytest.mark.live
ROOT = Path(__file__).resolve().parents[2]


def test_turso_store_persists_across_instances(tmp_path):
    load_dotenv(ROOT / ".env")
    assert os.environ.get("FORECAST_TURSO_URL") and os.environ.get("FORECAST_TURSO_TOKEN"), (
        "Turso is not configured"
    )
    uid = f"live-test-{uuid.uuid4().hex}"
    first = UserStore(tmp_path)
    assert first.backend == "turso"
    try:
        first.touch(uid, "guest", "Live Test", None)
        assert first.toggle(uid, "team-a") is True
        second = UserStore(
            tmp_path
        )  # a new instance (like a restarted serverless function) sees the same data
        assert second.favorites(uid) == ["team-a"]
        assert second.toggle(uid, "team-a") is False
        assert second.favorites(uid) == []
    finally:
        first.delete_user(uid)
