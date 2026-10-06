"""Model / artifact / version registry (ADR 0032). Built from what is actually on disk and in code --
never hand-maintained -- and written to artifacts/registry/registry.json with a content hash; each
change is appended to registry_history.jsonl.

Versions tracked: data version (`dv-...`), feature version + registry hash + parquet checksum, model id
and version, LLM provider/model + prompt id/version/schema version, calibration method + the
temperatures fitted for each LLM model, and every artifact directory.
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from src.versioning import canonical_json


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _models() -> list[dict]:
    from src.models import REGISTRY

    return [
        {"model_id": k, "model_version": v.model_version, "model_class": v.model_class}
        for k, v in sorted(REGISTRY.items())
    ]


def _llm(root: Path) -> dict:
    from src.llm.contract import SCHEMA_VERSION
    from src.llm.prompt import PROMPT_ID, PROMPT_VERSION, system_prompt_hash

    seen: dict[str, dict] = {}
    files = list(root.glob("artifacts/llm_runs/*/calls.jsonl")) + list(
        root.glob("artifacts/snapshots/*/*/llm_calls.jsonl")
    )
    for p in files:
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                c = json.loads(line)
                e = seen.setdefault(
                    f"{c['provider']}:{c['model']}",
                    {
                        "provider": c["provider"],
                        "model": c["model"],
                        "model_class": "LLM_REAL",
                        "calls": 0,
                        "prompt_versions": set(),
                    },
                )
                e["calls"] += 1
                e["prompt_versions"].add(c["prompt_version"])
    temps = {}
    for p in root.glob("artifacts/llm_runs/*/evaluation.json"):
        ev = json.loads(p.read_text(encoding="utf-8"))
        for model_id, cal in ev.get("calibration", {}).items():
            if not cal.get("skipped"):
                temps[model_id] = {
                    "temperature": cal["temperature"],
                    "fit_rows": cal["calibration_fit_rows"],
                    "run": p.parent.name,
                }
    for e in seen.values():
        e["prompt_versions"] = sorted(e["prompt_versions"])
    return {
        "prompt": {
            "prompt_id": PROMPT_ID,
            "prompt_version": PROMPT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "system_prompt_hash": system_prompt_hash(),
        },
        "models": [seen[k] for k in sorted(seen)],
        "calibration": {"method": "temperature_scaling (S9, ADR 0020)", "fitted": temps},
    }


def _features(root: Path) -> list[dict]:
    out = []
    for lin in sorted(root.glob("data/features/*/*/feature_lineage.json")):
        d = json.loads(lin.read_text(encoding="utf-8"))
        out.append(
            {k: d.get(k) for k in ("data_version", "feature_version", "registry_hash", "parquet_sha256")}
        )
    return out


def _artifacts(root: Path) -> list[dict]:
    out = []
    base = root / "artifacts"
    if not base.exists():
        return out
    for kind_dir in sorted(p for p in base.iterdir() if p.is_dir() and p.name not in ("registry", "ops")):
        for d in sorted(p for p in kind_dir.iterdir() if p.is_dir() and not p.name.startswith(".")):
            files = sorted(f for f in d.rglob("*") if f.is_file())
            layout = "".join(f"{f.relative_to(d)}:{f.stat().st_size}" for f in files)
            dv = next((part for part in d.name.split("_") if part.startswith("dv-")), None)
            out.append(
                {
                    "kind": kind_dir.name,
                    "name": d.name,
                    "files": len(files),
                    "layout_hash": hashlib.sha256(layout.encode()).hexdigest()[:16],
                    "data_version": dv,
                }
            )
    return out


def build_registry(root: Path) -> dict:
    root = Path(root)
    body = {
        "data": {
            "current": _read_json(root / "data" / "processed" / "CURRENT.json"),
            "versions": sorted(p.name for p in (root / "data" / "processed").glob("dv-*")),
        },
        "features": _features(root),
        "models": _models(),
        "llm": _llm(root),
        "artifacts": _artifacts(root),
    }
    body["registry_hash"] = hashlib.sha256(canonical_json(body).encode()).hexdigest()
    return body


def write_registry(root: Path, now: datetime | None = None) -> tuple[Path, bool]:
    """Writes registry.json; returns (path, changed). A change is appended to the history."""
    root = Path(root)
    reg = build_registry(root)
    d = root / "artifacts" / "registry"
    d.mkdir(parents=True, exist_ok=True)
    path = d / "registry.json"
    old = _read_json(path)
    changed = old is None or old.get("registry_hash") != reg["registry_hash"]
    if changed:
        path.write_text(json.dumps(reg, indent=2, sort_keys=True, default=str), encoding="utf-8")
        entry = {"ts": (now or datetime.now(UTC)).isoformat(), "registry_hash": reg["registry_hash"]}
        with (d / "registry_history.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    return path, changed
