"""Canonical LLM forecast contract (ADR 0024): every provider gets the SAME task and must return
this object. Validation is Pydantic; malformed output is rejected, never invented or patched.

Normalization policy (documented, deliberate): probabilities must each lie in [0, 1] and sum to
1.0 within `PROB_TOL` (providers round to a few decimals). Inside that tolerance the values are
divided by their sum so the stored PredictionRecord sums to 1 exactly; outside it the output is
REJECTED. The raw, un-normalized values stay in the stored raw response (hashed).
"""

import json
import math

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

SCHEMA_VERSION = "forecast-output-v1"
PROB_TOL = 1e-3


class MalformedLLMOutput(ValueError):
    pass


class ForecastOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    home_probability: float = Field(ge=0.0, le=1.0)
    draw_probability: float = Field(ge=0.0, le=1.0)
    away_probability: float = Field(ge=0.0, le=1.0)
    predicted_home_goals: float | None = Field(default=None, ge=0.0)
    predicted_away_goals: float | None = Field(default=None, ge=0.0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    analysis_summary: str | None = Field(default=None, max_length=600)

    @model_validator(mode="after")
    def _sums_to_one(self) -> "ForecastOutput":
        total = self.home_probability + self.draw_probability + self.away_probability
        if math.isnan(total) or not math.isclose(total, 1.0, abs_tol=PROB_TOL):
            raise ValueError(f"probabilities sum to {total}, not 1.0 (tol {PROB_TOL})")
        return self


def response_json_schema() -> dict:
    """Provider-facing JSON Schema. Strict-mode compatible (all keys required, optional ones
    nullable, no min/max keywords -- those are enforced by `ForecastOutput` after the call)."""
    num, nnum = {"type": "number"}, {"type": ["number", "null"]}
    props = {
        "home_probability": num,
        "draw_probability": num,
        "away_probability": num,
        "predicted_home_goals": nnum,
        "predicted_away_goals": nnum,
        "confidence": nnum,
        "analysis_summary": {"type": ["string", "null"]},
    }
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


def parse_forecast(text: str) -> ForecastOutput:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise MalformedLLMOutput(f"not valid JSON: {e}") from e
    try:
        return ForecastOutput.model_validate(data)
    except ValidationError as e:
        raise MalformedLLMOutput(
            f"schema violation: {e.error_count()} error(s): {e.errors()[0]['msg']}"
        ) from e
