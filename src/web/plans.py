"""Free / Pro plans (ADR 0062). Infrastructure only: there is NO payment yet, so nothing is sold.

- The matrix mirrors `docs/product.md`: a feature needs a minimum plan.
- Gates are OFF unless `FORECAST_PLANS_ENFORCED` is truthy; while off, every signed-in user sees everything (as before).
- A user is Pro when their stored plan is `pro` (set by a future payment hook or by the owner) or when their VERIFIED e-mail is
  listed in `FORECAST_PRO_EMAILS` (comma separated). Only Google sign-in puts an e-mail on the session, and it is checked
  against `email_verified` there, so a guest or phone user can never match that list."""

import os

FREE = "free"
PRO = "pro"
PLANS = (FREE, PRO)
_RANK = {FREE: 0, PRO: 1}

# feature -> minimum plan (docs/product.md; only features that exist in the web UI)
FEATURES: dict[str, str] = {
    "paper_value": PRO,  # /tavsiyeler
    "forecast_history": PRO,  # /basari-kaydi
}


def enforced() -> bool:
    return os.environ.get("FORECAST_PLANS_ENFORCED", "").strip().lower() in ("1", "true", "yes", "on")


def _pro_emails() -> set[str]:
    return {e.strip().lower() for e in os.environ.get("FORECAST_PRO_EMAILS", "").split(",") if e.strip()}


def effective_plan(email: str | None, stored: str | None) -> str:
    if email and email.strip().lower() in _pro_emails():
        return PRO
    return stored if stored in PLANS else FREE


def allows(plan: str, feature: str) -> bool:
    if feature not in FEATURES:
        raise KeyError(f"unknown feature: {feature}")
    if not enforced():
        return True
    return _RANK.get(plan, 0) >= _RANK[FEATURES[feature]]
