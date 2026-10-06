"""S13: probability delta between two consecutive-stage predictions for the same fixture+model."""

from dataclasses import dataclass

from src.schemas import PredictionRecord


@dataclass(frozen=True)
class ProbabilityDelta:
    fixture_id: str
    model_id: str
    from_stage: str | None  # None iff `previous` was None (the first stage has no delta source)
    to_stage: str
    delta_home: float
    delta_draw: float
    delta_away: float
    max_abs_delta: float


def probability_delta(
    previous: PredictionRecord | None, current: PredictionRecord, to_stage: str, from_stage: str | None = None
) -> ProbabilityDelta:
    if previous is None:
        return ProbabilityDelta(current.fixture_id, current.model_id, None, to_stage, 0.0, 0.0, 0.0, 0.0)
    if previous.fixture_id != current.fixture_id or previous.model_id != current.model_id:
        raise ValueError("previous and current must be the same fixture+model, different stages only")
    dh = current.p_home - previous.p_home
    dd = current.p_draw - previous.p_draw
    da = current.p_away - previous.p_away
    return ProbabilityDelta(
        current.fixture_id, current.model_id, from_stage, to_stage,
        round(dh, 6), round(dd, 6), round(da, 6), round(max(abs(dh), abs(dd), abs(da)), 6),
    )  # fmt: skip
