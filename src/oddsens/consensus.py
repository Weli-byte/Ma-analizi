"""As-of bookmaker consensus from stored exact-timestamp 1X2 quotes (ADR 0087).

Input: `artifacts/odds/<fixture_id>/quotes.jsonl` (The Odds API via the cloud collector, `timestamp_quality = exact`). At a cutoff only quotes whose provider timestamp AND our fetch time are at or before
the cutoff are visible; per bookmaker the newest quote of each of H/D/A is used and all three must exist. Each bookmaker's triple is de-vigged on its own, then the probabilities are averaged with
equal weights (no learned bookmaker weights: far too little data). Closing odds are never used as a feature: a cutoff is the time of a forecast, not of the final whistle.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from .devig import remove_vig

SELECTIONS = ("H", "D", "A")
MAX_STALENESS = timedelta(hours=24)
MIN_BOOKS = 3
TRIPLE_SPREAD = timedelta(minutes=10)   # the three prices of one bookmaker must come from the same moment


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


@dataclass(frozen=True)
class Quote:
    bookmaker: str
    selection: str
    odds: float
    provider_ts: datetime
    observed_at: datetime


def read_quotes(fixture_dir: Path) -> list[Quote]:
    """Valid exact 1X2 quotes of one fixture; anything else is ignored (approximate, other markets, odds <= 1)."""
    path = Path(fixture_dir) / "quotes.jsonl"
    out = []
    for line in path.read_text(encoding="utf-8").splitlines() if path.exists() else []:
        if not line.strip():
            continue
        d = json.loads(line)
        if d.get("market") != "1X2" or d.get("timestamp_quality") != "exact" or d.get("selection") not in SELECTIONS or not d.get("provider_timestamp"):
            continue
        if not isinstance(d.get("decimal_odds"), (int, float)) or d["decimal_odds"] <= 1.0:
            continue
        out.append(Quote((d.get("provenance") or {}).get("bookmaker_key") or d["bookmaker"], d["selection"], float(d["decimal_odds"]), _utc(d["provider_timestamp"]), _utc(d["observed_at"])))
    return out


def triples_at(quotes: list[Quote], cutoff: datetime) -> dict[str, dict]:
    """{bookmaker: {"odds": (h, d, a), "provider_ts": newest, "staleness": cutoff - oldest}} for the bookmakers with a complete, consistent, fresh triple visible at the cutoff."""
    visible = [q for q in quotes if q.provider_ts <= cutoff and q.observed_at <= cutoff]
    out = {}
    for book in sorted({q.bookmaker for q in visible}):
        newest = {}
        for q in (q for q in visible if q.bookmaker == book):
            if q.selection not in newest or q.provider_ts > newest[q.selection].provider_ts:
                newest[q.selection] = q
        if set(newest) != set(SELECTIONS):
            continue
        stamps = [newest[s].provider_ts for s in SELECTIONS]
        if max(stamps) - min(stamps) > TRIPLE_SPREAD or cutoff - min(stamps) > MAX_STALENESS:
            continue
        out[book] = {"odds": tuple(newest[s].odds for s in SELECTIONS), "provider_ts": max(stamps), "staleness": cutoff - min(stamps)}
    return out


def consensus(quotes: list[Quote], cutoff: datetime, method: str = "normalization", min_books: int = MIN_BOOKS) -> dict | None:
    """Equal-weight mean of the per-bookmaker de-vigged probabilities, or None with fewer than `min_books` usable bookmakers."""
    t = triples_at(quotes, cutoff)
    if len(t) < min_books:
        return None
    probs = np.array([remove_vig(v["odds"], method) for v in t.values()])
    return {
        "p": probs.mean(axis=0), "n_books": len(t), "dispersion": float(probs.std(axis=0).mean()), "staleness_min": float(np.mean([v["staleness"].total_seconds() / 60 for v in t.values()])),
        "sharp": None if "pinnacle" not in t else remove_vig(t["pinnacle"]["odds"], method),
    }  # fmt: skip
