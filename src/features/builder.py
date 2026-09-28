"""Build versioned FeatureSnapshots from the current processed dataset.

    python -m src.features.builder [--root DIR] [--mode research] [--audit-samples 300]

Output (data/features/<data_version>/<feature_version>/): features.parquet + feature_lineage.json.
The artifact is bound to the data version it was built from; loaders refuse mismatches.
"""

import argparse
import csv
import hashlib
import json
import os
import shutil
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType

import duckdb

from src.cli_utils import configure_output
from src.config import FeaturesConfig, config_dir_for, load_config
from src.data.dataset import DatasetRef, open_db, resolve_dataset
from src.provenance import collect
from src.runmode import RunMode
from src.schemas import FeatureSnapshot, FixtureStatus, thaw
from src.versioning import canonical_json, config_hash

from .artifact import artifact_dir, file_sha256
from .compute import compute_features
from .history import MatchHistory, MatchRecord
from .leakage_audit import audit_leakage
from .registry import (
    BUILDER_VERSION,
    EXPERIMENTAL,
    FEATURE_VERSION,
    REGISTRY,
    produced_names,
    registry_hash,
    spec_for,
)

ROOT = Path(__file__).resolve().parents[2]


class BuildError(RuntimeError):
    pass


@dataclass(frozen=True)
class BuildResult:
    path: Path
    n_snapshots: int
    lineage: dict


def load_matches(ref: DatasetRef) -> list[MatchRecord]:
    """FINISHED fixtures with result availability, plus post-match extras (audit-only inputs)."""
    con = open_db(ref.db_path)
    rows = con.execute(
        "SELECT f.fixture_id, f.season, f.kickoff_utc, f.home_id, f.away_id, r.home_goals, "
        "r.away_goals, f.result_available_at_utc, f.status "
        "FROM fixtures f JOIN results r USING (fixture_id) ORDER BY f.kickoff_utc, f.fixture_id"
    ).fetchall()
    shots = {
        (fid, side): sh
        for fid, side, sh in con.execute(
            "SELECT fixture_id, side, shots FROM team_match_stats WHERE shots IS NOT NULL"
        ).fetchall()
    }
    closing: dict[str, dict[str, float]] = {}
    for fid, sel, price in con.execute(
        "SELECT fixture_id, selection, price FROM odds_snapshots WHERE snapshot_type='closing' "
        "AND market_source_type='aggregate' AND aggregate_kind='avg'"
    ).fetchall():
        closing.setdefault(fid, {})[sel] = price
    con.close()
    out = []
    for fid, season, ko, h, a, hg, ag, avail, status in rows:
        extras: dict[str, float] = {}
        for side in ("home", "away"):
            if (fid, side) in shots:
                extras[f"{side}_shots"] = float(shots[(fid, side)])
        for sel, price in closing.get(fid, {}).items():
            extras[f"closing_avg_{sel}"] = float(price)
        out.append(
            MatchRecord(
                fid,
                season,
                ko.astimezone(UTC),
                h,
                a,
                hg,
                ag,
                avail.astimezone(UTC) if avail else None,
                FixtureStatus(status),
                MappingProxyType(extras),
            )
        )
    return out


def build_snapshots(
    matches: list[MatchRecord], data_version: str, cfg: FeaturesConfig
) -> list[FeatureSnapshot]:
    """One snapshot per fixture; cutoff = kickoff - offset. generated_at == cutoff (replayable)."""
    history = MatchHistory(matches)
    out = []
    for m in matches:
        cutoff = m.kickoff_utc - timedelta(hours=cfg.cutoff_offset_hours)
        res = compute_features(m, history, cutoff, cfg)
        out.append(
            FeatureSnapshot(
                fixture_id=m.fixture_id,
                feature_version=cfg.feature_version,
                data_version=data_version,
                kickoff_utc=m.kickoff_utc,
                information_cutoff=cutoff,
                generated_at=cutoff,
                values=res.values,
                available_at=res.available_at,
                unavailable_reasons=res.reasons,
            )
        )
    return out


def content_hash(snaps: list[FeatureSnapshot]) -> str:
    payload = [
        {
            "f": s.fixture_id,
            "v": thaw(s.values),
            "r": thaw(s.unavailable_reasons),
            "c": s.information_cutoff.isoformat(),
        }
        for s in sorted(snaps, key=lambda x: x.fixture_id)
    ]
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def write_features(snapshots: list[FeatureSnapshot], out_dir: Path) -> Path:
    """Write features.parquet (wide, one row per fixture) into out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cols = list(snapshots[0].values) if snapshots else []
    csv_path = out_dir / "_features.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        for s in sorted(snapshots, key=lambda x: x.fixture_id):
            w.writerow(
                [s.fixture_id, s.information_cutoff.isoformat()]
                + ["" if s.values[c] is None else s.values[c] for c in cols]
                + [json.dumps(thaw(s.unavailable_reasons), sort_keys=True)]
            )
    con = duckdb.connect()
    ddl = ", ".join(f'"{c}" DOUBLE' for c in cols)
    con.execute(
        f"CREATE TABLE f (fixture_id VARCHAR, information_cutoff_utc VARCHAR, {ddl}, "
        "unavailable_reasons VARCHAR)"
    )
    con.execute(f"COPY f FROM '{csv_path.as_posix()}' (FORMAT CSV, HEADER false)")
    target = out_dir / "features.parquet"
    con.execute(f"COPY (SELECT * FROM f ORDER BY fixture_id) TO '{target.as_posix()}' (FORMAT PARQUET)")
    con.close()
    csv_path.unlink()
    return target


def build_features(
    root: Path = ROOT, mode: RunMode | str = RunMode.RESEARCH, audit_samples: int = 300
) -> BuildResult:
    mode = RunMode(mode)
    cdir = config_dir_for(root)
    data_cfg = load_config("data", cdir)
    feat_cfg = load_config("features", cdir)
    model_cfg = load_config("model", cdir)
    for name, v in (
        ("features.yaml", feat_cfg.feature_version),
        ("model.yaml", model_cfg.feature_version),
    ):
        if v != FEATURE_VERSION:
            raise BuildError(f"{name} feature_version {v} != code FEATURE_VERSION {FEATURE_VERSION}")
    prov = collect(mode, root)
    ref = resolve_dataset(root / data_cfg.processed_dir)
    matches = load_matches(ref)
    if not matches:
        raise BuildError("dataset has no finished matches")

    violations = audit_leakage(matches, n_samples=audit_samples, seed=model_cfg.seed, cfg=feat_cfg)
    if violations:
        raise BuildError(f"LEAKAGE AUDIT FAILED: {violations[:3]} ... ({len(violations)} total)")
    snaps = build_snapshots(matches, ref.data_version, feat_cfg)

    final = artifact_dir(root, ref.data_version, FEATURE_VERSION)
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = final.parent / f".tmp-{FEATURE_VERSION}-{uuid.uuid4().hex[:8]}"
    try:
        parquet = write_features(snaps, tmp)
        lineage = {
            "data_version": ref.data_version,
            "dataset_content_hash": ref.meta["content_hash"],
            "feature_version": FEATURE_VERSION,
            "builder_version": BUILDER_VERSION,
            "registry_hash": registry_hash(),
            "source_files": [f["file"] for f in ref.meta["raw_files"]],
            "source_checksums": [f["sha256"] for f in ref.meta["raw_files"]],
            "created_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "git_sha": prov.git_sha,
            "git_dirty": prov.git_dirty,
            "config_hash": config_hash(feat_cfg.model_dump()),
            "config": feat_cfg.model_dump(),
            "python_version": prov.python_version,
            "dependency_lock_hash": prov.dependency_lock_hash,
            "run_mode": mode.value,
            "rows": len(snaps),
            "content_hash": content_hash(snaps),
            "parquet_sha256": file_sha256(parquet),
            "cutoff_rule": f"information_cutoff = kickoff - {feat_cfg.cutoff_offset_hours}h",
            "result_availability": "inferred (kickoff + result_lag_hours); ADR 0006",
            "leakage_audit": {"samples": audit_samples, "seed": model_cfg.seed, "violations": 0},
            "features": {n: spec_for(n).model_dump(mode="json") for n in produced_names()},
            "experimental_not_produced": [s.name for s in EXPERIMENTAL],
            "specs": [s.model_dump(mode="json") for s in REGISTRY],
        }
        (tmp / "feature_lineage.json").write_text(
            json.dumps(lineage, indent=2, sort_keys=True), encoding="utf-8"
        )
        if final.exists():
            old = final.with_name(f".old-{uuid.uuid4().hex[:8]}")
            os.replace(final, old)
            os.replace(tmp, final)
            shutil.rmtree(old, ignore_errors=True)
        else:
            os.replace(tmp, final)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
    return BuildResult(final, len(snaps), lineage)


def main(argv: list[str] | None = None) -> int:
    configure_output()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--mode", default="research", choices=[m.value for m in RunMode])
    p.add_argument("--audit-samples", type=int, default=300)
    a = p.parse_args(argv)
    try:
        res = build_features(Path(a.root), a.mode, a.audit_samples)
    except (BuildError, RuntimeError, ValueError) as e:
        print(f"FEATURE BUILD FAILED: {e}", file=sys.stderr)
        return 2
    print(
        f"{res.n_snapshots} snapshots -> {res.path} (data_version={res.lineage['data_version']}, "
        f"audit clean, {a.audit_samples} samples)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
