"""S14: immutable LIVE (in-play) 1X2 forecast. A DIFFERENT record type from `PredictionRecord` on
purpose (ADR 0029): a pre-match prediction must be generated before kickoff and may only use
information up to its cutoff; a live forecast is generated after kickoff from OBSERVED in-match
state. Keeping them apart makes accidental mixing impossible: a live record can never enter the
pre-match ledger/benchmark, and a pre-match record can never carry in-match state.
"""

import hashlib
import json
import math
from typing import Literal, Self

from pydantic import Field, computed_field, model_validator

from src.versioning import canonical_json

from .common import ImmutableModel, UtcDatetime

PROB_TOL = 1e-6


class LivePredictionRecord(ImmutableModel):
    mode: Literal["LIVE"] = "LIVE"
    fixture_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    data_version: str = Field(min_length=1)
    feature_version: str = Field(min_length=1)
    kickoff_utc: UtcDatetime
    observed_at: UtcDatetime  # when the in-match state was observed
    generated_at: UtcDatetime  # when this forecast was produced (>= observed_at)
    state_hash: str = Field(min_length=8)  # content hash of the in-match state used
    status: str  # NOT_STARTED | IN_PLAY | HALF_TIME | FINISHED | UNKNOWN (as observed)
    match_minute: int | None = Field(default=None, ge=0, le=150)
    minute_source: Literal["reported", "last_event", "inferred_from_clock"] | None = None
    score_home: int = Field(ge=0)
    score_away: int = Field(ge=0)
    # the exact pre-match goal rates the in-play model started from (provenance, not a feature)
    prematch_rate_home: float = Field(gt=0)
    prematch_rate_away: float = Field(gt=0)
    p_home: float = Field(ge=0.0, le=1.0)
    p_draw: float = Field(ge=0.0, le=1.0)
    p_away: float = Field(ge=0.0, le=1.0)
    calibration_status: Literal["NOT_CALIBRATED", "CALIBRATED"] = "NOT_CALIBRATED"
    notes: str = ""

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not math.isclose(self.p_home + self.p_draw + self.p_away, 1.0, abs_tol=PROB_TOL):
            raise ValueError("probabilities must sum to 1")
        if self.generated_at < self.observed_at:
            raise ValueError("generated_at must not precede observed_at")
        if self.observed_at < self.kickoff_utc:
            raise ValueError("a LIVE forecast needs state observed at/after kickoff; use PredictionRecord")
        return self

    def _logical_fields(self) -> dict:
        return {
            "fixture_id": self.fixture_id,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "data_version": self.data_version,
            "feature_version": self.feature_version,
            "state_hash": self.state_hash,
        }

    @computed_field  # type: ignore[prop-decorator]
    @property
    def logical_id(self) -> str:
        return hashlib.sha256(canonical_json(self._logical_fields()).encode()).hexdigest()[:16]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def content_hash(self) -> str:
        payload = {
            **self._logical_fields(),
            "observed_at": self.observed_at.isoformat(),
            "generated_at": self.generated_at.isoformat(),
            "p": [repr(self.p_home), repr(self.p_draw), repr(self.p_away)],
            "minute": self.match_minute,
            "score": [self.score_home, self.score_away],
            "rates": [repr(self.prematch_rate_home), repr(self.prematch_rate_away)],
        }
        return hashlib.sha256(canonical_json(payload).encode()).hexdigest()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def prediction_id(self) -> str:
        return self.content_hash[:16]

    @classmethod
    def from_json(cls, text: str) -> Self:
        """Parse a stored record and VERIFY its identity (detects edited files)."""
        data = json.loads(text)
        stored = {k: data.pop(k, None) for k in ("logical_id", "content_hash", "prediction_id")}
        rec = cls.model_validate(data)
        for key, value in stored.items():
            if value is not None and value != getattr(rec, key):
                raise ValueError(f"stored {key} does not match record content (tampered or corrupted)")
        return rec
