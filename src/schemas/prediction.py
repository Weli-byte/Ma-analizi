import hashlib
import json
import math
from typing import Self

from pydantic import Field, computed_field, model_validator

from src.versioning import (
    DATA_VERSION_RE,
    FEATURE_VERSION_RE,
    MODEL_ID_RE,
    MODEL_VERSION_RE,
    canonical_json,
    check,
)

from .common import ImmutableModel, PredictionStatus, UtcDatetime

PROB_TOL = 1e-6


class PredictionRecord(ImmutableModel):
    """Immutable pre-match 1X2 prediction. A change is a NEW record, never an edit.

    Identity (ADR 0002-style):
      logical_id    = hash(fixture, model, model_version, feature_version, data_version, cutoff)
                      -> "which prediction is this?"
      content_hash  = hash(logical fields + kickoff + probabilities)
                      -> "what exactly does it say?"
      prediction_id = content_hash[:16]
    Same logical_id with a different content_hash is a conflict (PredictionLedger rejects it).
    """

    fixture_id: str = Field(min_length=1)
    model_id: str
    model_version: str
    feature_version: str
    data_version: str
    kickoff_utc: UtcDatetime
    information_cutoff: UtcDatetime
    generated_at: UtcDatetime  # prediction_created_at
    p_home: float = Field(ge=0.0, le=1.0)
    p_draw: float = Field(ge=0.0, le=1.0)
    p_away: float = Field(ge=0.0, le=1.0)
    status: PredictionStatus = PredictionStatus.DRAFT

    @model_validator(mode="after")
    def _check(self) -> Self:
        check(MODEL_ID_RE, self.model_id, "model_id")
        check(MODEL_VERSION_RE, self.model_version, "model_version")
        check(FEATURE_VERSION_RE, self.feature_version, "feature_version")
        check(DATA_VERSION_RE, self.data_version, "data_version")
        total = self.p_home + self.p_draw + self.p_away
        if not math.isclose(total, 1.0, abs_tol=PROB_TOL):
            raise ValueError(f"probabilities must sum to 1, got {total}")
        if self.information_cutoff > self.kickoff_utc:
            raise ValueError("information_cutoff must not be after kickoff")
        if self.generated_at < self.information_cutoff:
            raise ValueError("generated_at must not precede information_cutoff")
        if self.generated_at > self.kickoff_utc:
            raise ValueError("pre-match prediction cannot be generated after kickoff")
        return self

    def _logical_fields(self) -> dict:
        return {
            "fixture_id": self.fixture_id,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "feature_version": self.feature_version,
            "data_version": self.data_version,
            "information_cutoff": self.information_cutoff.isoformat(),
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
            "kickoff_utc": self.kickoff_utc.isoformat(),
            "generated_at": self.generated_at.isoformat(),
            "p": [repr(self.p_home), repr(self.p_draw), repr(self.p_away)],
        }
        return hashlib.sha256(canonical_json(payload).encode()).hexdigest()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def prediction_id(self) -> str:
        return self.content_hash[:16]

    @classmethod
    def from_json(cls, text: str) -> Self:
        """Parse a serialized record and VERIFY its stored identity (detects edited files)."""
        data = json.loads(text)
        stored = {k: data.pop(k, None) for k in ("logical_id", "content_hash", "prediction_id")}
        rec = cls.model_validate(data)
        for key, value in stored.items():
            if value is not None and value != getattr(rec, key):
                raise ValueError(f"stored {key} does not match record content (tampered or corrupted)")
        return rec
