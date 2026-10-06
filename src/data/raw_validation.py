"""Raw file validation: catches HTML pages, empty/truncated files, wrong schema, bad encoding."""

import csv
import hashlib
import io
from dataclasses import dataclass, field

from src.config import LeagueFormat

REQUIRED_COLUMNS = ("Div", "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR")


class RawFileError(ValueError):
    """A raw file is unusable (corrupt, wrong type, wrong schema)."""


@dataclass(frozen=True)
class RawFileReport:
    filename: str
    size_bytes: int
    encoding: str
    columns: tuple[str, ...]
    schema_hash: str
    n_rows: int
    warnings: tuple[str, ...] = field(default_factory=tuple)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def schema_hash(columns: tuple[str, ...] | list[str]) -> str:
    return sha256_bytes("|".join(columns).encode("utf-8"))[:16]


def decode(raw: bytes) -> tuple[str, str]:
    try:
        return raw.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("latin-1"), "latin-1"


def validate_raw_bytes(name: str, raw: bytes, league: LeagueFormat) -> RawFileReport:
    """Validate file content BEFORE it is trusted. Raises RawFileError on any corruption."""
    if len(raw) == 0:
        raise RawFileError(f"{name}: file is empty (0 bytes)")
    head = raw[:2048].lstrip().lower()
    if head.startswith((b"<!doctype", b"<html", b"<?xml")) or b"<html" in head:
        raise RawFileError(f"{name}: content is HTML, not CSV (blocked/error page?)")
    text, encoding = decode(raw)
    warnings: list[str] = []
    if encoding != "utf-8":
        warnings.append("file is not UTF-8; decoded as latin-1")
    rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    if not rows:
        raise RawFileError(f"{name}: no CSV rows")
    header = tuple(c.strip() for c in rows[0])
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        raise RawFileError(f"{name}: invalid header, missing columns {missing}")
    data = rows[1:]
    if not data:
        raise RawFileError(f"{name}: header only, no data rows")
    width = len(header)
    bad = [i + 2 for i, r in enumerate(data) if len(r) != width]
    if bad:
        kind = "truncated" if len(bad) == 1 and bad[0] == len(data) + 1 else "malformed"
        raise RawFileError(f"{name}: {kind} CSV, rows with wrong field count: lines {bad[:5]}")
    div_idx = header.index("Div")
    divs = {r[div_idx].strip() for r in data}
    if divs != {league.source_code}:
        raise RawFileError(f"{name}: Div values {sorted(divs)} != expected {league.source_code!r}")
    return RawFileReport(name, len(raw), encoding, header, schema_hash(header), len(data), tuple(warnings))
