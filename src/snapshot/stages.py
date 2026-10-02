"""S13: the four canonical pre-match snapshot stages (ADR 0027). Each stage is a DISTINCT
`information_cutoff` for the SAME fixture. Every offset is >= 0 minutes BEFORE kickoff, so a
stage cutoff can never be after kickoff (structural, not a runtime check that could be bypassed).

The `kickoff` stage is the FINAL pre-kickoff look at T-5m, not T-0: a PredictionRecord needs
`information_cutoff <= generated_at <= kickoff`, so a cutoff exactly at kickoff could only be
generated at the very instant of kickoff. Each stage also has a tolerance window: a stage run later
than `cutoff + tolerance` is MISSED (recorded, never silently run late).
"""

from datetime import datetime, timedelta
from enum import StrEnum


class SnapshotStage(StrEnum):
    T_24H = "t-24h"
    T_90M = "t-90m"
    T_30M = "t-30m"
    KICKOFF = "kickoff"


class StageState(StrEnum):
    NOT_YET = "not_yet"  # cutoff is still in the future
    DUE = "due"  # cutoff <= now <= cutoff + tolerance and now < kickoff
    MISSED = "missed"  # window passed (or kickoff passed) without a run


# Minutes BEFORE kickoff. Ordered earliest-first -- `STAGE_ORDER` is the canonical sequence a
# fixture's snapshots are generated and compared in.
STAGE_OFFSET_MINUTES: dict[SnapshotStage, int] = {
    SnapshotStage.T_24H: 1440,
    SnapshotStage.T_90M: 90,
    SnapshotStage.T_30M: 30,
    SnapshotStage.KICKOFF: 5,
}
STAGE_TOLERANCE_MINUTES: dict[SnapshotStage, int] = {
    SnapshotStage.T_24H: 180,
    SnapshotStage.T_90M: 20,
    SnapshotStage.T_30M: 10,
    SnapshotStage.KICKOFF: 4,
}
STAGE_ORDER: tuple[SnapshotStage, ...] = (
    SnapshotStage.T_24H, SnapshotStage.T_90M, SnapshotStage.T_30M, SnapshotStage.KICKOFF,
)  # fmt: skip


def cutoff_for_stage(kickoff_utc: datetime, stage: SnapshotStage) -> datetime:
    return kickoff_utc - timedelta(minutes=STAGE_OFFSET_MINUTES[stage])


def previous_stage(stage: SnapshotStage) -> SnapshotStage | None:
    idx = STAGE_ORDER.index(stage)
    return STAGE_ORDER[idx - 1] if idx > 0 else None


def stage_state(kickoff_utc: datetime, stage: SnapshotStage, now: datetime) -> StageState:
    """Pre-match only: at/after kickoff every stage is MISSED (the post-kickoff guard)."""
    if now >= kickoff_utc:
        return StageState.MISSED
    cutoff = cutoff_for_stage(kickoff_utc, stage)
    if now < cutoff:
        return StageState.NOT_YET
    if now <= cutoff + timedelta(minutes=STAGE_TOLERANCE_MINUTES[stage]):
        return StageState.DUE
    return StageState.MISSED


def due_stage(kickoff_utc: datetime, now: datetime) -> SnapshotStage | None:
    """The stage whose window contains `now`, if any (windows never overlap)."""
    for stage in STAGE_ORDER:
        if stage_state(kickoff_utc, stage, now) == StageState.DUE:
            return stage
    return None
