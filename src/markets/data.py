"""Match rows with post-match statistics for the markets models (ADR 0041).

Rows come from the processed dataset (goals + corners/cards/shots from football-data.co.uk) and from the
ingested recent results (goals only; their statistics are None, never imputed). A row is HISTORY only from
its `available_at` (the same inferred/observed result time the feature builder uses).
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from src.data.dataset import DatasetRef, open_db

STATS = ("corners", "yellow_cards", "shots_on_target")


@dataclass(frozen=True)
class StatMatch:
    fixture_id: str
    season: str
    league: str
    kickoff_utc: datetime
    available_at: datetime
    home_id: str
    away_id: str
    home_goals: int
    away_goals: int
    home_stats: dict  # stat -> int | None
    away_stats: dict
    closing_avg: tuple[float, float, float] | None  # market-implied reference (H, D, A decimal odds)


def load_stat_matches(ref: DatasetRef, seasons: list[str] | None = None) -> list[StatMatch]:
    """FINISHED matches. `seasons=None` loads everything (production inference only); evaluation passes the
    train+validation seasons explicitly so the locked final-test seasons are never read."""
    con = open_db(ref.db_path)
    where = "" if seasons is None else f"AND f.season IN ({','.join('?' * len(seasons))})"
    rows = con.execute(
        "SELECT f.fixture_id, f.season, f.league_id, f.kickoff_utc, f.result_available_at_utc, f.home_id, "
        f"f.away_id, r.home_goals, r.away_goals FROM fixtures f JOIN results r USING (fixture_id) "
        f"WHERE f.status='finished' {where} ORDER BY f.kickoff_utc, f.fixture_id",
        seasons or [],
    ).fetchall()
    stats: dict[tuple[str, str], dict] = {}
    for fid, side, *vals in con.execute(
        "SELECT fixture_id, side, corners, yellow_cards, shots_on_target FROM team_match_stats"
    ).fetchall():
        stats[(fid, side)] = dict(zip(STATS, vals, strict=True))
    closing: dict[str, dict[str, float]] = {}
    for fid, sel, price in con.execute(
        "SELECT fixture_id, selection, price FROM odds_snapshots WHERE snapshot_type='closing' "
        "AND market_source_type='aggregate' AND aggregate_kind='avg'"
    ).fetchall():
        closing.setdefault(fid, {})[sel] = price
    con.close()
    empty = dict.fromkeys(STATS)
    out = []
    for fid, season, league, ko, avail, h, a, hg, ag in rows:
        if avail is None:
            continue
        c = closing.get(fid)
        out.append(
            StatMatch(
                fid,
                season,
                league,
                ko.astimezone(UTC),
                avail.astimezone(UTC),
                h,
                a,
                hg,
                ag,
                stats.get((fid, "home"), empty),
                stats.get((fid, "away"), empty),
                (c["H"], c["D"], c["A"]) if c and {"H", "D", "A"} <= set(c) else None,
            )  # fmt: skip
        )
    return out


def ingested_stat_matches(root: Path) -> list[StatMatch]:
    """Recent results seen by the ingestion store (goals only)."""
    from src.ingestion.results import read_store

    empty = dict.fromkeys(STATS)
    out = []
    for r in read_store(root):
        out.append(
            StatMatch(
                r["fixture_id"],
                r["season"],
                r["league"],
                datetime.fromisoformat(r["kickoff_utc"]),
                datetime.fromisoformat(r["first_seen_utc"]),
                r["home_id"],
                r["away_id"],
                r["home_goals"],
                r["away_goals"],
                empty,
                empty,
                None,
            )  # fmt: skip
        )
    return out


def merge(dataset: list[StatMatch], extra: list[StatMatch]) -> list[StatMatch]:
    """Dataset first; an ingested match is added only if the dataset lacks that team pair on that day."""
    key = lambda m: (m.home_id, m.away_id, m.kickoff_utc.date())  # noqa: E731
    have = {key(m) for m in dataset}
    return sorted([*dataset, *(m for m in extra if key(m) not in have)], key=lambda m: m.kickoff_utc)
