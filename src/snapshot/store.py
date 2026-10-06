"""Stage store (ADR 0027): one directory per (fixture, stage) under artifacts/snapshots/.

A stage is written to a temp directory and atomically renamed into place together with a
`LOCK.json` (snapshot hash + prediction hashes). Once the directory exists the stage is LOCKED:
`write_stage` refuses to overwrite it, so repeated invocations can never change a stored forecast.
"""

import json
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from src.schemas import PredictionRecord
from src.schemas.lifecycle import dump_records

from .stages import SnapshotStage


class StageLocked(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredStage:
    snapshot: dict
    predictions: list[PredictionRecord]
    lock: dict


class StageStore:
    def __init__(self, root: Path):
        self.base = Path(root) / "artifacts" / "snapshots"

    def stage_dir(self, fixture_id: str, stage: SnapshotStage | str) -> Path:
        return self.base / fixture_id / str(getattr(stage, "value", stage))

    def is_locked(self, fixture_id: str, stage: SnapshotStage | str) -> bool:
        return (self.stage_dir(fixture_id, stage) / "LOCK.json").exists()

    def read(self, fixture_id: str, stage: SnapshotStage | str) -> StoredStage:
        d = self.stage_dir(fixture_id, stage)
        if not (d / "LOCK.json").exists():
            raise FileNotFoundError(f"stage {stage} of {fixture_id} is not stored")
        text = (d / "predictions.jsonl").read_text(encoding="utf-8")
        return StoredStage(
            json.loads((d / "snapshot.json").read_text(encoding="utf-8")),
            [PredictionRecord.from_json(line) for line in text.splitlines() if line.strip()],
            json.loads((d / "LOCK.json").read_text(encoding="utf-8")),
        )

    def write_stage(
        self, fixture_id: str, stage: SnapshotStage | str, files: dict[str, str], lock: dict
    ) -> Path:
        """`files`: name -> text. Refuses if the stage already exists (immutability)."""
        final = self.stage_dir(fixture_id, stage)
        if final.exists():
            raise StageLocked(f"{fixture_id}/{getattr(stage, 'value', stage)} is already locked")
        tmp = final.with_name(f".tmp-{uuid.uuid4().hex[:8]}")
        tmp.mkdir(parents=True)
        try:
            for name, text in files.items():
                (tmp / name).write_text(text, encoding="utf-8")
            (tmp / "LOCK.json").write_text(json.dumps(lock, indent=2, sort_keys=True), encoding="utf-8")
            tmp.replace(final)
        finally:
            if tmp.exists():
                shutil.rmtree(tmp, ignore_errors=True)
        return final

    def latest_locked_before(self, fixture_id: str, stage: SnapshotStage) -> SnapshotStage | None:
        from .stages import STAGE_ORDER

        for s in reversed(STAGE_ORDER[: STAGE_ORDER.index(stage)]):
            if self.is_locked(fixture_id, s):
                return s
        return None


def predictions_text(preds: list[PredictionRecord]) -> str:
    return dump_records(preds) + "\n" if preds else ""
