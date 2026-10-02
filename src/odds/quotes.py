"""S15 odds quotes (ADR 0030, amended). One quote = one bookmaker price for one selection.

`timestamp_quality` (strict reading of ADR 0007):
- `exact`       : the SOURCE supplies its own capture/update time for the quote (`provider_timestamp`)
                 and we record when we received it, so the feed latency is MEASURED
                 (`source_latency_s = received - provider_timestamp`, never guessed, never negative).
- `approximate` : only OUR observation time is known (`observed_at`); the source gives no quote time,
                 so the quote may have been stale for an unknown time. ESPN is in this class.
- `unknown`     : no usable time (historical football-data odds; opening prices without an open time).
Only `exact` quotes may drive edge/EV/CLV/paper bets (CLAUDE.md non-negotiable). `approximate` quotes
are kept as a time series and may be shown as a market REFERENCE, never as a signal. Closing odds from
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
MAX_CLOCK_SKEW_S = 5.0  # a provider time this far ahead of our clock is not trusted as exact


class OddsQuote(ImmutableModel):
    source: str = Field(min_length=1)  # e.g. "espn", "the-odds-api"
    bookmaker: str = Field(min_length=1)  # e.g. "DraftKings"
    fixture_id: str = Field(min_length=1)  # id this quote is attached to
    market: Literal["1X2"] = "1X2"
    selection: Selection
    decimal_odds: float = Field(gt=1.0)
    snapshot_type: Literal["opening", "pre_match", "closing", "live"]
    observed_at: UtcDatetime  # when WE received/read it
    provider_timestamp: UtcDatetime | None = None  # the SOURCE's own time for this quote, if it gives one
    timestamp_quality: TimestampQuality
    source_latency_s: float | None = None  # observed_at - provider_timestamp; None = unknown
    raw_price: str  # the price exactly as the source gave it, e.g. "+390"
    provenance: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self):
        if self.timestamp_quality == "exact":
            if self.provider_timestamp is None or self.source_latency_s is None:
                raise ValueError("'exact' needs a provider timestamp and a measured source latency")
            measured = (self.observed_at - self.provider_timestamp).total_seconds()
            if measured < -MAX_CLOCK_SKEW_S or abs(measured - self.source_latency_s) > 1e-6:
                raise ValueError(
                    "source_latency_s must equal observed_at - provider_timestamp (and not be negative)"
                )
            if self.snapshot_type == "opening":
                raise ValueError("an opening price cannot be declared exact")
        elif self.provider_timestamp is not None or self.source_latency_s is not None:
            raise ValueError("a provider timestamp/latency is only recorded on 'exact' quotes")
        return self

    @property
    def quote_id(self) -> str:
        payload = {
            "source": self.source, "bookmaker": self.bookmaker, "fixture": self.fixture_id,
            "selection": self.selection, "odds": self.decimal_odds, "type": self.snapshot_type,
            "observed_at": self.observed_at.isoformat(),
            "provider_timestamp": self.provider_timestamp.isoformat() if self.provider_timestamp else None,
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
