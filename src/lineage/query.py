"""Lineage of one fixture's published forecasts (ADR 0091): every stored version, oldest first, with what produced it.

Each version reports its content hash and whether it still verifies (recomputed), the information cutoff, the model / data / feature / config versions, the stage, the producing commit and the
odds source (bookmaker, observation time, quality — never the prices). Versions written before the lineage block existed say `legacy_unknown` for the fields only the block carries; nothing
is back-filled. No secret, no raw provider payload and no user data is returned.
"""

import json
from datetime import datetime
from pathlib import Path

from src.prospective.audit import recompute_hash

from .record import stage_of

LEGACY = "legacy_unknown"


def version_view(body: dict, file_name: str) -> dict:
    lin = body.get("lineage")
    kickoff, cutoff = datetime.fromisoformat(body["kickoff_utc"]), datetime.fromisoformat(body["information_cutoff"])
    src = body.get("market_source") or None
    flags = (body.get("intelligence", {}).get("data_quality") or {}).get("flags", [])
    return {
        "file": file_name, "content_hash": body.get("content_hash"), "hash_verified": recompute_hash(body) == body.get("content_hash"),
        "information_cutoff": body["information_cutoff"], "generated_at": body.get("generated_at"), "pre_kickoff": cutoff < kickoff,
        "model_version": body.get("model_version"), "data_version": body.get("data_version"), "feature_version": body.get("feature_version"), "config_hash": body.get("config_hash"),
        "stage": lin["stage"] if lin else f"{stage_of(kickoff, cutoff)[0]} (derived; {LEGACY} lineage)",
        "code_commit": lin["code_commit"] if lin else LEGACY, "calibration": lin["calibration"] if lin else LEGACY,
        "history": lin["history"] if lin else {"data_version": body.get("data_version"), "matches": (body.get("intelligence", {}).get("fitted_on") or {}).get("matches")},
        "odds": ({"bookmaker": src.get("bookmaker"), "observed_at": src.get("observed_at"), "quality": src.get("quality")} if src else None),
        "data_quality_flags": flags, "lineage_schema": lin["schema"] if lin else LEGACY,
    }  # fmt: skip


def lineage_for(root, fixture_key: str) -> dict | None:
    d = Path(root) / "artifacts" / "markets" / fixture_key
    files = sorted(d.glob("intel-*.json")) if d.is_dir() else []
    if not files:
        return None
    versions = [version_view(json.loads(f.read_text(encoding="utf-8")), f.name) for f in files]
    return {"fixture_id": fixture_key, "versions": versions, "n_versions": len(versions), "all_hashes_verified": all(v["hash_verified"] for v in versions),
            "with_lineage_block": sum(1 for v in versions if v["lineage_schema"] != LEGACY)}  # fmt: skip
