"""S10 (ADR 0021): scan PERSISTED `predictions.jsonl`/`calls.jsonl` artifacts for the leakage
classes the sprint names, as defense in depth beyond what the schema validators already forbid
at CONSTRUCTION time. Most individual checks here can only ever fire on a hand-edited or
corrupted file -- a file produced by this repo's own writers (`dump_records`,
`LLMCallRecord.model_dump_json`) already satisfies every one of them by construction (that is
what Phase 25-era contract tests and S8/S10's own unit tests already prove). This module exists
to catch tampering/corruption AFTER the fact and give one command that reports everything
together, plus a critical-leakage exclusion count -- "nothing crashed while loading" is not the
same guarantee as "this was actually audited."

    python -m src.llm.audit --root DIR

Scans every `artifacts/llm_runs/*/predictions.jsonl` + `calls.jsonl` pair under `--root`.
"""

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from src.cli_utils import configure_output
from src.schemas import PredictionRecord
from src.schemas.llm import LLMCallRecord

ROOT = Path(__file__).resolve().parents[2]

# Which Violation `kind`s are severe enough to exclude a fixture's prediction from a benchmark
# result ("kritik leakage kayitlarini benchmark sonucundan cikar ve sayisini raporla").
CRITICAL_KINDS = frozenset({
    "generated_at_after_kickoff", "cutoff_after_kickoff", "content_tampered",
    "missing_timestamp", "invalid_json", "invalid_record",
})  # fmt: skip


@dataclass(frozen=True)
class Violation:
    source: str  # "prediction" | "call"
    line_no: int
    kind: str
    detail: str
    fixture_id: str | None = None

    @property
    def critical(self) -> bool:
        return self.kind in CRITICAL_KINDS


def _classify_prediction_error(msg: str) -> str:
    if "tampered" in msg:
        return "content_tampered"
    if "cannot be generated after kickoff" in msg:
        return "generated_at_after_kickoff"
    if "must not be after kickoff" in msg:
        return "cutoff_after_kickoff"
    return "invalid_record"


def audit_predictions_jsonl(text: str) -> list[Violation]:
    violations = []
    for i, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            PredictionRecord.from_json(line)
        except ValueError as e:
            fixture_id = None
            try:
                fixture_id = json.loads(line).get("fixture_id")
            except json.JSONDecodeError:
                pass
            kind = _classify_prediction_error(str(e))
            violations.append(Violation("prediction", i, kind, str(e), fixture_id))
    return violations


def audit_calls_jsonl(text: str) -> list[Violation]:
    violations = []
    for i, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as e:
            violations.append(Violation("call", i, "invalid_json", str(e)))
            continue
        fixture_id = data.get("fixture_id")
        if not data.get("generated_at"):
            violations.append(
                Violation("call", i, "missing_timestamp", "generated_at missing/empty", fixture_id)
            )
            continue
        try:
            LLMCallRecord.model_validate(data)
        except ValidationError as e:
            violations.append(Violation("call", i, "invalid_record", str(e), fixture_id))
    return violations


def partition_clean(
    predictions: list[PredictionRecord], violations: list[Violation]
) -> tuple[list[PredictionRecord], int]:
    """Excludes any prediction whose `fixture_id` has a CRITICAL violation. Returns
    `(clean_predictions, excluded_count)` -- the count is always reported, never dropped
    silently."""
    critical_fixtures = {v.fixture_id for v in violations if v.critical and v.fixture_id}
    clean = [p for p in predictions if p.fixture_id not in critical_fixtures]
    return clean, len(predictions) - len(clean)


def audit_run_dir(run_dir: Path) -> list[Violation]:
    violations = []
    pred_file = run_dir / "predictions.jsonl"
    if pred_file.exists():
        violations += audit_predictions_jsonl(pred_file.read_text(encoding="utf-8"))
    calls_file = run_dir / "calls.jsonl"
    if calls_file.exists():
        violations += audit_calls_jsonl(calls_file.read_text(encoding="utf-8"))
    return violations


def main(argv: list[str] | None = None) -> int:
    configure_output()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default=str(ROOT))
    a = p.parse_args(argv)
    llm_runs = Path(a.root) / "artifacts" / "llm_runs"
    if not llm_runs.exists():
        print("no artifacts/llm_runs/ directory found; nothing to audit")
        return 0
    total = 0
    critical = 0
    for run_dir in sorted(llm_runs.iterdir()):
        if not run_dir.is_dir():
            continue
        violations = audit_run_dir(run_dir)
        total += len(violations)
        critical += sum(v.critical for v in violations)
        for v in violations:
            flag = "CRITICAL" if v.critical else "warning "
            loc = f"{run_dir.name}/{v.source}:{v.line_no}"
            print(f"{flag} {loc} [{v.kind}] fixture={v.fixture_id}: {v.detail}")
    print(f"\n{total} violation(s), {critical} critical")
    return 1 if critical else 0


if __name__ == "__main__":
    raise SystemExit(main())
