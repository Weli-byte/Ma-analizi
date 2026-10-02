"""Value analytics (edge / EV / CLV) with the timestamp gate (ADR 0030). PAPER ONLY: nothing here
places or implies a real bet.

Gate: a value row is produced only from a COMPLETE, internally consistent 1X2 snapshot (same
bookmaker, same observation time) whose three quotes are all `timestamp_quality == "exact"`, observed
at/after the model forecast was generated (`prediction.generated_at <= observed_at`) and strictly
before kickoff. Anything else returns `NOT_ELIGIBLE` with the reason and NO numbers: an EV is never
exposed for a quote that is not exact.
"""

from dataclasses import dataclass
from datetime import datetime

import numpy as np

from .math import clv, devig, edge, ev, overround
from .quotes import OddsQuote

SELECTIONS = ("H", "D", "A")


@dataclass(frozen=True)
class ValueRow:
    status: str  # ELIGIBLE | NOT_ELIGIBLE
    reason: str | None
    fixture_id: str
    model_id: str
    bookmaker: str | None = None
    observed_at: datetime | None = None
    odds: tuple[float, float, float] | None = None
    model_probs: tuple[float, float, float] | None = None
    market_probs_devig: tuple[float, float, float] | None = None
    overround: float | None = None
    edge: tuple[float, float, float] | None = None
    ev: tuple[float, float, float] | None = None
    source_latency_s: float | None = None  # MEASURED feed latency of the quotes used (exact quotes only)
    # market REFERENCE for non-exact snapshots: de-vigged implied probabilities, NOT a signal, no edge/EV
    reference_market_probs: tuple[float, float, float] | None = None


def _not_eligible(reason: str, fixture_id: str, model_id: str) -> ValueRow:
    return ValueRow("NOT_ELIGIBLE", reason, fixture_id, model_id)


def snapshots(quotes: list[OddsQuote]) -> dict[tuple[str, datetime], dict[str, OddsQuote]]:
    """Group pre-match/live quotes into (bookmaker, observed_at) -> {selection: quote}."""
    out: dict[tuple[str, datetime], dict[str, OddsQuote]] = {}
    for q in quotes:
        if q.snapshot_type in ("pre_match", "closing", "live"):
            out.setdefault((q.bookmaker, q.observed_at), {})[q.selection] = q
    return out


def value_row(
    prediction, quotes: list[OddsQuote], kickoff_utc: datetime, fixture_id: str | None = None
) -> ValueRow:
    """`prediction`: any record with model_id, generated_at, p_home, p_draw, p_away."""
    fid = fixture_id or prediction.fixture_id
    cands = [(k, v) for k, v in snapshots(quotes).items() if len(v) == 3]
    if not cands:
        return _not_eligible("no complete 1X2 quote snapshot", fid, prediction.model_id)
    usable = [
        (k, v)
        for k, v in cands
        if k[1] >= prediction.generated_at and k[1] < kickoff_utc  # decision after the forecast, pre-kickoff
    ]
    if not usable:
        return _not_eligible(
            "no quote observed after the forecast and before kickoff", fid, prediction.model_id
        )
    (book, observed), snap = max(usable, key=lambda kv: kv[0][1])  # most recent eligible observation
    odds = tuple(snap[s].decimal_odds for s in SELECTIONS)
    qualities = sorted({q.timestamp_quality for q in snap.values()})
    if qualities != ["exact"]:
        return ValueRow(
            "NOT_ELIGIBLE",
            f"timestamp_quality is {'/'.join(qualities)}, not exact: no edge/EV/CLV (market reference only)",
            fid, prediction.model_id, book, observed, None, None, None, None, None, None, None,
            tuple(float(x) for x in devig(odds)),
        )  # fmt: skip
    latency = max(q.source_latency_s for q in snap.values())
    p = (prediction.p_home, prediction.p_draw, prediction.p_away)
    return ValueRow(
        "ELIGIBLE", None, fid, prediction.model_id, book, observed, odds, p,
        tuple(float(x) for x in devig(odds)), overround(odds),
        tuple(float(x) for x in edge(p, odds)), tuple(float(x) for x in ev(p, odds)), latency,
    )  # fmt: skip


def closing_reference(quotes: list[OddsQuote], kickoff_utc: datetime) -> tuple[float, float, float] | None:
    """The LAST complete exact snapshot observed before kickoff (a collector observation, so it can be
    older than the true close by up to the collection interval). None if there is none."""
    snaps = [
        (k, v)
        for k, v in snapshots(quotes).items()
        if len(v) == 3 and k[1] < kickoff_utc and all(q.timestamp_quality == "exact" for q in v.values())
    ]
    if not snaps:
        return None
    _, v = max(snaps, key=lambda kv: kv[0][1])
    return tuple(v[s].decimal_odds for s in SELECTIONS)


def clv_for(row: ValueRow, selection: str, closing_odds: tuple[float, float, float]) -> float:
    if row.status != "ELIGIBLE":
        raise ValueError("CLV needs an ELIGIBLE value row")
    i = SELECTIONS.index(selection)
    return clv(row.odds[i], np.asarray(closing_odds), i)
