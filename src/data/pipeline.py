"""Raw -> cleaned -> processed pipeline. Deterministic and idempotent (full rebuild from raw).

python -m src.data.pipeline [--raw-dir data/raw/football_data] [--data-version dv1]
"""

import argparse
import csv
import json
import shutil
from collections import Counter
from pathlib import Path

import duckdb

from src.config import CONFIG_DIR, load_config

from .clean import CleanRow, clean_row, league_of, read_csv_rows
from .leagues import LEAGUES
from .manifest import RawFile, build_manifest
from .quality import build_report, write_report
from .teams import TeamRegistry

ROOT = Path(__file__).resolve().parents[2]

DDL = {
    "leagues": "league_id VARCHAR, country VARCHAR, name VARCHAR, tier INTEGER",
    "teams": "team_id VARCHAR, canonical_name VARCHAR, country VARCHAR",
    "fixtures": (
        "fixture_id VARCHAR, league_id VARCHAR, season VARCHAR, kickoff_utc TIMESTAMP, "
        "kickoff_time_known BOOLEAN, home_id VARCHAR, away_id VARCHAR, status VARCHAR, "
        "source_file VARCHAR"
    ),
    "results": ("fixture_id VARCHAR, home_goals INTEGER, away_goals INTEGER, outcome VARCHAR"),
    "team_match_stats": (
        "fixture_id VARCHAR, team_id VARCHAR, side VARCHAR, shots INTEGER, "
        "shots_on_target INTEGER, corners INTEGER, fouls INTEGER, yellow_cards INTEGER, "
        "red_cards INTEGER"
    ),
    "odds_snapshots": (
        "fixture_id VARCHAR, bookmaker VARCHAR, market VARCHAR, selection VARCHAR, "
        "decimal_odds DOUBLE, snapshot_kind VARCHAR, timestamp_utc TIMESTAMP"
    ),
}
ORDER = {
    "leagues": "league_id",
    "teams": "team_id",
    "fixtures": "fixture_id",
    "results": "fixture_id",
    "team_match_stats": "fixture_id, side",
    "odds_snapshots": "fixture_id, snapshot_kind, bookmaker, selection",
}


def _bulk_insert(con, table: str, rows: list[tuple], tmp_dir: Path) -> None:
    """COPY from a temp CSV (executemany is far too slow in DuckDB). None -> empty = NULL."""
    if not rows:
        return
    path = tmp_dir / f"_{table}.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        for row in rows:
            w.writerow(["" if v is None else v for v in row])
    con.execute(f"COPY {table} FROM '{path.as_posix()}' (FORMAT CSV, HEADER false)")
    path.unlink()


def run_pipeline(
    raw_dir: Path,
    out_dir: Path,
    data_version: str,
    reports_dir: Path,
    alias_file: Path | None = None,
) -> dict:
    manifest = build_manifest(raw_dir, raw_dir / "manifest.json")
    teams = TeamRegistry.from_alias_file(alias_file or CONFIG_DIR / "team_aliases.yaml")

    cleaned: list[tuple[RawFile, CleanRow]] = []
    rejected: list[dict] = []
    raw_rows = Counter()
    seen: dict[str, str] = {}
    duplicates = 0
    for entry in sorted(manifest, key=lambda e: (e.season, e.league_id)):
        league = league_of(entry.league_id)
        for raw in read_csv_rows(raw_dir / entry.path):
            raw_rows[(entry.league_id, entry.season)] += 1
            res = clean_row(raw, league, entry.season, teams)
            if not isinstance(res, CleanRow):
                rejected.append({"file": entry.path, "reason": res.reason, "detail": res.detail})
                continue
            if res.fixture.fixture_id in seen:
                duplicates += 1
                rejected.append(
                    {"file": entry.path, "reason": "duplicate", "detail": res.fixture.fixture_id}
                )
                continue
            seen[res.fixture.fixture_id] = entry.path
            cleaned.append((entry, res))

    # processed layer -> DuckDB + Parquet (rebuilt from scratch: idempotent)
    version_dir = out_dir / data_version
    if version_dir.exists():
        shutil.rmtree(version_dir)
    version_dir.mkdir(parents=True)
    con = duckdb.connect(str(version_dir / "football.duckdb"))
    con.execute("SET TimeZone='UTC'")
    for name, ddl in DDL.items():
        con.execute(f"CREATE TABLE {name} ({ddl})")
    con.executemany(
        "INSERT INTO leagues VALUES (?,?,?,?)",
        [(lg.league_id, lg.country, lg.name, lg.tier) for lg in LEAGUES.values()],
    )
    if teams.teams:
        con.executemany(
            "INSERT INTO teams VALUES (?,?,?)",
            [(t["team_id"], t["canonical_name"], t["country"]) for t in teams.teams.values()],
        )
    fx_rows, res_rows, stat_rows, odds_rows = [], [], [], []
    for entry, r in cleaned:
        f = r.fixture
        fx_rows.append(
            (
                f.fixture_id,
                f.league_id,
                f.season,
                f.kickoff_utc.replace(tzinfo=None),  # stored as naive UTC
                r.kickoff_time_known,
                f.home_id,
                f.away_id,
                f.status.value,
                entry.path,
            )
        )
        res_rows.append((f.fixture_id, f.home_goals, f.away_goals, f.outcome.value))
        stat_rows += [
            (
                s["fixture_id"],
                s["team_id"],
                s["side"],
                s["shots"],
                s["shots_on_target"],
                s["corners"],
                s["fouls"],
                s["yellow_cards"],
                s["red_cards"],
            )
            for s in r.stats
        ]
        odds_rows += [
            (
                o["fixture_id"],
                o["bookmaker"],
                o["market"],
                o["selection"],
                o["decimal_odds"],
                o["snapshot_kind"],
                o["timestamp_utc"],
            )
            for o in r.odds
        ]
    for table, rows in (
        ("fixtures", fx_rows),
        ("results", res_rows),
        ("team_match_stats", stat_rows),
        ("odds_snapshots", odds_rows),
    ):
        _bulk_insert(con, table, rows, version_dir)
    for name, order in ORDER.items():
        con.execute(
            f"COPY (SELECT * FROM {name} ORDER BY {order}) "
            f"TO '{(version_dir / (name + '.parquet')).as_posix()}' (FORMAT PARQUET)"
        )
    (version_dir / "team_mapping.json").write_text(teams.to_json(), encoding="utf-8")

    report = build_report(con, data_version, manifest, raw_rows, rejected, cleaned, teams)
    con.close()
    write_report(report, reports_dir, data_version)
    return report


def main() -> None:
    cfg = load_config("data")
    p = argparse.ArgumentParser()
    p.add_argument("--raw-dir", default=str(ROOT / cfg.raw_dir / "football_data"))
    p.add_argument("--out-dir", default=str(ROOT / cfg.processed_dir))
    p.add_argument("--reports-dir", default=str(ROOT / "reports"))
    p.add_argument("--data-version", default=cfg.data_version)
    a = p.parse_args()
    rep = run_pipeline(Path(a.raw_dir), Path(a.out_dir), a.data_version, Path(a.reports_dir))
    print(json.dumps(rep["summary"], indent=2))


if __name__ == "__main__":
    main()
