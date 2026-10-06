"""REAL Anthropic call (Messages API). Fails (never skips) while ANTHROPIC_API_KEY is absent.
Run: pytest -m live tests/integration/test_anthropic_live.py"""

import pytest

from ._live import run_live

pytestmark = pytest.mark.live


def test_anthropic_live_structured_forecast():
    run_live("anthropic")
