"""S12: coverage matrix (league x season x endpoint -> last_success / freshness) and a freshness
monitor that flags stale cells, instead of ingestion silently going quiet on a league/endpoint.
"""

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class CoverageCell:
    league_id: str
    season: str
    endpoint: str
    last_success_utc: str  # ISO 8601; string (not datetime) so JSON round-trips exactly
    last_error: str | None = None


class CoverageMatrix:
    """One cell per (league_id, season, endpoint). `record_success`/`record_error` are the only
    writers -- a cell's `last_success_utc` only ever moves forward in time via `record_success`;
    an error never overwrites a prior success, it is tracked in `last_error` alongside it, so a
    transient failure doesn't erase evidence that the endpoint DID work before."""

    def __init__(self):
        self._cells: dict[tuple[str, str, str], CoverageCell] = {}

    def record_success(self, league_id: str, season: str, endpoint: str, at: datetime | None = None) -> None:
        when = (at or datetime.now(UTC)).isoformat()
        key = (league_id, season, endpoint)
        self._cells[key] = CoverageCell(league_id, season, endpoint, when, last_error=None)

    def record_error(self, league_id: str, season: str, endpoint: str, error: str) -> None:
        key = (league_id, season, endpoint)
        prior = self._cells.get(key)
        last_success = prior.last_success_utc if prior else ""
        self._cells[key] = CoverageCell(league_id, season, endpoint, last_success, last_error=error)

    def cells(self) -> list[CoverageCell]:
        return sorted(self._cells.values(), key=lambda c: (c.league_id, c.season, c.endpoint))

    def stale_cells(self, max_age_hours: float, now: datetime | None = None) -> list[CoverageCell]:
        """Cells whose last success is older than `max_age_hours`, OR that have NEVER succeeded
        (empty `last_success_utc`) -- both are freshness failures, reported together."""
        ref = now or datetime.now(UTC)
        stale = []
        for cell in self.cells():
            if not cell.last_success_utc:
                stale.append(cell)
                continue
            age_hours = (ref - datetime.fromisoformat(cell.last_success_utc)).total_seconds() / 3600
            if age_hours > max_age_hours:
                stale.append(cell)
        return stale

    def to_json(self) -> str:
        return json.dumps([asdict(c) for c in self.cells()], indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> "CoverageMatrix":
        m = cls()
        for row in json.loads(text):
            m._cells[(row["league_id"], row["season"], row["endpoint"])] = CoverageCell(**row)
        return m

    def dump(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "CoverageMatrix":
        if not path.exists():
            return cls()
        return cls.from_json(path.read_text(encoding="utf-8"))
