"""Shared helper for REAL provider smoke tests. No mocks, no monkeypatching, no fake HTTP.
A missing credential FAILS the test (never skips/passes): operational status needs a real call."""

import os
from pathlib import Path

import pytest

from src.cli_utils import load_dotenv
from src.config import load_config
from src.llm.budget import estimate_input_tokens, preflight
from src.llm.contract import parse_forecast
from src.llm.pricing import load_price_table
from src.llm.prompt import SYSTEM_PROMPT, build_user_prompt
from src.llm.providers import PROVIDERS

ROOT = Path(__file__).resolve().parents[2]

SNAPSHOT = {
    "purpose": "live API smoke check, not a benchmark fixture",
    "home_team": "Arsenal",
    "away_team": "Chelsea",
    "information_cutoff": "2025-01-01T00:00:00+00:00",
    "permitted_historical_features": {"home_form_points_5": 10.0, "away_form_points_5": 6.0},
}


def run_live(provider: str) -> None:
    load_dotenv()
    cfg = load_config("provider", ROOT / "configs")
    entry = cfg.providers[provider]
    key = os.environ.get(entry.api_key_env)
    if not key:
        pytest.fail(f"{provider}: ${entry.api_key_env} not set -> NOT_CONFIGURED (live test cannot pass)")
    if entry.model.upper() == "TBD":
        pytest.fail(f"{provider}: no model configured")
    prices = load_price_table()
    user = build_user_prompt(SNAPSHOT)
    preflight([(provider, entry.model, estimate_input_tokens(SYSTEM_PROMPT, user))], cfg.budget, prices)

    resp = PROVIDERS[provider].complete(
        SYSTEM_PROMPT, user, model=entry.model, api_key=key,
        timeout_s=cfg.budget.request_timeout_seconds,
        max_output_tokens=cfg.budget.max_output_tokens, retry_limit=cfg.budget.retry_limit,
    )  # fmt: skip

    assert resp.provider == provider and resp.text and key not in resp.text
    assert resp.latency_ms > 0 and len(resp.raw_response_hash) == 64
    assert resp.input_tokens and resp.output_tokens  # real usage metadata was returned
    out = parse_forecast(resp.text)  # Pydantic schema + sum-to-1 validation
    assert abs(out.home_probability + out.draw_probability + out.away_probability - 1) <= 1e-3
    cost = prices.estimate(provider, entry.model, resp.input_tokens, resp.output_tokens)
    assert cost is not None and cost < 0.005  # strict per-call cost ceiling
