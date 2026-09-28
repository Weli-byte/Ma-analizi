"""Prediction status machine and an append-only ledger that enforces immutability.

    DRAFT -> PUBLISHED -> LOCKED -> EVALUATED
      \\-> VOID    \\-> VOID          (a void prediction is never evaluated)

Probabilities can never change: transition() only changes `status`; the ledger rejects a record
whose logical_id already exists with different content.
"""

import json
from collections.abc import Iterable
from pathlib import Path

from .common import PredictionStatus as S
from .prediction import PredictionRecord

TRANSITIONS: dict[S, frozenset[S]] = {
    S.DRAFT: frozenset({S.PUBLISHED, S.VOID}),
    S.PUBLISHED: frozenset({S.LOCKED, S.VOID}),
    S.LOCKED: frozenset({S.EVALUATED}),
    S.EVALUATED: frozenset(),
    S.VOID: frozenset(),
}


class InvalidTransition(ValueError):
    pass


class LedgerConflict(ValueError):
    """Same logical prediction already recorded with different content."""


def can_transition(old: S, new: S) -> bool:
    return new in TRANSITIONS[old]


def transition(record: PredictionRecord, new_status: S) -> PredictionRecord:
    if not can_transition(record.status, new_status):
        raise InvalidTransition(f"{record.status.value} -> {new_status.value} is not allowed")
    data = record.model_dump(exclude={"logical_id", "content_hash", "prediction_id"})
    data["status"] = new_status
    return PredictionRecord.model_validate(data)


class PredictionLedger:
    """Append-only. Optional JSONL persistence (one record per line, full history kept)."""

    def __init__(self, path: Path | None = None):
        self.path = path
        self._history: dict[str, list[PredictionRecord]] = {}
        if path is not None and path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self._apply(PredictionRecord.from_json(line))

    def _apply(self, rec: PredictionRecord) -> bool:
        hist = self._history.setdefault(rec.logical_id, [])
        if not hist:
            if rec.status != S.DRAFT:
                raise InvalidTransition(f"first record must be DRAFT, got {rec.status.value}")
            hist.append(rec)
            return True
        last = hist[-1]
        if rec.content_hash != last.content_hash:
            raise LedgerConflict(f"logical prediction {rec.logical_id} already exists with different content")
        if rec.status == last.status:
            return False  # idempotent re-append
        if not can_transition(last.status, rec.status):
            raise InvalidTransition(f"{last.status.value} -> {rec.status.value} is not allowed")
        hist.append(rec)
        return True

    def append(self, rec: PredictionRecord) -> bool:
        """Returns True if the ledger changed. Raises on conflicts / invalid transitions."""
        changed = self._apply(rec)
        if changed and self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(rec.model_dump_json() + "\n")
        return changed

    def extend(self, recs: Iterable[PredictionRecord]) -> int:
        return sum(self.append(r) for r in recs)

    def latest(self, logical_id: str) -> PredictionRecord:
        return self._history[logical_id][-1]

    def latest_records(self) -> list[PredictionRecord]:
        return [h[-1] for _, h in sorted(self._history.items())]

    def __len__(self) -> int:
        return len(self._history)


def dump_records(records: Iterable[PredictionRecord]) -> str:
    """Deterministic JSONL (sorted by fixture/model) for hashing and artifacts."""
    ordered = sorted(records, key=lambda r: (r.fixture_id, r.model_id, r.model_version))
    return "\n".join(json.dumps(json.loads(r.model_dump_json()), sort_keys=True) for r in ordered)
