"""Sprint 1 data audit command (ADR 0063).

    python -m src.league_models.audit --as-of 2026-10-20T00:00:00Z            # print the summary only
    python -m src.league_models.audit --as-of 2026-10-20T00:00:00Z --write   # write docs/league_models/

Reads the cached public-domain source files, writes nothing outside docs/league_models and
artifacts/league_models, and never calls a network API. The result is deterministic for the same cached
bytes and the same --as-of."""

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from src.data.teams import TeamDirectory

from .asof import temporal_audit
from .competitions import ROOT, load_config
from .coverage import (
    AuditResult,
    collect,
    coverage_dicts,
    history_matches,
    universe_summary,
)
from .identity import collision_audit, mapping_hash, upsert_mapping
from .licenses import PlanStatus, evidence_exists, gate_reasons, load_manifest, manifest_hash

PURPOSES = ("production_ml", "research_ml", "display")


def dumps(obj) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n"


def license_report(manifest: dict, root: Path) -> dict:
    sources = {}
    for pid, lic in sorted(manifest.items()):
        sources[pid] = {
            **lic.model_dump(mode="json"),
            "evidence_file_exists": evidence_exists(lic, root),
            "gate": {p: (gate_reasons(lic, p) or "ALLOWED") for p in PURPOSES},
        }
    statuses: dict[PlanStatus, list[str]] = {}
    for pid, lic in sorted(manifest.items()):
        statuses.setdefault(lic.plan_status, []).append(pid)
    return {
        "manifest_sha256": manifest_hash(manifest),
        "not_legal_advice": True,
        "by_plan_status": statuses,
        "sources": sources,
    }


def identity_summary(result: AuditResult, mapping_file: str) -> dict:
    rows = result.identity_rows
    per_comp = Counter(r.canonical_league_id for r in rows)
    names = Counter((u.canonical_league_id, u.raw_name) for u in result.unresolved)
    return {
        "mapping_sha256": mapping_hash(rows),
        "rows": len(rows),
        "rows_per_competition": dict(sorted(per_comp.items())),
        "collisions": collision_audit(rows),
        "unresolved_club_mentions": len(result.unresolved),
        "unresolved_top_names": [
            {"competition": c, "raw_name": n, "mentions": k} for (c, n), k in names.most_common(25)
        ],
        "mapping_file": mapping_file,
        "migration": "NOT_APPLICABLE: no fixtures database exists (the only database is the Turso user "
        "store); this is an additive, idempotent mapping layer with a dry-run",
    }


def snapshot_manifest(result: AuditResult, mapping_sha: str) -> dict:
    files = [
        {
            "competition": f.competition,
            "season": f.season,
            "provider_id": f.provider_id,
            "path": f.path,
            "sha256": f.sha256,
            "bytes": f.bytes,
        }
        for f in sorted(result.source_files, key=lambda x: (x.competition, x.season))
    ]
    digest = hashlib.sha256("".join(f["sha256"] for f in files).encode()).hexdigest()
    return {
        "as_of": result.as_of.isoformat(),
        "data_snapshot_sha256": digest,
        "identity_mapping_sha256": mapping_sha,
        "files": files,
        "excluded_not_opened": result.excluded_files,
        "mutability_warning": "the source files are fetched from public GitHub repositories whose "
        "current-season files change; this manifest pins the exact bytes (sha256). Archive them before "
        "Sprint 2 if exact reproduction matters.",
    }


def write_csv(path: Path, rows: list[dict]) -> None:
    cols = list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(cols)
        for r in rows:
            w.writerow(
                [
                    "; ".join(v) if isinstance(v, list) else ("" if v is None else v)
                    for v in (r[c] for c in cols)
                ]
            )


def run(root: Path, as_of: datetime, out_dir: Path, write: bool) -> dict:
    cfg = load_config()
    manifest = load_manifest()
    directory = TeamDirectory.load(root / "configs" / "team_aliases.yaml")
    result = collect(root, cfg, directory, manifest, as_of)
    hist = history_matches(result.identity_rows, cfg.result_lag_hours)
    audit = temporal_audit(hist, brute_every=max(1, len(hist) // 300))
    mapping_path = root / "artifacts" / "league_models" / "identity_mapping.jsonl"
    ident = identity_summary(result, mapping_path.relative_to(root).as_posix())
    upsert = upsert_mapping(mapping_path, result.identity_rows, apply=write)
    universe = universe_summary(result.coverage)
    status_counts = Counter(c.training_status for c in result.coverage)
    summary = {
        "as_of": as_of.isoformat(),
        "seasons_audited": len(result.coverage),
        "training_status_counts": dict(sorted(status_counts.items())),
        "temporal_audit": audit.__dict__,
        "identity_mapping_sha256": ident["mapping_sha256"],
        "identity_mapping_upsert": upsert,
        "collisions_empty": not any(ident["collisions"].values()),
        "license_manifest_sha256": manifest_hash(manifest),
    }
    if write:
        out_dir.mkdir(parents=True, exist_ok=True)
        cov = coverage_dicts(result.coverage)
        write_csv(out_dir / "league_coverage.csv", cov)
        files = {
            "league_coverage.json": cov,
            "data_source_license_manifest.json": license_report(manifest, root),
            "canonical_identity_summary.json": ident,
            "snapshot_manifest.json": snapshot_manifest(result, ident["mapping_sha256"]),
            "temporal_audit.json": {**audit.__dict__, "stages": ["t-24h", "t-90m", "t-30m", "kickoff"]},
            "quality_report.json": result.quality,
            "training_universe.json": universe,
        }
        for name, obj in files.items():
            (out_dir / name).write_text(dumps(obj), encoding="utf-8")
    return {"summary": summary, "universe": universe}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--as-of", default=None, help="audit time, ISO UTC (default: now)")
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--out", default="docs/league_models")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    as_of = datetime.fromisoformat(a.as_of.replace("Z", "+00:00")) if a.as_of else datetime.now(UTC)
    if as_of.tzinfo is None:
        sys.exit("--as-of must carry a UTC offset or Z")
    root = Path(a.root)
    out = run(root, as_of.astimezone(UTC), root / a.out, a.write)
    sys.stdout.write(dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
