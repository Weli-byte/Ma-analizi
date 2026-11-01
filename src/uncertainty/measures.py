"""Uncertainty measures of one 1X2 forecast (ADR 0095). No single invented "confidence score": each measure is reported by its own name.

- top probability (the market model's own 1X2; the identity is the champion calibrator, ADR 0089, so it is the calibrated value as far as calibration exists),
- entropy (nats) and the top-1 minus top-2 margin,
- disagreement: half the L1 distance between the model's and the de-vigged market's 1X2 (0 = identical, 1 = disjoint), only when exact odds exist,
- data flags: no exact odds (model only), a club without history (low-confidence label of ADR 0057 / predict.py), a stale forecast at the stage (ADR 0093).
"""

import math


def top(p: list[float]) -> float:
    return max(p)


def entropy(p: list[float]) -> float:
    return -sum(x * math.log(x) for x in p if x > 0)


def margin(p: list[float]) -> float:
    s = sorted(p, reverse=True)
    return s[0] - s[1]


def disagreement(model: list[float], market: list[float] | None) -> float | None:
    return None if market is None else 0.5 * sum(abs(a - b) for a, b in zip(model, market, strict=True))


def from_intel(i: dict) -> dict:
    """Measures of a published forecast (the public intelligence dict or the artifact's `intelligence`)."""
    res = i["result"]
    model = [res["model_probs"][k] for k in ("home", "draw", "away")]
    mk = res.get("market_probs_devig")
    market = [mk[k] for k in ("home", "draw", "away")] if mk else None
    dq = i.get("data_quality") or {}
    return {"model_top_probability": top(model), "entropy": entropy(model), "margin": margin(model), "disagreement_with_market": disagreement(model, market),
            "has_exact_odds": market is not None, "low_history": bool(dq.get("low_confidence")), "model_probs": model}  # fmt: skip
