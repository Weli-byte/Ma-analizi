"""Content-derived data version: dv-<12 hex> (ADR 0003)."""

import hashlib
from pathlib import Path

from src.config import LeaguesConfig
from src.versioning import canonical_json

from .manifest import PARSER_VERSION, RawFile

SCHEMA_VERSION = "tables-2"  # bump when the processed table layout changes


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def normalization_inputs_hash(leagues: LeaguesConfig, league_ids: list[str], alias_file: Path) -> str:
    """Everything besides the raw bytes that changes the normalized output."""
    payload = {
        "leagues": {k: leagues.leagues[k].model_dump() for k in sorted(league_ids)},
        "aliases": file_sha256(alias_file),
        "parser": PARSER_VERSION,
        "schema": SCHEMA_VERSION,
    }
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def compute_data_version(entries: tuple[RawFile, ...] | list[RawFile], norm_hash: str) -> str:
    """Changes iff any raw file changes (or normalization inputs change)."""
    raw = sorted((e.league, e.season, e.sha256) for e in entries)
    digest = hashlib.sha256(canonical_json({"raw": raw, "norm": norm_hash}).encode()).hexdigest()
    return f"dv-{digest[:12]}"
