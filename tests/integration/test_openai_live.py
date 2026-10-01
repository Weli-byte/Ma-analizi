"""REAL OpenAI call (Responses API). Run: pytest -m live tests/integration/test_openai_live.py"""

import pytest

from ._live import run_live

pytestmark = pytest.mark.live


def test_openai_live_structured_forecast():
    run_live("openai")
