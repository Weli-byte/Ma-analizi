"""Free / Pro plans (ADR 0062). Infrastructure only: there is NO payment yet, so nothing is sold.

- The matrix mirrors `docs/product.md`: a feature needs a minimum plan. Package B (ADR 0062 amendment): the "Marketler",
  "Hakem" and "Oyuncular" tabs of a match page are Pro; everything else, including tips and the track record, stays free.
- Gates are OFF unless `FORECAST_PLANS_ENFORCED` is truthy; while off, every signed-in user sees everything (as before).
- A user is Pro when their stored plan is `pro` (set by a future payment hook or by the owner), when their VERIFIED e-mail is
  listed in `FORECAST_PRO_EMAILS` (comma separated), or when it is the owner's contact address (`brand.CONTACT_EMAIL`): the owner
  uses Pro for free. Only Google sign-in puts an e-mail on the session, and it is checked
  against `email_verified` there, so a guest or phone user can never match that list."""

import os

from .brand import CONTACT_EMAIL

FREE = "free"
PRO = "pro"
PLANS = (FREE, PRO)
_RANK = {FREE: 0, PRO: 1}

# feature -> minimum plan (docs/product.md; only features that exist in the web UI)
FEATURES: dict[str, str] = {
    "goal_markets": PRO,  # match tab "Marketler": over/under, both teams to score, scores, corners, cards
    "match_people": PRO,  # match tabs "Hakem" (referee) and "Oyuncular" (top scorers)
}
# match-page tab key -> the feature that locks it
TAB_FEATURES: dict[str, str] = {"market": "goal_markets", "hakem": "match_people", "oyuncu": "match_people"}


def enforced() -> bool:
    return os.environ.get("FORECAST_PLANS_ENFORCED", "").strip().lower() in ("1", "true", "yes", "on")


def _pro_emails() -> set[str]:
    listed = {e.strip().lower() for e in os.environ.get("FORECAST_PRO_EMAILS", "").split(",") if e.strip()}
    return listed | {CONTACT_EMAIL.strip().lower()}


# tip markets everyone sees; every other market (over/under, both teams to score, corners, cards ...) is `goal_markets`
FREE_TIP_MARKETS = frozenset({"match_result", "double_chance"})


def filter_tips(tips: list[dict], plan: str) -> list[dict]:
    """The tips a plan may see: all while the gates are off, otherwise only the free markets."""
    if allows(plan, "goal_markets"):
        return tips
    return [t for t in tips if t["market"] in FREE_TIP_MARKETS]


def locked_tabs(plan: str) -> set[str]:
    """Match-page tab keys this plan cannot open (empty while the gates are off)."""
    return {tab for tab, feature in TAB_FEATURES.items() if not allows(plan, feature)}


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
