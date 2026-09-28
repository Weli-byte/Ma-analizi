"""Raw -> normalized -> validated dataset. Atomic: the current dataset is never touched until a new
version is fully built, verified and renamed into place (ADR 0003, 0011).

    python -m src.data.pipeline [--root DIR] [--mode research] [--download] [--as-of 2026-09-25]

Stages: download (optional) -> checksum -> parse -> validate -> quality -> write -> commit.
A failure at any stage leaves CURRENT.json and the previous dataset untouched.
"""

import argparse
import csv
import hashlib
import json
import os
import shutil
import sys
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb

from src.cli_utils import configure_output
from src.config import config_dir_for, load_config
from src.runmode import RunMode, policy
from src.schemas import FixtureStatus, SeasonStatus
from src.versioning import canonical_json

from .clean import CleanRow, clean_row, read_csv_rows
from .dataset import write_pointer
from .manifest import PARSER_VERSION, build_manifest
from .quality import QualityInputs, build_report, run_checks, write_report
from .raw_validation import decode
from .teams import TeamDirectory
from .timezones import CONVERSION_VERSION
from .versioning import SCHEMA_VERSION, compute_data_version, normalization_inputs_hash

STAGES = ("download", "checksum", "parse", "validate", "quality", "write", "commit")
ROOT = Path(__file__).resolve().parents[2]

DDL = {
    "leagues": "league_id VARCHAR, country VARCHAR, name VARCHAR, tier INTEGER, "
    "source_timezone VARCHAR, n_teams INTEGER, rounds INTEGER",
    "teams": "team_id VARCHAR, canonical_name VARCHAR, country VARCHAR",
    "fixtures": (
        "fixture_id VARCHAR, league_id VARCHAR, season VARCHAR, kickoff_utc TIMESTAMPTZ, "
        "kickoff_time_known BOOLEAN, home_id VARCHAR, away_id VARCHAR, status VARCHAR, "
        "actual_kickoff_utc TIMESTAMPTZ, finished_at_utc TIMESTAMPTZ, "
        "result_available_at_utc TIMESTAMPTZ, result_available_at_source VARCHAR, "
        "season_status VARCHAR, source_file VARCHAR"
    ),
    "results": "fixture_id VARCHAR, home_goals INTEGER, away_goals INTEGER, outcome VARCHAR",
    "team_match_stats": (
        "fixture_id VARCHAR, team_id VARCHAR, side VARCHAR, shots INTEGER, "
        "shots_on_target INTEGER, corners INTEGER, fouls INTEGER, yellow_cards INTEGER, "
        "red_cards INTEGER"
    ),
    "odds_snapshots": (
        "fixture_id VARCHAR, source VARCHAR, bookmaker VARCHAR, market_source_type VARCHAR, "
        "aggregate_kind VARCHAR, market VARCHAR, selection VARCHAR, price DOUBLE, "
        "snapshot_type VARCHAR, timestamp_utc TIMESTAMPTZ, timestamp_quality VARCHAR"
    ),
}
ORDER = {
    "leagues": "league_id",
    "teams": "team_id",
    "fixtures": "fixture_id",
    "results": "fixture_id",
    "team_match_stats": "fixture_id, side",
    "odds_snapshots": "fixture_id, snapshot_type, market_source_type, bookmaker, aggregate_kind, selection",
}


class PipelineError(RuntimeError):
    pass


@dataclass(frozen=True)
class PipelineResult:
    data_version: str
    path: Path
    report: dict
    unchanged: bool  # True when an identical dataset version already existed


def _iso(dt: datetime | None) -> str:
    return "" if dt is None else dt.astimezone(UTC).isoformat()


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


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


def table_hashes(con) -> dict[str, str]:
    out = {}
    for name, order in ORDER.items():
        rows = con.execute(f"SELECT * FROM {name} ORDER BY {order}").fetchall()
        out[name] = hashlib.sha256(repr(rows).encode("utf-8")).hexdigest()
    return out


def _cleanup_stale(processed: Path) -> None:
    for p in processed.glob(".tmp-*"):
        shutil.rmtree(p, ignore_errors=True)


def run_pipeline(
    root: Path = ROOT,
    mode: RunMode | str = RunMode.RESEARCH,
    as_of: date | None = None,
    download: bool = False,
    fetch=None,
    stage_hook: Callable[[str], None] | None = None,
) -> PipelineResult:
    mode = RunMode(mode)
    pol = policy(mode)
    hook = stage_hook or (lambda stage: None)
    cdir = config_dir_for(root)
    data_cfg = load_config("data", cdir)
    leagues = load_config("leagues", cdir)
    feat_cfg = load_config("features", cdir)
    unknown_leagues = [x for x in data_cfg.leagues if x not in leagues.leagues]
    if unknown_leagues:
        raise PipelineError(f"data.yaml leagues not defined in leagues.yaml: {unknown_leagues}")
    if as_of is None:
        as_of = date.fromisoformat(data_cfg.as_of) if data_cfg.as_of else datetime.now(UTC).date()

    processed = root / data_cfg.processed_dir
    processed.mkdir(parents=True, exist_ok=True)
    _cleanup_stale(processed)
    tmp = processed / f".tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    try:
        # 1. download (optional; each file is replaced atomically and only if valid)
        hook("download")
        if download:
            from .download import default_fetch, download_all

            _, failed = download_all(root, fetch=fetch or default_fetch)
            if failed:
                raise PipelineError("download failed:\n" + "\n".join(failed))

        # 2. checksum / provenance / raw validation
        hook("checksum")
        manifest = build_manifest(
            root / data_cfg.raw_dir,
            root / data_cfg.provenance_dir,
            root / data_cfg.expected_checksums,
            leagues,
            data_cfg,
        )
        if not manifest.entries:
            raise PipelineError(f"no raw files found in {root / data_cfg.raw_dir}")
        fatal: list[str] = []
        if pol.require_complete_provenance:
            fatal += [p for p in manifest.problems if p.startswith("provenance:")]
        if pol.require_checksums_known:
            fatal += [p for p in manifest.problems if p.startswith("checksum:")]
        if fatal:
            raise PipelineError(f"{mode.value} run refuses incomplete provenance:\n" + "\n".join(fatal))
        alias_file = cdir / "team_aliases.yaml"
        norm_hash = normalization_inputs_hash(leagues, data_cfg.leagues, alias_file)
        data_version = compute_data_version(manifest.entries, norm_hash)

        # 3. parse + normalize
        hook("parse")
        teams = TeamDirectory.load(alias_file)
        lag = timedelta(hours=feat_cfg.result_lag_hours)
        cleaned: list[tuple[str, CleanRow]] = []
        rejected: list[dict] = []
        raw_rows: Counter = Counter()
        raw_dups = 0
        seen: dict[str, str] = {}
        file_columns: dict[str, tuple[str, ...]] = {}
        for entry in sorted(manifest.entries, key=lambda e: (e.season, e.league)):
            fmt = leagues.leagues[entry.league]
            path = root / data_cfg.raw_dir / entry.filename
            text = decode(path.read_bytes())[0]
            file_columns[entry.filename] = tuple(next(csv.reader(text.splitlines())))
            fingerprints: set[tuple] = set()
            key = (entry.league, entry.season)
            for raw in read_csv_rows(path):
                raw_rows[key] += 1
                fp = tuple(raw.values())
                if any(v and v.strip() for v in fp):
                    if fp in fingerprints:
                        raw_dups += 1
                    fingerprints.add(fp)
                res = clean_row(
                    raw,
                    entry.league,
                    fmt,
                    entry.season,
                    teams,
                    data_cfg.team_resolution,
                    lag,
                    as_of,
                )
                if not isinstance(res, CleanRow):
                    rejected.append(
                        {
                            "file": entry.filename,
                            "reason": res.reason,
                            "detail": res.detail,
                            "league_season": key,
                        }
                    )
                    continue
                fid = res.fixture.fixture_id
                if fid in seen:
                    rejected.append(
                        {
                            "file": entry.filename,
                            "reason": "duplicate",
                            "detail": fid,
                            "league_season": key,
                        }
                    )
                    continue
                seen[fid] = entry.filename
                cleaned.append((entry.filename, res))

        # 4. validate cross-row invariants (schema objects were validated while parsing)
        hook("validate")
        ids = [r.fixture.fixture_id for _, r in cleaned]
        if len(ids) != len(set(ids)):
            raise PipelineError("internal error: duplicate fixture ids after de-duplication")

        # 5. quality gate
        hook("quality")
        inp = QualityInputs(
            manifest,
            cleaned,
            rejected,
            dict(raw_rows),
            raw_dups,
            teams,
            leagues,
            data_cfg,
            as_of,
            file_columns,
        )
        checks, seasons = run_checks(inp)
        report = build_report(inp, checks, seasons, data_version)
        failed_err = [c for c in checks if not c.passed and c.severity == "error"]
        failed_warn = [c for c in checks if not c.passed and c.severity == "warning"]
        blocking = (failed_err if pol.fail_on_quality_errors else []) + (
            failed_warn if pol.fail_on_quality_warnings else []
        )
        if blocking:
            _write_atomic(
                processed / "last_failed_quality_report.json",
                json.dumps(report, indent=2, sort_keys=True, default=str),
            )
            raise PipelineError(
                f"data quality gate failed in {mode.value} mode: "
                + "; ".join(f"{c.id} {c.name}: {c.observed}" for c in blocking)
            )

        # 6. write into a temporary version directory
        hook("write")
        tmp.mkdir()
        con = duckdb.connect(str(tmp / "football.duckdb"))
        con.execute("SET TimeZone='UTC'")
        for name, ddl in DDL.items():
            con.execute(f"CREATE TABLE {name} ({ddl})")
        _bulk_insert(
            con,
            "leagues",
            [
                (lid, f.country, f.name, f.tier, f.source_timezone, f.n_teams, f.rounds)
                for lid, f in sorted(leagues.leagues.items())
                if lid in data_cfg.leagues
            ],
            tmp,
        )
        used = {t for _, r in cleaned for t in (r.fixture.home_id, r.fixture.away_id)}
        _bulk_insert(
            con,
            "teams",
            [
                (t["team_id"], t["canonical_name"], t["country"])
                for t in sorted(teams.teams.values(), key=lambda x: x["team_id"])
                if t["team_id"] in used
            ],
            tmp,
        )
        fx_rows, res_rows, stat_rows, odds_rows = [], [], [], []
        for filename, r in cleaned:
            f = r.fixture
            info = seasons[(f.league_id, f.season)]
            sstatus = (
                info.status.value if f.status == FixtureStatus.FINISHED else SeasonStatus.FUTURE_FIXTURE.value
            )
            fx_rows.append(
                (
                    f.fixture_id,
                    f.league_id,
                    f.season,
                    _iso(f.kickoff_utc),
                    r.kickoff_time_known,
                    f.home_id,
                    f.away_id,
                    f.status.value,
                    _iso(f.actual_kickoff_utc),
                    _iso(f.finished_at_utc),
                    _iso(f.result_available_at_utc),
                    f.result_available_at_source,
                    sstatus,
                    filename,
                )
            )
            if f.status == FixtureStatus.FINISHED and f.outcome is not None:
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
                    o["source"],
                    o["bookmaker"],
                    o["market_source_type"],
                    o["aggregate_kind"],
                    o["market"],
                    o["selection"],
                    o["price"],
                    o["snapshot_type"],
                    o["timestamp_utc"],
                    o["timestamp_quality"],
                )
                for o in r.odds
            ]
        for table, rows in (
            ("fixtures", fx_rows),
            ("results", res_rows),
            ("team_match_stats", stat_rows),
            ("odds_snapshots", odds_rows),
        ):
            _bulk_insert(con, table, rows, tmp)
        hashes = table_hashes(con)
        content_hash = hashlib.sha256(canonical_json(hashes).encode()).hexdigest()
        for name, order in ORDER.items():
            target = (tmp / (name + ".parquet")).as_posix()
            con.execute(f"COPY (SELECT * FROM {name} ORDER BY {order}) TO '{target}' (FORMAT PARQUET)")
        con.close()
        (tmp / "manifest.json").write_text(manifest.to_json(), encoding="utf-8")
        write_report(report, tmp)
        (tmp / "team_mapping.json").write_text(
            json.dumps(
                {
                    "teams": teams.teams,
                    "review_queue": teams.review_queue,
                    "usage": dict(sorted(teams.usage.items())),
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        meta = {
            "data_version": data_version,
            "content_hash": content_hash,
            "table_hashes": hashes,
            "parser_version": PARSER_VERSION,
            "schema_version": SCHEMA_VERSION,
            "timezone_conversion_version": CONVERSION_VERSION,
            "timezone_source": "league source_timezone (configs/leagues.yaml)",
            "as_of": as_of.isoformat(),
            "normalization_inputs_hash": norm_hash,
            "raw_files": [
                {"file": e.filename, "sha256": e.sha256, "origin": e.origin} for e in manifest.entries
            ],
            "season_status": {f"{k[0]}|{k[1]}": v.status.value for k, v in sorted(seasons.items())},
            "quality_summary": report["summary"],
        }
        (tmp / "dataset_meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")

        # 7. commit: atomic rename, then atomic pointer update
        hook("commit")
        final = processed / data_version
        unchanged = False
        if final.exists():
            existing = json.loads((final / "dataset_meta.json").read_text(encoding="utf-8"))
            if existing.get("content_hash") != content_hash:
                raise PipelineError(
                    f"{data_version} already exists with different content: bump PARSER_VERSION/"
                    "SCHEMA_VERSION when normalization logic changes"
                )
            unchanged = True
            shutil.rmtree(tmp)
        else:
            os.replace(tmp, final)
        write_pointer(processed, data_version, content_hash)
        return PipelineResult(data_version, final, report, unchanged)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    configure_output()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--mode", default="research", choices=[m.value for m in RunMode])
    p.add_argument("--download", action="store_true", help="fetch raw files first (verified TLS)")
    p.add_argument("--as-of", default=None)
    a = p.parse_args(argv)
    try:
        res = run_pipeline(
            Path(a.root),
            a.mode,
            date.fromisoformat(a.as_of) if a.as_of else None,
            download=a.download,
        )
    except (PipelineError, ValueError, OSError) as e:
        print(f"PIPELINE FAILED: {e}", file=sys.stderr)
        return 2
    s = res.report["summary"]
    print(
        json.dumps(
            {
                "data_version": res.data_version,
                "unchanged": res.unchanged,
                "accepted_fixtures": s["accepted_fixtures"],
                "rejected_rows": s["rejected_rows"],
                "failed_error_checks": s["errors"],
                "failed_warning_checks": s["warnings"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
