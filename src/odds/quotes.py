"""S15 odds quotes (ADR 0030). One quote = one bookmaker price for one selection, observed at an
exact moment by OUR collector.

`timestamp_quality` (extends ADR 0007, decision for the owner to confirm):
- `exact`   : the quote was read from a live bookmaker line by this collector, and `observed_at` is
              our own fetch time (known to the second). The bookmaker's own quote time and the
              feed latency are NOT known (`source_latency_s` is None and is reported as such), so an
              EV on such a quote assumes the line was live when read.
- `unknown` : no usable time (historical football-data odds, or an "open" price whose open time the
              source does not give). Never eligible for edge/EV/CLV.
Only `exact` quotes may drive edge/EV/CLV/paper bets (CLAUDE.md non-negotiable). Closing odds from
football-data.co.uk stay a REFERENCE_MARKET_BASELINE.
"""

import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from src.schemas.common import ImmutableModel, UtcDatetime
from src.versioning import canonical_json

Selection = Literal["H", "D", "A"]
TimestampQuality = Literal["exact", "approximate", "unknown"]


class OddsQuote(ImmutableModel):
    source: str = Field(min_length=1)  # e.g. "espn"
    bookmaker: str = Field(min_length=1)  # e.g. "DraftKings"
    fixture_id: str = Field(min_length=1)  # repo fixture id this quote is attached to
    market: Literal["1X2"] = "1X2"
    selection: Selection
    decimal_odds: float = Field(gt=1.0)
    snapshot_type: Literal["opening", "pre_match", "closing", "live"]
    observed_at: UtcDatetime
    timestamp_quality: TimestampQuality
    source_latency_s: float | None = None  # None = unknown; never guessed
    raw_price: str  # the price exactly as the source gave it, e.g. "+390"
    provenance: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self):
        if self.timestamp_quality == "exact" and self.snapshot_type == "opening":
            raise ValueError("an opening price has no known capture time; it cannot be 'exact'")
        return self

    @property
    def quote_id(self) -> str:
        payload = {
            "source": self.source, "bookmaker": self.bookmaker, "fixture": self.fixture_id,
            "selection": self.selection, "odds": self.decimal_odds, "type": self.snapshot_type,
            "observed_at": self.observed_at.isoformat(),
        }  # fmt: skip
        return hashlib.sha256(canonical_json(payload).encode()).hexdigest()[:16]

    def to_json_line(self) -> str:
        return json.dumps({**json.loads(self.model_dump_json()), "quote_id": self.quote_id}, sort_keys=True)


def american_to_decimal(price: str | float | int) -> float:
    """'+390' -> 4.9, '-260' -> 1.3846.... Raises on anything else (never guesses)."""
    try:
        v = float(str(price).replace("+", ""))
    except ValueError as e:
        raise ValueError(f"unparseable American price {price!r}") from e
    if v == 0 or -100 < v < 100:
        raise ValueError(f"invalid American price {price!r}")
    return 1.0 + v / 100.0 if v > 0 else 1.0 + 100.0 / abs(v)
