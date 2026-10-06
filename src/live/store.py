"""Append-only live store (ADR 0029): artifacts/live/<fixture_id>/{events,states,predictions}.jsonl.

Events are de-duplicated by their content-derived `event_id` (re-polling never duplicates). Live
forecasts follow the same immutability rule as the pre-match ledger: the same logical forecast
(fixture + model + version + state hash) with different content is a conflict and is rejected.
"""

import json
from pathlib import Path

from src.schemas import LivePredictionRecord

from .events import LiveEvent
from .state import MatchState


class LiveLedgerConflict(RuntimeError):
    pass


class LiveStore:
    def __init__(self, root: Path, fixture_id: str):
        self.dir = Path(root) / "artifacts" / "live" / fixture_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.dir / "events.jsonl"
        self.states_path = self.dir / "states.jsonl"
        self.predictions_path = self.dir / "predictions.jsonl"

    @staticmethod
    def _read(path: Path) -> list[dict]:
        if not path.exists():
            return []
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]

    @staticmethod
    def _append(path: Path, row: dict) -> None:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")

    @property
    def rates_path(self) -> Path:
        return self.dir / "prematch_rates.json"

    def load_rates(self):
        """Pre-match goal rates are computed once per fixture and then frozen, so every forecast
        of the match starts from the same numbers (and the live loop never refits models)."""
        from .engine import PrematchRates

        if not self.rates_path.exists():
            return None
        return PrematchRates(**json.loads(self.rates_path.read_text(encoding="utf-8")))

    def save_rates(self, rates) -> None:
        self.rates_path.write_text(json.dumps(rates.__dict__, indent=2, sort_keys=True), encoding="utf-8")

    def events(self) -> list[dict]:
        return self._read(self.events_path)

    def add_events(self, events: list[LiveEvent]) -> list[LiveEvent]:
        """Persist only events not seen before; returns the new ones."""
        known = {e["event_id"] for e in self.events()}
        new = []
        for e in events:
            if e.event_id not in known:
                self._append(self.events_path, e.to_dict())
                known.add(e.event_id)
                new.append(e)
        return new

    def last_state(self) -> dict | None:
        rows = self._read(self.states_path)
        return rows[-1] if rows else None

    def append_state(self, state: MatchState) -> None:
        self._append(self.states_path, state.to_dict())

    def predictions(self) -> list[LivePredictionRecord]:
        return [LivePredictionRecord.from_json(json.dumps(r)) for r in self._read(self.predictions_path)]

    def append_prediction(self, rec: LivePredictionRecord) -> bool:
        for r in self.predictions():
            if r.logical_id == rec.logical_id:
                if r.content_hash != rec.content_hash:
                    raise LiveLedgerConflict(f"live forecast {rec.logical_id} exists with different content")
                return False  # idempotent
        self._append(self.predictions_path, json.loads(rec.model_dump_json()))
        return True
