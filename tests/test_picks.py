"""Bet suggestion maths (ADR 0039) on value rows computed from the REAL forecasts and REAL exact quotes."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from src.odds.picks import kelly_stake_pct, make_picks
from src.odds.theoddsapi import parse_event
from src.odds.value import value_row
from src.schemas import PredictionRecord

CAP = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "real_provider_captures"
SAMPLE = json.loads((CAP / "theoddsapi_event.json").read_text(encoding="utf-8"))
EV = parse_event(SAMPLE["event"], datetime.fromisoformat(SAMPLE["captured_at_utc"]), "sha")
PREDS = [
    PredictionRecord.from_json(x)
    for x in (CAP / "forecast_arsenal_leeds_predictions.jsonl").read_text(encoding="utf-8").splitlines()
    if x.strip()
]


def rows():
    out = []
    for p in PREDS:
        r = value_row(p, EV["quotes"], EV["kickoff_utc"], "arsenal-leeds")
        out.append(
            {
                "fixture_id": r.fixture_id,
                "model_id": r.model_id,
                "status": r.status,
                "bookmaker": r.bookmaker,
                "observed_at": r.observed_at.isoformat(),
                "odds": r.odds,
                "model_probs": r.model_probs,
                "market_probs_devig": r.market_probs_devig,
                "edge": r.edge,
                "ev": r.ev,
            }  # fmt: skip
        )
    return out


def test_kelly_hint_is_fractional_capped_and_never_negative():
    assert kelly_stake_pct(0.5, 2.5, 0.25, 2.0) == pytest.approx(2.0 if 100 * 0.25 * 0.25 > 2 else 6.25)
    assert kelly_stake_pct(0.60, 2.5, 0.25, 50) == pytest.approx(100 * 0.25 * (0.6 * 2.5 - 1) / 1.5, abs=0.01)
    assert kelly_stake_pct(0.30, 2.0, 0.25, 2.0) == 0.0  # negative edge -> no stake


def test_real_rows_give_picks_only_above_thresholds_and_aggregate_models():
    rs = rows()
    assert all(r["status"] == "ELIGIBLE" for r in rs)
    none = make_picks(rs, 1.0, 5.0, 0.25, 2.0)
    assert none == []
    loose = make_picks(rs, -1.0, -1.0, 0.25, 2.0)  # every selection qualifies: pure aggregation check
    assert {p["selection"] for p in loose} == {"H", "D", "A"}
    for p in loose:
        assert p["models_agreeing"] == p["models_evaluated"] == len(PREDS)
        assert p["confidence"] == "MEDIUM" and p["stake_hint_pct_of_bankroll"] <= 2.0
        assert p["ev_per_unit"] == pytest.approx(p["mean_model_prob"] * p["odds"] - 1, abs=1e-3)


def test_ineligible_rows_never_become_suggestions():
    rs = rows()
    for r in rs:
        r["status"] = "NOT_ELIGIBLE"
    assert make_picks(rs, -1.0, -1.0, 0.25, 2.0) == []
