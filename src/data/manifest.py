"""Raw file manifest with honest provenance (origin, source URL, retrieval time, checksums)."""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from src.config import DataConfig, LeaguesConfig

from .leagues import season_from_code
from .raw_validation import RawFileError, sha256_bytes, validate_raw_bytes

PARSER_VERSION = "parser-2.0.0"
ORIGINS = ("official", "archive", "manual", "other", "unknown")


class ChecksumMismatch(RawFileError):
    """A raw file no longer matches its pinned/verified checksum (or its retrieval-time hash)."""


@dataclass(frozen=True)
class RawFile:
    dataset_id: str
    source_url: str
    origin: str  # official | archive | manual | other | unknown
    retrieved_at_utc: str | None  # None when unknown (never invented)
    sha256: str
    size_bytes: int
    season: str
    league: str
    filename: str
    schema_hash: str
    parser_version: str
    n_rows: int
    encoding: str
    expected_checksum_status: str  # verified_official | pinned_observed | unknown
    provenance_note: str = ""


@dataclass(frozen=True)
class Manifest:
    entries: tuple[RawFile, ...]
    ignored: tuple[str, ...]  # files outside the configured leagues/seasons or unparseable names
    problems: tuple[str, ...]  # 'provenance: ...' / 'checksum: ...' gaps (run mode decides)

    def to_json(self) -> str:
        return json.dumps(
            {
                "entries": [asdict(e) for e in self.entries],
                "ignored": list(self.ignored),
                "problems": list(self.problems),
            },
            indent=2,
            sort_keys=True,
        )


def parse_filename(name: str, leagues: LeaguesConfig) -> tuple[str, str]:
    """'E0_2324.csv' -> ('EPL', '2023-24')."""
    stem, _, ext = name.rpartition(".")
    code, _, season_code = stem.partition("_")
    if ext != "csv" or not code or not season_code:
        raise ValueError(f"unrecognized raw filename {name!r}; expected <DIV>_<YYSS>.csv")
    found = leagues.by_source_code(code)
    if found is None:
        raise ValueError(f"unknown league code {code!r} in {name!r}")
    return found[0], season_from_code(season_code)


def load_expected(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("files", {})


def load_sidecar(provenance_dir: Path, filename: str) -> dict | None:
    p = provenance_dir / f"{filename}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def build_manifest(
    raw_dir: Path,
    provenance_dir: Path,
    expected_path: Path,
    leagues: LeaguesConfig,
    data_cfg: DataConfig,
) -> Manifest:
    expected = load_expected(expected_path)
    entries: list[RawFile] = []
    ignored: list[str] = []
    problems: list[str] = []
    for path in sorted(raw_dir.glob("*.csv")):
        try:
            league_id, season = parse_filename(path.name, leagues)
        except ValueError as e:
            ignored.append(f"{path.name}: {e}")
            continue
        if league_id not in data_cfg.leagues or season not in data_cfg.seasons:
            ignored.append(f"{path.name}: outside configured leagues/seasons")
            continue
        raw = path.read_bytes()
        report = validate_raw_bytes(path.name, raw, leagues.leagues[league_id])  # may raise
        digest = sha256_bytes(raw)

        side = load_sidecar(provenance_dir, path.name)
        if side is None:
            origin, url, retrieved, note = "unknown", "", None, ""
            problems.append(f"provenance: {path.name}: no provenance sidecar (origin unknown)")
        else:
            origin = side["origin"]
            if origin not in ORIGINS:
                raise RawFileError(f"{path.name}: invalid origin {origin!r} in provenance sidecar")
            url = side["source_url"]
            retrieved = side.get("retrieved_at_utc")
            note = side.get("note", "")
            if side.get("sha256") and side["sha256"] != digest:
                raise ChecksumMismatch(
                    f"{path.name}: sha256 differs from the value recorded at retrieval time"
                )

        exp = expected.get(path.name)
        if exp is None:
            status = "unknown"
            problems.append(f"checksum: {path.name}: no expected checksum (status unknown)")
        else:
            if exp["sha256"] != digest:
                raise ChecksumMismatch(
                    f"{path.name}: sha256 {digest[:12]}.. != expected {exp['sha256'][:12]}.. "
                    f"(status {exp['status']})"
                )
            status = exp["status"]

        entries.append(
            RawFile(
                dataset_id=f"football_data:{league_id}:{season}",
                source_url=url,
                origin=origin,
                retrieved_at_utc=retrieved,
                sha256=digest,
                size_bytes=len(raw),
                season=season,
                league=league_id,
                filename=path.name,
                schema_hash=report.schema_hash,
                parser_version=PARSER_VERSION,
                n_rows=report.n_rows,
                encoding=report.encoding,
                expected_checksum_status=status,
                provenance_note=note,
            )
        )
    return Manifest(tuple(entries), tuple(ignored), tuple(problems))
