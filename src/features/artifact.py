"""Feature artifact I/O with lineage verification: stale or foreign artifacts fail loudly."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import duckdb

from src.data.dataset import DatasetRef

from .registry import registry_hash


class StaleArtifactError(RuntimeError):
    """The feature artifact does not belong to the current data/feature/registry version."""


@dataclass(frozen=True)
class FeatureTable:
    rows: dict[str, dict[str, float | None]]  # fixture_id -> feature values
    reasons: dict[str, dict[str, str]]  # fixture_id -> feature -> reason
    lineage: dict


def artifact_dir(root: Path, data_version: str, feature_version: str) -> Path:
    return root / "data" / "features" / data_version / feature_version


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_features(root: Path, ref: DatasetRef, feature_version: str) -> FeatureTable:
    d = artifact_dir(root, ref.data_version, feature_version)
    lin_path, parquet = d / "feature_lineage.json", d / "features.parquet"
    if not lin_path.exists() or not parquet.exists():
        raise StaleArtifactError(
            f"no feature artifact for {ref.data_version}/{feature_version} at {d}; "
            "run: python -m src.features.builder"
        )
    lin = json.loads(lin_path.read_text(encoding="utf-8"))
    problems = []
    if lin["data_version"] != ref.data_version:
        problems.append(f"data_version {lin['data_version']} != {ref.data_version}")
    if lin["dataset_content_hash"] != ref.meta["content_hash"]:
        problems.append("dataset content hash differs from the one the features were built on")
    if lin["feature_version"] != feature_version:
        problems.append(f"feature_version {lin['feature_version']} != {feature_version}")
    if lin["registry_hash"] != registry_hash():
        problems.append("feature registry changed without a feature_version bump")
    if lin["parquet_sha256"] != file_sha256(parquet):
        problems.append("features.parquet does not match its recorded checksum (corrupted/edited)")
    if problems:
        raise StaleArtifactError("stale/invalid feature artifact: " + "; ".join(problems))

    con = duckdb.connect()
    cur = con.execute(f"SELECT * FROM read_parquet('{parquet.as_posix()}')")
    names = [c[0] for c in cur.description]
    rows: dict[str, dict[str, float | None]] = {}
    reasons: dict[str, dict[str, str]] = {}
    for rec in cur.fetchall():
        d_ = dict(zip(names, rec, strict=True))
        fid = d_.pop("fixture_id")
        d_.pop("information_cutoff_utc")
        reasons[fid] = json.loads(d_.pop("unavailable_reasons") or "{}")
        rows[fid] = d_
    con.close()
    if len(rows) != lin["rows"]:
        raise StaleArtifactError(f"row count {len(rows)} != lineage rows {lin['rows']}")
    return FeatureTable(rows, reasons, lin)
