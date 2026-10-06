"""Live tick (ADR 0029):  FEED -> normalized snapshot -> events (deduped) -> match state ->
in-play model -> immutable LivePredictionRecord -> (calibration hook).

LIVE is its own execution mode: it only ever writes `LivePredictionRecord`s to the live store and
never touches the pre-match ledger. Pre-match models and the pre-match benchmark cannot receive a
live record (different type, and `observed_at >= kickoff` is enforced by the record itself).

A forecast is made only when everything it needs was OBSERVED: status IN_PLAY/HALF_TIME, a score
and a minute, plus the pre-match goal rates. Otherwise the tick reports why it did not forecast;
nothing is substituted. Calibration: no live outcome history exists yet, so records carry
`NOT_CALIBRATED` (a temperature can be passed once live calibration data exists).
"""

from dataclasses import dataclass
from datetime import datetime

from src.evaluation.calibration import apply_temperature
from src.schemas import LivePredictionRecord

from .events import MatchStatus
from .feeds import derive_goal_events
from .inplay import inplay_probs
from .state import build_state
from .store import LiveStore

MODEL_ID = "inplay_poisson"
MODEL_VERSION = "1.0.0"
FORECASTABLE = (MatchStatus.IN_PLAY, MatchStatus.HALF_TIME)


@dataclass(frozen=True)
class PrematchRates:
    home: float
    away: float
    source: str  # e.g. "poisson fit on train+validation seasons"
    note: str = ""  # e.g. "home team unseen in training: league-average strength"


@dataclass(frozen=True)
class TickResult:
    status: str  # FORECAST | NO_CHANGE | STATE_ONLY | FINISHED
    n_new_events: int
    reason: str | None = None
    forecast: LivePredictionRecord | None = None


def process_snapshot(
    store: LiveStore,
    snapshot,
    rates: PrematchRates | None,
    now: datetime,
    data_version: str,
    feature_version: str,
    temperature: float | None = None,
) -> TickResult:
    prev = store.last_state()
    prev_score = tuple(prev["score"]) if prev and prev["score"] else None
    new_events = store.add_events(list(snapshot.events) + derive_goal_events(prev_score, snapshot))

    all_events = store.events()
    last_minute = max((e["minute"] for e in all_events if e["minute"] is not None), default=None)
    state = build_state(snapshot, tuple(e["event_id"] for e in all_events), last_minute)

    if prev and prev["state_hash"] == state.state_hash:
        return TickResult("NO_CHANGE", len(new_events))
    store.append_state(state)

    if state.status == MatchStatus.FINISHED:
        return TickResult("FINISHED", len(new_events), "result observed; no forecast after full time")
    if state.status not in FORECASTABLE:
        return TickResult("STATE_ONLY", len(new_events), f"status {state.status.value}: not in play")
    missing = [
        n
        for n, v in (("score", state.score), ("minute", state.minute), ("prematch_rates", rates))
        if v is None
    ]
    if missing:
        return TickResult("STATE_ONLY", len(new_events), f"cannot forecast, missing: {', '.join(missing)}")

    p_home, p_draw, p_away = inplay_probs(rates.home, rates.away, state.minute, *state.score)
    calibrated = temperature is not None
    if calibrated:
        p_home, p_draw, p_away = (
            float(x) for x in apply_temperature([[p_home, p_draw, p_away]], temperature)[0]
        )
    notes = "; ".join(
        x
        for x in (
            f"minute {state.minute_source}",
            f"status {state.status_source}",
            f"rates: {rates.source}",
            rates.note,
        )
        if x
    )
    rec = LivePredictionRecord(
        fixture_id=state.fixture_id,
        model_id=MODEL_ID,
        model_version=MODEL_VERSION,
        data_version=data_version,
        feature_version=feature_version,
        kickoff_utc=snapshot.kickoff_utc,
        observed_at=snapshot.observed_at,
        generated_at=max(now, snapshot.observed_at),
        state_hash=state.state_hash,
        status=state.status.value,
        match_minute=state.minute,
        minute_source=state.minute_source,
        score_home=state.score[0],
        score_away=state.score[1],
        prematch_rate_home=rates.home,
        prematch_rate_away=rates.away,
        p_home=p_home,
        p_draw=p_draw,
        p_away=p_away,
        calibration_status="CALIBRATED" if calibrated else "NOT_CALIBRATED",
        notes=notes,
    )
    store.append_prediction(rec)
    return TickResult("FORECAST", len(new_events), forecast=rec)
