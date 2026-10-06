"""Retraining gate (ADR 0032). NOTHING in this repo retrains a model automatically. This module only
answers "would retraining be allowed now?" with the reasons, so a human decision is based on facts:

1. versioned data: the current `data_version` differs from the one the last walk-forward / ensemble
   artifacts were built on (otherwise there is nothing new to learn from);
2. validation exists: a walk-forward report for the CURRENT data/feature version;
3. audit: the feature artifact lineage is present and LLM artifacts pass the leakage audit;
4. approval: an explicit `artifacts/registry/retrain_approval.json` naming that data_version and an
   approver. All four must hold; `eligible` never triggers anything.
"""

import json
from pathlib import Path


def evaluate_retrain(root: Path, feature_version: str) -> dict:
    root = Path(root)
    checks = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail})

    cur_path = root / "data" / "processed" / "CURRENT.json"
    current = json.loads(cur_path.read_text(encoding="utf-8"))["data_version"] if cur_path.exists() else None
    add("current_data_version", current is not None, str(current))
    wf = sorted(root.glob("artifacts/walk_forward/*"))
    trained_on = {p.name.split("_")[0] for p in wf}
    add(
        "new_data_version",
        current is not None and current not in trained_on,
        f"models were last evaluated on {sorted(trained_on) or 'nothing'}; current is {current}",
    )
    add(
        "validation_report_for_current",
        any(p.name.startswith(f"{current}_{feature_version}") for p in wf),
        "walk-forward report for the current data/feature version",
    )
    lineage = root / "data" / "features" / str(current) / feature_version / "feature_lineage.json"
    add("feature_artifact_lineage", lineage.exists(), "feature_lineage.json present")
    try:
        from src.llm.audit import main as audit_main

        leak_ok = audit_main(["--root", str(root)]) == 0
        detail = "src.llm.audit over artifacts/llm_runs"
    except Exception as e:  # noqa: BLE001 - reported as a failed check, never hidden
        leak_ok, detail = False, f"audit could not run: {type(e).__name__}"
    add("llm_leakage_audit", leak_ok, detail)
    approval = root / "artifacts" / "registry" / "retrain_approval.json"
    approved = False
    if approval.exists():
        a = json.loads(approval.read_text(encoding="utf-8"))
        approved = a.get("data_version") == current and bool(a.get("approved_by"))
    add("human_approval", approved, "retrain_approval.json naming this data_version and an approver")
    return {"eligible": all(c["ok"] for c in checks), "checks": checks, "automatic_retraining": False}
