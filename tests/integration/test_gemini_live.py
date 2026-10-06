"""REAL Gemini call (google-genai). Run: pytest -m live tests/integration/test_gemini_live.py"""

import pytest

from ._live import run_live

pytestmark = pytest.mark.live


def test_gemini_live_structured_forecast():
    run_live("gemini")
