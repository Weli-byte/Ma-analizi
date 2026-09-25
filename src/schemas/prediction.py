import hashlib
import math
from typing import Self

from pydantic import Field, computed_field, model_validator

from src.versioning import (
    DATA_VERSION_RE,
    FEATURE_VERSION_RE,
    MODEL_ID_RE,
    MODEL_VERSION_RE,
    check,
)

from .common import ImmutableModel, PredictionStatus, UtcDatetime

PROB_TOL = 1e-6


class PredictionRecord(ImmutableModel):
    """Immutable 1X2 prediction. A change is a NEW record, never an edit."""

    fixture_id: str = Field(min_length=1)
    model_id: str
    model_version: str
    feature_version: str
    data_version: str
    generated_at: UtcDatetime
    information_cutoff: UtcDatetime
    p_home: float = Field(ge=0.0, le=1.0)
    p_draw: float = Field(ge=0.0, le=1.0)
    p_away: float = Field(ge=0.0, le=1.0)
    status: PredictionStatus = PredictionStatus.CREATED

    @model_validator(mode="after")
    def _check(self) -> Self:
        check(MODEL_ID_RE, self.model_id, "model_id")
        check(MODEL_VERSION_RE, self.model_version, "model_version")
        check(FEATURE_VERSION_RE, self.feature_version, "feature_version")
        check(DATA_VERSION_RE, self.data_version, "data_version")
        total = self.p_home + self.p_draw + self.p_away
        if not math.isclose(total, 1.0, abs_tol=PROB_TOL):
            raise ValueError(f"probabilities must sum to 1, got {total}")
        if self.generated_at < self.information_cutoff:
            raise ValueError("generated_at must not precede information_cutoff")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def prediction_id(self) -> str:
        """Deterministic id: same inputs -> same id (replay/idempotency)."""
        key = "|".join(
            [
                self.fixture_id,
                self.model_id,
                self.model_version,
                self.feature_version,
                self.data_version,
                self.information_cutoff.isoformat(),
            ]
        )
        return hashlib.sha256(key.encode()).hexdigest()[:16]
