"""As-of (time-safety) contract of the league-model programme (Sprint 1, plan task 4 and page 6).

Two separate time checks for every piece of source information:
  1. EVENT time   `source_event_time_utc   <= prediction_as_of_utc` (when it happened)
  2. KNOWN time   `source_available_at_utc <= prediction_as_of_utc` (when our system could first see it)
A result that is only INFERRED to be available (openfootball has no result timestamp: kick-off plus a fixed
lag, ADR 0006) can never prove historical as-of correctness, so its rows are labelled BACKTEST_LIMITED.
Updated scores, odds and lineups are never replayed backwards. Stage cutoffs reuse the snapshot stages of
S13 (`src/snapshot/stages.py`)."""

import bisect
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.snapshot.stages import STAGE_ORDER, SnapshotStage, cutoff_for_stage

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class Availability(StrEnum):
    ALLOWED = "ALLOWED"  # may feed a prediction at this stage (subject to the two time checks)
    CONDITIONAL = "CONDITIONAL"  # only under the stated rule
    FORBIDDEN = "FORBIDDEN"  # never at this stage


# (field group, stage) -> (availability, rule). One row per field group, in stage order t-24h, t-90m, t-30m,
# kickoff.
_A, _C, _F = Availability.ALLOWED, Availability.CONDITIONAL, Availability.FORBIDDEN
STAGE_FIELD_MATRIX: dict[str, tuple[tuple[Availability, ...], str]] = {
    "historical_results": ((_A, _A, _A, _A), "only finished matches with source_available_at <= as_of"),
    "schedule_and_venue": (
        (_A, _A, _A, _A),
        "kick-off time as published at as_of; a later change is not replayed",
    ),
    "team_form_windows": (
        (_A, _A, _A, _A),
        "5/10 matches, 30/90/365 days from earlier matches only; same-day and postponed matches ordered",
    ),
    "elo_and_strength_ratings": (
        (_A, _A, _A, _A),
        "computed from matches available at as_of; per-league and per-tournament scales kept apart",
    ),
    "injuries_and_suspensions": (
        (_C, _C, _C, _C),
        "only records observed at or before as_of; status labels, never back-dated",
    ),
    "lineups_confirmed": (
        (_F, _C, _A, _A),
        "an unannounced starting eleven can never be a t-24h input; t-90m only if already published",
    ),
    "referee_assignment": ((_C, _C, _A, _A), "only if announced and observed at or before as_of"),
    "odds_exact_observed": (
        (_C, _C, _C, _C),
        "only quotes with timestamp_quality exact and observed_at <= as_of",
    ),
    "odds_closing": (
        (_F, _F, _F, _C),
        "never a feature; at kick-off only as a reference baseline (REFERENCE_MARKET_BASELINE)",
    ),
    "live_score_state": ((_F, _F, _F, _F), "pre-match stages never see an in-play state"),
    "match_statistics_current_match": (
        (_F, _F, _F, _F),
        "shots, corners, cards, xG of the fixture being predicted are post-match facts",
    ),
}


def stage_availability(field_group: str, stage: SnapshotStage) -> Availability:
    return STAGE_FIELD_MATRIX[field_group][0][STAGE_ORDER.index(stage)]


def stage_as_of(kickoff_utc: datetime, stage: SnapshotStage) -> datetime:
    return cutoff_for_stage(kickoff_utc, stage)


AvailableAtSource = Literal["observed", "inferred"]


class SourceObservation(BaseModel):
    """One piece of source information used for one prediction, with both clocks. Immutable."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    field_group: str
    prediction_as_of_utc: datetime
    fixture_kickoff_utc: datetime
    source_event_time_utc: datetime
    source_available_at_utc: datetime
    available_at_source: AvailableAtSource
    ingest_time_utc: datetime
    source_version: str = Field(min_length=1)
    content_hash: str
    raw_snapshot_pointer: str = Field(min_length=1)
    quality_flags: tuple[str, ...] = ()

    @field_validator(
        "prediction_as_of_utc",
        "fixture_kickoff_utc",
        "source_event_time_utc",
        "source_available_at_utc",
        "ingest_time_utc",
    )
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() != timedelta(0):
            raise ValueError("timestamps must be timezone-aware UTC")
        return v

    @model_validator(mode="after")
    def _shape(self) -> "SourceObservation":
        if not SHA256_RE.match(self.content_hash):
            raise ValueError("content_hash must be a lowercase sha256 hex digest")
        if self.field_group not in STAGE_FIELD_MATRIX:
            raise ValueError(f"unknown field group: {self.field_group}")
        if self.prediction_as_of_utc > self.fixture_kickoff_utc:
            raise ValueError("prediction_as_of_utc must not be after the fixture kick-off")
        if self.source_available_at_utc < self.source_event_time_utc:
            raise ValueError("information cannot be available before the event it describes")
        return self

    @property
    def backtest_limited(self) -> bool:
        """True when historical as-of correctness cannot be proven (the availability time is inferred)."""
        return self.available_at_source == "inferred"


@dataclass(frozen=True)
class Violation:
    code: str
    detail: str


def check_observation(obs: SourceObservation, stage: SnapshotStage | None = None) -> list[Violation]:
    """Both time checks plus the stage matrix. An empty list means the observation may feed the prediction."""
    out = []
    if obs.source_event_time_utc > obs.prediction_as_of_utc:
        out.append(Violation("EVENT_AFTER_AS_OF", f"event {obs.source_event_time_utc.isoformat()} > as_of"))
    if obs.source_available_at_utc > obs.prediction_as_of_utc:
        out.append(
            Violation("AVAILABLE_AFTER_AS_OF", f"available {obs.source_available_at_utc.isoformat()} > as_of")
        )
    if stage is not None:
        availability = stage_availability(obs.field_group, stage)
        if availability is Availability.FORBIDDEN:
            out.append(Violation("STAGE_FORBIDDEN", f"{obs.field_group} is forbidden at {stage.value}"))
    return out


def quality_label(obs: SourceObservation) -> str:
    return "BACKTEST_LIMITED" if obs.backtest_limited else "AS_OF_VERIFIED"


@dataclass(frozen=True)
class HistoryMatch:
    key: str
    kickoff_utc: datetime
    available_at_utc: datetime  # when the result could first be known (inferred or observed)


def inferred_available_at(kickoff_utc: datetime, lag_hours: int) -> datetime:
    return kickoff_utc + timedelta(hours=lag_hours)


def eligible_history(
    matches: list[HistoryMatch], as_of: datetime, own_key: str | None = None
) -> list[HistoryMatch]:
    """Matches whose result may feed a prediction made at `as_of`: known time <= as_of, and never the
    fixture itself."""
    return [m for m in matches if m.available_at_utc <= as_of and m.key != own_key]


@dataclass(frozen=True)
class TemporalAudit:
    fixtures: int
    checks: int  # (fixture, stage) pairs audited with the indexed implementation (100% of them)
    violations: int  # a history element whose result became available after the cutoff
    own_result_leaks: int  # the fixture's own result inside its own history
    future_kickoff_in_history: int  # a history element that kicked off at or after the cutoff
    data_anomalies: int  # matches whose availability time is BEFORE their own kick-off (impossible)
    brute_checks: int  # (fixture, stage) pairs re-checked with the plain list scan
    disagreements: int  # the plain scan and the indexed implementation disagreed (must be 0)
    backtest_limited: int  # fixtures whose history relies on inferred availability times


def temporal_audit(
    matches: list[HistoryMatch],
    stages: tuple[SnapshotStage, ...] = STAGE_ORDER,
    brute_every: int = 1,
    inferred: bool = True,
) -> TemporalAudit:
    """Audit EVERY (fixture, stage) pair with a sorted index: the history for the stage cutoff must hold
    only results known by then,
    never the fixture's own result, never a match kicked off at or after the cutoff. A second, plain list-
    scan implementation
    (`eligible_history`) re-checks every `brute_every`-th fixture and must agree. All violation counters
    must be 0."""
    ordered = sorted(matches, key=lambda m: (m.available_at_utc, m.key))
    avail = [m.available_at_utc for m in ordered]
    position = {m.key: i for i, m in enumerate(ordered)}
    prefix_max_kickoff: list[datetime] = []
    running = None
    for m in ordered:
        running = m.kickoff_utc if running is None or m.kickoff_utc > running else running
        prefix_max_kickoff.append(running)
    anomalies = sum(1 for m in matches if m.available_at_utc < m.kickoff_utc)
    checks = violations = own = future = brute = disagreements = 0
    for n, fx in enumerate(matches):
        for stage in stages:
            as_of = stage_as_of(fx.kickoff_utc, stage)
            idx = bisect.bisect_right(avail, as_of)  # ordered[:idx] is the history known at as_of
            checks += 1
            if idx and avail[idx - 1] > as_of:
                violations += 1
            if position[fx.key] < idx:
                own += 1
            if idx and prefix_max_kickoff[idx - 1] >= as_of:
                future += 1
            if brute_every and n % brute_every == 0:
                brute += 1
                scan = {m.key for m in eligible_history(matches, as_of, own_key=fx.key)}
                indexed = {m.key for m in ordered[:idx] if m.key != fx.key}
                if scan != indexed:
                    disagreements += 1
    return TemporalAudit(
        len(matches), checks, violations, own, future, anomalies, brute, disagreements,
        len(matches) if inferred else 0,
    )  # fmt: skip


def row_hash(row: dict) -> str:
    """Content hash of a training row (stable JSON of its values)."""
    return hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()


class TrainingRowLineage(BaseModel):
    """Traceability of one training row: as_of, the latest source availability it used, and its content
    hash."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    row_id: str
    prediction_as_of_utc: datetime
    max_source_available_at_utc: datetime
    row_hash: str
    quality_label: Literal["AS_OF_VERIFIED", "BACKTEST_LIMITED"]

    @model_validator(mode="after")
    def _check(self) -> "TrainingRowLineage":
        if self.max_source_available_at_utc > self.prediction_as_of_utc:
            raise ValueError("leakage: a source used by the row became available after as_of")
        if not SHA256_RE.match(self.row_hash):
            raise ValueError("row_hash must be a lowercase sha256 hex digest")
        return self


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)
