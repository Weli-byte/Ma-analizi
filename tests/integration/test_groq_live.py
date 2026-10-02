"""REAL Groq call (groq SDK). Fails (never skips) while GROQ_API_KEY is absent.
Run: pytest -m live tests/integration/test_groq_live.py"""

import pytest

from ._live import run_live

pytestmark = pytest.mark.live


def test_groq_live_structured_forecast():
    run_live("groq")
