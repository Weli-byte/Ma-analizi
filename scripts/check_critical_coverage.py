"""Coverage gate for the critical scientific paths (not a vanity 100% target).

    pytest --cov=src --cov-report=json:coverage.json && python scripts/check_critical_coverage.py

Fails when overall coverage or any critical-path group falls below its threshold.
"""

import json
import sys
from pathlib import Path

OVERALL_MIN = 90.0
GROUP_MIN = 90.0
CRITICAL = {
    "data ingestion": ["src/data/download.py", "src/data/raw_validation.py", "src/data/manifest.py"],
    "normalization": ["src/data/clean.py", "src/data/teams.py", "src/data/timezones.py"],
    "feature generation": [
        "src/features/compute.py",
        "src/features/history.py",
        "src/features/registry.py",
        "src/features/builder.py",
    ],
    "split logic": ["src/evaluation/split.py"],
    "leakage guard": ["src/evaluation/context.py", "src/features/leakage_audit.py"],
    "evaluation": ["src/evaluation/metrics.py", "src/evaluation/runner.py", "src/evaluation/dataset.py"],
    "prediction id / lifecycle": ["src/schemas/prediction.py", "src/schemas/lifecycle.py"],
    "lineage / provenance": [
        "src/features/artifact.py",
        "src/data/versioning.py",
        "src/provenance.py",
        "src/data/dataset.py",
    ],
    "quality checks": ["src/data/quality.py"],
}


def main(path: str) -> int:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    files = {k.replace("\\", "/"): v["summary"] for k, v in data["files"].items()}
    failures: list[str] = []
    overall = data["totals"]["percent_covered"]
    print(f"overall coverage: {overall:.1f}% (min {OVERALL_MIN}%)")
    if overall < OVERALL_MIN:
        failures.append(f"overall {overall:.1f}% < {OVERALL_MIN}%")
    for group, members in CRITICAL.items():
        stmts = covered = 0
        for m in members:
            if m not in files:
                failures.append(f"{group}: {m} missing from the coverage report")
                continue
            stmts += files[m]["num_statements"]
            covered += files[m]["covered_lines"]
        pct = 100.0 * covered / stmts if stmts else 0.0
        flag = "ok " if pct >= GROUP_MIN else "LOW"
        print(f"  [{flag}] {group:<28} {pct:5.1f}%  ({', '.join(Path(m).name for m in members)})")
        if pct < GROUP_MIN:
            failures.append(f"{group} {pct:.1f}% < {GROUP_MIN}%")
    if failures:
        print("COVERAGE GATE FAILED:\n  " + "\n  ".join(failures))
        return 1
    print("coverage gate passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "coverage.json"))
