"""Load evaluation rows (fixture + outcome + features + odds) — the ONLY reader of season data for
evaluation. It requires an EvaluationContext, which blocks final-test seasons (ADR 0004)."""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from src.data.dataset import DatasetRef, open_db
from src.features.artifact import FeatureTable

from .context import EvaluationContext

OUTCOME_INDEX = {"H": 0, "D": 1, "A": 2}


@dataclass(frozen=True)
class EvalRow:
    fixture_id: str
    league_id: str
    season: str
    kickoff_utc: datetime
    home_id: str
    away_id: str
    outcome: int  # 0 H, 1 D, 2 A
    features: dict[str, float | None] = field(default_factory=dict)
    unavailable_reasons: dict[str, str] = field(default_factory=dict)
    # "<snapshot_type>:<source>" -> (H, D, A) decimal odds; source is a bookmaker code or agg_avg/agg_max
    odds: dict[str, tuple[float, float, float]] = field(default_factory=dict)


def load_rows(
    ref: DatasetRef,
    ctx: EvaluationContext,
    seasons: list[str],
    features: FeatureTable | None = None,
) -> list[EvalRow]:
    """Rows of the requested seasons, chronologically ordered. Raises on forbidden seasons."""
    ctx.check_seasons(seasons)
    con = open_db(ref.db_path)
    marks = ",".join("?" * len(seasons))
    fx = con.execute(
        "SELECT f.fixture_id, f.league_id, f.season, f.kickoff_utc, f.home_id, f.away_id, r.outcome "
        "FROM fixtures f JOIN results r USING (fixture_id) "
        f"WHERE f.season IN ({marks}) ORDER BY f.kickoff_utc, f.fixture_id",
        seasons,
    ).fetchall()
    odds: dict[str, dict[str, dict[str, float]]] = {}
    for fid, snap, mtype, book, kind, sel, price in con.execute(
        "SELECT fixture_id, snapshot_type, market_source_type, bookmaker, aggregate_kind, "
        "selection, price FROM odds_snapshots WHERE fixture_id IN "
        f"(SELECT fixture_id FROM fixtures WHERE season IN ({marks}))",
        seasons,
    ).fetchall():
        source = book if mtype == "bookmaker" else f"agg_{kind}"
        odds.setdefault(fid, {}).setdefault(f"{snap}:{source}", {})[sel] = price
    con.close()

    rows = []
    for fid, league, season, kickoff, home, away, outcome in fx:
        book_odds = {k: (v["H"], v["D"], v["A"]) for k, v in odds.get(fid, {}).items() if len(v) == 3}
        rows.append(
            EvalRow(
                fid,
                league,
                season,
                kickoff.astimezone(UTC),
                home,
                away,
                OUTCOME_INDEX[outcome],
                dict(features.rows.get(fid, {})) if features else {},
                dict(features.reasons.get(fid, {})) if features else {},
                book_odds,
            )
        )
    return rows
