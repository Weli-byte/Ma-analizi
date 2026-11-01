"""The uncertainty block served with every forecast (ADR 0095): API field `uncertainty`, the "Model güveni" card, and the abstain rule.

Reads `docs/phase3/s16_selective.json` (thresholds from history and the live evidence per bucket, written by `python -m src.uncertainty.study`). Fields: `calibrated_probability` (the model's top
1X2 probability; the identity is the champion calibrator, so no further calibration is applied), entropy, margin, disagreement with the market, `coverage_bucket` ("40" = among the 40 % most
confident of the history, "60", "80", or "abstain" for the least confident 20 %), `abstain_reason`, `uncertainty_reason` (data flags) and the bucket's live evidence — INSUFFICIENT_EVIDENCE
below 50 settled matches, in which case no confidence claim is made. When a forecast abstains, no value suggestion is shown for it (the abstain decision wins over an expected-value signal;
nothing is ever placed as a bet).
"""

import json
from pathlib import Path

from . import selective
from .measures import from_intel

DISAGREEMENT_FLAG = 0.15  # half-L1 distance between model and market 1X2 above which the reason list says they disagree (a flag, not an abstain rule)
NOTE = "model confidence from its error history; not a guarantee of a result and not betting advice"


def load(root) -> dict | None:
    for p in (Path(root) / "docs" / "phase3" / "s16_selective.json", Path(__file__).resolve().parents[2] / "docs" / "phase3" / "s16_selective.json"):
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    return None


def block(intel: dict, sel: dict | None) -> dict | None:
    if sel is None:
        return None
    u = from_intel(intel)
    b = selective.bucket(u["model_top_probability"], sel["thresholds"])
    reasons = []
    if not u["has_exact_odds"]:
        reasons.append("NO_EXACT_ODDS (model only)")
    if u["low_history"]:
        reasons.append("LOW_HISTORY (a club has little or no match history)")
    if u["disagreement_with_market"] is not None and u["disagreement_with_market"] > DISAGREEMENT_FLAG:
        reasons.append("MODEL_MARKET_DISAGREEMENT")
    ev = (sel.get("live_tiers") or {}).get(b) or {"evidence": "INSUFFICIENT_EVIDENCE", "n": 0}
    return {"calibrated_probability": round(u["model_top_probability"], 4), "calibration": "identity champion (ADR 0089)", "entropy": round(u["entropy"], 4), "margin": round(u["margin"], 4),
            "disagreement_with_market": None if u["disagreement_with_market"] is None else round(u["disagreement_with_market"], 4), "coverage_bucket": b, "abstain": b == "abstain",
            "abstain_reason": "TOP_PROBABILITY_BELOW_THE_80_PERCENT_COVERAGE_THRESHOLD" if b == "abstain" else None, "uncertainty_reason": reasons, "bucket_evidence": ev, "note": NOTE}  # fmt: skip
