"""Raw file manifest: source, retrieval timestamp, checksum, league, season."""

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from .leagues import BY_SOURCE_CODE, season_from_code

SOURCE = "football-data.co.uk"
FILENAME_RE = re.compile(r"^(?P<code>[A-Z0-9]+)_(?P<season>\d{4})\.csv$")  # e.g. E0_2324.csv


@dataclass(frozen=True)
class RawFile:
    path: str  # relative to raw dir
    source: str
    league_id: str
    season: str
    checksum_sha256: str
    retrieval_time: str  # ISO UTC; first time the file was registered (or sidecar value)
    size_bytes: int


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_filename(name: str) -> tuple[str, str]:
    """'E0_2324.csv' -> ('EPL', '2023-24')."""
    m = FILENAME_RE.match(name)
    if not m:
        raise ValueError(f"unrecognized raw filename {name!r}; expected <DIV>_<YYSS>.csv")
    league = BY_SOURCE_CODE.get(m["code"])
    if league is None:
        raise ValueError(f"unknown league code {m['code']!r} in {name!r}")
    return league.league_id, season_from_code(m["season"])


def build_manifest(
    raw_dir: Path, manifest_path: Path, now: datetime | None = None
) -> list[RawFile]:
    """Scan raw_dir, keep existing entries (idempotent), add new/changed files."""
    existing: dict[str, RawFile] = {}
    if manifest_path.exists():
        for e in json.loads(manifest_path.read_text(encoding="utf-8")):
            existing[e["path"]] = RawFile(**e)
    stamp = (now or datetime.now(UTC)).isoformat()
    entries: list[RawFile] = []
    for p in sorted(raw_dir.glob("*.csv")):
        league_id, season = parse_filename(p.name)
        checksum = sha256_file(p)
        prev = existing.get(p.name)
        if prev and prev.checksum_sha256 == checksum:
            entries.append(prev)
            continue
        entries.append(
            RawFile(p.name, SOURCE, league_id, season, checksum, stamp, p.stat().st_size)
        )
    manifest_path.write_text(
        json.dumps([asdict(e) for e in entries], indent=2, sort_keys=True), encoding="utf-8"
    )
    return entries
