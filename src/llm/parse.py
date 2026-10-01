"""Strict JSON parsing of an LLM provider's response (S8). A provider is free to say anything in
prose; this module enforces the contract, raising on anything else -- the runner retries a
malformed response rather than silently coercing it.
"""

import json
import math

REQUIRED_KEYS = ("home_probability", "draw_probability", "away_probability", "confidence")
PROB_TOL = 1e-3  # looser than PredictionRecord's 1e-6: LLM output is rounded by the provider


class MalformedLLMOutput(ValueError):
    pass


def parse_llm_output(text: str) -> dict:
    stripped = text.strip()
    if stripped.startswith("```"):  # tolerate a fenced block even though the prompt forbids it
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as e:
        raise MalformedLLMOutput(f"not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise MalformedLLMOutput(f"expected a JSON object, got {type(data).__name__}")
    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing:
        raise MalformedLLMOutput(f"missing keys: {missing}")
    probs = {}
    for k in ("home_probability", "draw_probability", "away_probability"):
        v = data[k]
        if not isinstance(v, int | float) or math.isnan(v) or not (0.0 <= v <= 1.0):
            raise MalformedLLMOutput(f"{k} must be a float in [0, 1], got {v!r}")
        probs[k] = float(v)
    total = sum(probs.values())
    if not math.isclose(total, 1.0, abs_tol=PROB_TOL):
        raise MalformedLLMOutput(f"probabilities sum to {total}, not 1.0")
    confidence = data["confidence"]
    if not isinstance(confidence, int | float) or not (0.0 <= confidence <= 1.0):
        raise MalformedLLMOutput(f"confidence must be a float in [0, 1], got {confidence!r}")
    reasoning = data.get("short_reasoning")
    if reasoning is not None and not isinstance(reasoning, str):
        raise MalformedLLMOutput(f"short_reasoning must be a string, got {type(reasoning).__name__}")
    return {
        "p_home": probs["home_probability"] / total,  # renormalized: provider rounding tolerated
        "p_draw": probs["draw_probability"] / total,
        "p_away": probs["away_probability"] / total,
        "confidence": float(confidence),
        "short_reasoning": reasoning,
    }
