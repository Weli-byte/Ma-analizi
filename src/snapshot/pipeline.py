"""Run ONE stage of ONE fixture end to end (ADR 0027). Pure orchestration over injected parts, so
the same code serves the real CLI (`run.py`) and deterministic system tests that drive a real
historical fixture through a chosen `now`. Nothing here fabricates data: unknown stays UNKNOWN,
failed/abstaining models are reported, a locked stage is returned as stored, never recomputed.
"""

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime

from src.config import FeaturesConfig
from src.features.compute import DEFAULT_CONFIG
from src.features.history import MatchHistory
from src.schemas import PredictionLedger, PredictionRecord

from .delta import probability_delta
from .engine import build_stage_snapshot, predict_stage
from .stages import SnapshotStage, StageState, cutoff_for_stage, stage_state
from .store import StageStore, predictions_text

# (snapshot row) -> (predictions, call-record JSON lines, raw-response JSON lines)
LLMStep = Callable[[object, datetime], tuple[list[PredictionRecord], list[str], list[str]]]


@dataclass(frozen=True)
class StageResult:
    status: str  # COMPLETED | ALREADY_LOCKED | NOT_YET | MISSED
    fixture_id: str
    stage: str
    snapshot_hash: str | None = None
    lateness_minutes: float | None = None
    predictions: list[PredictionRecord] = field(default_factory=list)
    model_statuses: list[dict] = field(default_factory=list)
    deltas: list[dict] = field(default_factory=list)


def run_stage(
    store: StageStore,
    fixture,
    history: MatchHistory,
    stage: SnapshotStage,
    now: datetime,
    data_version: str,
    feature_version: str,
    models: list,
    features_cfg: FeaturesConfig = DEFAULT_CONFIG,
    llm_step: LLMStep | None = None,
    injuries: dict | None = None,
) -> StageResult:
    fid = fixture.fixture_id
    if store.is_locked(fid, stage):  # idempotent: the stored forecast is the forecast
        stored = store.read(fid, stage)
        return StageResult(
            "ALREADY_LOCKED", fid, stage.value, stored.lock["snapshot_hash"],
            stored.lock.get("lateness_minutes"), stored.predictions,
        )  # fmt: skip
    state = stage_state(fixture.kickoff_utc, stage, now)
    if state != StageState.DUE:
        return StageResult(state.name, fid, stage.value)

    snapshot = build_stage_snapshot(
        fixture, history, stage, data_version, feature_version, features_cfg, injuries
    )
    records, statuses = predict_stage(snapshot, models, now)
    llm_calls: list[str] = []
    llm_responses: list[str] = []
    if llm_step is not None:
        llm_records, llm_calls, llm_responses = llm_step(snapshot.to_eval_row(), snapshot.information_cutoff)
        records += llm_records

    ledger = PredictionLedger(store.base / "ledger.jsonl")  # a changed forecast conflicts here
    for r in records:
        ledger.append(r)

    prev_stage = store.latest_locked_before(fid, stage)
    deltas: list[dict] = []
    if prev_stage is not None:
        prev = {p.model_id: p for p in store.read(fid, prev_stage).predictions}
        for r in records:
            if r.model_id in prev:
                deltas.append(
                    asdict(probability_delta(prev[r.model_id], r, stage.value, prev_stage.value))
                )  # fmt: skip

    lateness = (now - cutoff_for_stage(fixture.kickoff_utc, stage)).total_seconds() / 60
    lock = {
        "fixture_id": fid,
        "stage": stage.value,
        "snapshot_hash": snapshot.snapshot_hash,
        "generated_at": now.isoformat(),
        "lateness_minutes": round(lateness, 2),
        "prediction_ids": {r.model_id: r.prediction_id for r in records},
        "previous_stage": prev_stage.value if prev_stage else None,
        "availability": snapshot.availability,
    }
    files = {
        "snapshot.json": json.dumps(snapshot.to_dict(), indent=2, sort_keys=True),
        "predictions.jsonl": predictions_text(records),
        "model_status.json": json.dumps([asdict(s) for s in statuses], indent=2),
        "deltas.json": json.dumps(deltas, indent=2),
        "llm_calls.jsonl": "\n".join(llm_calls) + ("\n" if llm_calls else ""),
        "llm_responses.jsonl": "\n".join(llm_responses) + ("\n" if llm_responses else ""),
    }
    store.write_stage(fid, stage, files, lock)
    return StageResult(
        "COMPLETED", fid, stage.value, snapshot.snapshot_hash, round(lateness, 2), records,
        [asdict(s) for s in statuses], deltas,
    )  # fmt: skip
