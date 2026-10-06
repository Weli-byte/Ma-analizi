"""Match state from normalized live snapshots (ADR 0029). Immutable; every state has a content
hash so a forecast can name exactly which observed state it used.

Cards, substitutions and VAR are NOT tracked as counts: no implemented feed supplies them, so a
count of 0 would be a fabricated "no cards" -- they stay absent (see `LiveSnapshot.capabilities`).
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime

from src.versioning import canonical_json

from .events import LiveSnapshot, MatchStatus
from .feeds import infer_minute

MINUTE_BUCKET = 5  # a pure clock tick re-forecasts at most every 5 match minutes


@dataclass(frozen=True)
class MatchState:
    fixture_id: str
    source: str
    status: MatchStatus
    status_source: str
    score: tuple[int, int] | None
    minute: int | None
    minute_source: str | None
    event_ids: tuple[str, ...]
    observed_at: datetime

    @property
    def state_hash(self) -> str:
        bucket = None if self.minute is None else self.minute // MINUTE_BUCKET
        payload = {
            "fixture": self.fixture_id, "status": self.status.value, "score": self.score,
            "minute_bucket": bucket, "events": sorted(self.event_ids),
        }  # fmt: skip
        return hashlib.sha256(canonical_json(payload).encode()).hexdigest()

    def to_dict(self) -> dict:
        return {
            "fixture_id": self.fixture_id, "source": self.source, "status": self.status.value,
            "status_source": self.status_source, "score": list(self.score) if self.score else None,
            "minute": self.minute, "minute_source": self.minute_source,
            "n_events": len(self.event_ids), "observed_at": self.observed_at.isoformat(),
            "state_hash": self.state_hash,
        }  # fmt: skip


def build_state(
    snapshot: LiveSnapshot, all_event_ids: tuple[str, ...], last_event_minute: int | None
) -> MatchState:
    if snapshot.minute_reported is not None:
        minute, source = snapshot.minute_reported, "reported"
    elif snapshot.status in (MatchStatus.IN_PLAY, MatchStatus.HALF_TIME):
        minute, source = infer_minute(
            snapshot.kickoff_utc, snapshot.observed_at, snapshot.status, last_event_minute
        )
    else:
        minute, source = None, None
    return MatchState(
        snapshot.fixture_id, snapshot.source, snapshot.status, snapshot.status_source,
        snapshot.score, minute, source, tuple(all_event_ids), snapshot.observed_at,
    )  # fmt: skip
