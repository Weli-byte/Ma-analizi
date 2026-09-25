"""Locate the current processed dataset through an atomically-updated pointer (ADR 0003)."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

import duckdb

POINTER = "CURRENT.json"


class DatasetError(RuntimeError):
    pass


@dataclass(frozen=True)
class DatasetRef:
    data_version: str
    path: Path
    meta: dict

    @property
    def db_path(self) -> Path:
        return self.path / "football.duckdb"


def write_pointer(processed_dir: Path, data_version: str, content_hash: str) -> None:
    tmp = processed_dir / f".{POINTER}.tmp"
    tmp.write_text(
        json.dumps({"data_version": data_version, "content_hash": content_hash}, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, processed_dir / POINTER)  # atomic on POSIX and Windows


def resolve_dataset(processed_dir: Path, data_version: str | None = None) -> DatasetRef:
    """Resolve the dataset to use. With no version, follow CURRENT.json. Fails loudly."""
    if data_version is None:
        pointer = processed_dir / POINTER
        if not pointer.exists():
            raise DatasetError(f"no dataset pointer at {pointer}; run: python -m src.data.pipeline")
        data_version = json.loads(pointer.read_text(encoding="utf-8"))["data_version"]
    path = processed_dir / data_version
    meta_path = path / "dataset_meta.json"
    if not meta_path.exists():
        raise DatasetError(f"dataset {data_version} not found or incomplete at {path}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta["data_version"] != data_version:
        raise DatasetError(f"dataset meta version {meta['data_version']} != directory {data_version}")
    return DatasetRef(data_version, path, meta)


def open_db(path: Path, read_only: bool = True):
    """Open a dataset DuckDB with the session timezone pinned to UTC.

    Without this, TIMESTAMPTZ values come back in the machine's local timezone."""
    con = duckdb.connect(str(path), read_only=read_only)
    con.execute("SET TimeZone='UTC'")
    return con
