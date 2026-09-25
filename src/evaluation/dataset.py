"""Load evaluation rows (fixture + outcome + fv1 features + odds) from the processed dataset."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import duckdb

OUTCOME_INDEX = {"H": 0, "D": 1, "A": 2}


@dataclass(frozen=True)
class EvalRow:
    fixture_id: str
    league_id: str
    season: str
    kickoff_utc: datetime
    outcome: int  # 0 H, 1 D, 2 A
    features: dict[str, float | None] = field(default_factory=dict)
    odds: dict[str, tuple[float, float, float]] = field(default_factory=dict)  # "kind:book"


def load_rows(db_path: Path, features_path: Path | None, seasons: list[str]) -> list[EvalRow]:
    con = duckdb.connect(str(db_path), read_only=True)
    marks = ",".join("?" * len(seasons))
    fx = con.execute(
        "SELECT f.fixture_id, f.league_id, f.season, f.kickoff_utc, r.outcome "
        "FROM fixtures f JOIN results r USING (fixture_id) "
        f"WHERE f.season IN ({marks}) ORDER BY f.kickoff_utc, f.fixture_id",
        seasons,
    ).fetchall()
    ids = [r[0] for r in fx]

    odds: dict[str, dict[str, dict[str, float]]] = {}
    for fid, kind, book, sel, val in con.execute(
        f"SELECT fixture_id, snapshot_kind, bookmaker, selection, decimal_odds "
        f"FROM odds_snapshots WHERE fixture_id IN (SELECT fixture_id FROM fixtures "
        f"WHERE season IN ({marks}))",
        seasons,
    ).fetchall():
        odds.setdefault(fid, {}).setdefault(f"{kind}:{book}", {})[sel] = val
    con.close()

    feats: dict[str, dict[str, float | None]] = {}
    if features_path is not None and features_path.exists():
        fcon = duckdb.connect()
        cur = fcon.execute(f"SELECT * FROM read_parquet('{features_path.as_posix()}')")
        names = [d[0] for d in cur.description]
        for row in cur.fetchall():
            feats[row[0]] = dict(zip(names[2:], row[2:], strict=True))
        fcon.close()

    rows = []
    for fid, league, season, kickoff, outcome in fx:
        book_odds = {
            k: (v["H"], v["D"], v["A"]) for k, v in odds.get(fid, {}).items() if len(v) == 3
        }
        rows.append(
            EvalRow(
                fid,
                league,
                season,
                kickoff.replace(tzinfo=UTC),
                OUTCOME_INDEX[outcome],
                feats.get(fid, {}),
                book_odds,
            )
        )
    assert len(rows) == len(ids)
    return rows
