"""Build versioned FeatureSnapshots from the processed dataset.

    python -m src.features.builder [--data-version dv1] [--cutoff-offset-hours 0]

Outputs (data/features/<feature_version>/): features.parquet (wide), feature_lineage.json.
"""

import argparse
import csv
import json
from datetime import UTC, timedelta
from pathlib import Path

import duckdb

from src.config import load_config
from src.schemas import FeatureSnapshot

from .compute import compute_features
from .history import MatchHistory, MatchRecord
from .leakage_audit import audit_leakage
from .registry import FEATURE_VERSION, REGISTRY, produced_names, registry_hash, spec_for

ROOT = Path(__file__).resolve().parents[2]
XG_COLUMN = "xg"  # absent in dv1; picked up automatically if a later dataset adds it


def load_matches(db_path: Path) -> list[MatchRecord]:
    con = duckdb.connect(str(db_path), read_only=True)
    cols = {r[0] for r in con.execute("DESCRIBE team_match_stats").fetchall()}
    xg = XG_COLUMN in cols
    rows = con.execute(
        "SELECT f.fixture_id, f.kickoff_utc, f.home_id, f.away_id, r.home_goals, r.away_goals"
        + (", h.xg, a.xg " if xg else ", NULL, NULL ")
        + "FROM fixtures f JOIN results r USING (fixture_id) "
        + (
            "JOIN team_match_stats h ON h.fixture_id=f.fixture_id AND h.side='home' "
            "JOIN team_match_stats a ON a.fixture_id=f.fixture_id AND a.side='away' "
            if xg
            else ""
        )
        + "ORDER BY f.kickoff_utc, f.fixture_id"
    ).fetchall()
    con.close()
    return [
        MatchRecord(fid, ko.replace(tzinfo=UTC), h, a, hg, ag, hx, ax)
        for fid, ko, h, a, hg, ag, hx, ax in rows
    ]


def build_snapshots(
    matches: list[MatchRecord], cutoff_offset_hours: float = 0.0
) -> list[FeatureSnapshot]:
    """One snapshot per fixture; cutoff = kickoff - offset. generated_at == cutoff (replayable)."""
    history = MatchHistory(matches)
    out = []
    for m in matches:
        cutoff = m.kickoff_utc - timedelta(hours=cutoff_offset_hours)
        res = compute_features(m, history, cutoff)
        out.append(
            FeatureSnapshot(
                fixture_id=m.fixture_id,
                feature_version=FEATURE_VERSION,
                kickoff_utc=m.kickoff_utc,
                information_cutoff=cutoff,
                generated_at=cutoff,
                values=res.values,
                available_at=res.available_at,
            )
        )
    return out


def lineage(data_version: str, cutoff_offset_hours: float, n_rows: int, audit_n: int) -> dict:
    return {
        "feature_version": FEATURE_VERSION,
        "registry_hash": registry_hash(),
        "dataset_version": data_version,
        "cutoff_rule": f"information_cutoff = kickoff - {cutoff_offset_hours}h",
        "rows": n_rows,
        "leakage_audit": {"samples": audit_n, "violations": 0},
        "features": {n: spec_for(n).model_dump() for n in produced_names()},
        "specs": [s.model_dump() for s in REGISTRY],
    }


def write_features(snapshots: list[FeatureSnapshot], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    cols = list(snapshots[0].values) if snapshots else []
    csv_path = out_dir / "_features.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        for s in snapshots:
            w.writerow(
                [s.fixture_id, s.information_cutoff.replace(tzinfo=None).isoformat(sep=" ")]
                + ["" if s.values[c] is None else s.values[c] for c in cols]
            )
    con = duckdb.connect()
    ddl = ", ".join(f'"{c}" DOUBLE' for c in cols)
    con.execute(f"CREATE TABLE f (fixture_id VARCHAR, information_cutoff TIMESTAMP, {ddl})")
    con.execute(f"COPY f FROM '{csv_path.as_posix()}' (FORMAT CSV, HEADER false)")
    target = out_dir / "features.parquet"
    con.execute(
        f"COPY (SELECT * FROM f ORDER BY fixture_id) TO '{target.as_posix()}' (FORMAT PARQUET)"
    )
    con.close()
    csv_path.unlink()
    return target


def main() -> None:
    cfg = load_config("data")
    p = argparse.ArgumentParser()
    p.add_argument("--data-version", default=cfg.data_version)
    p.add_argument("--cutoff-offset-hours", type=float, default=0.0)
    p.add_argument("--audit-samples", type=int, default=300)
    a = p.parse_args()
    db = ROOT / cfg.processed_dir / a.data_version / "football.duckdb"
    matches = load_matches(db)
    violations = audit_leakage(matches, n_samples=a.audit_samples)
    if violations:
        raise SystemExit(f"LEAKAGE AUDIT FAILED: {violations[:3]} ... ({len(violations)} total)")
    snaps = build_snapshots(matches, a.cutoff_offset_hours)
    out_dir = ROOT / "data" / "features" / FEATURE_VERSION
    write_features(snaps, out_dir)
    (out_dir / "feature_lineage.json").write_text(
        json.dumps(
            lineage(a.data_version, a.cutoff_offset_hours, len(snaps), a.audit_samples),
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print(f"{len(snaps)} snapshots -> {out_dir} (audit clean, {a.audit_samples} samples)")


if __name__ == "__main__":
    main()
