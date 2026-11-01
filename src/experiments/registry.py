"""Pre-registered experiments (ADR 0096).

An experiment is a spec (hypothesis, track, market, primary and secondary metrics, minimal practical effect, window, baselines, candidate, decision rule, data requirements). `register` writes
`docs/phase3/experiments/<id>/spec.json` with the spec's SHA-256 BEFORE anything is computed. `record_result` refuses (a) a spec whose file no longer matches its registered hash, (b) an
experiment that already has a result — re-running until a better number appears is blocked; a changed protocol is a NEW experiment id.
"""

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from src.versioning import canonical_json

REPO = Path(__file__).resolve().parents[2]
BASE = REPO / "docs" / "phase3" / "experiments"
TRACKS = ("RETROSPECTIVE_OOF", "PROSPECTIVE_LIVE", "STORED_EVIDENCE", "NOT_RUN")


@dataclass(frozen=True)
class Spec:
    id: str
    hypothesis: str
    track: str
    market: str
    candidate: str
    baselines: tuple[str, ...]
    primary_metric: str = "log_loss"
    secondary_metrics: tuple[str, ...] = ("brier", "ece", "accuracy")
    min_practical_effect: float = 0.002          # log-loss improvement the candidate must beat with its whole interval
    window: str = ""
    stage: str = "last forecast before kick-off"
    leagues: str = "the six domestic forecast leagues"
    decision_rule: str = "promotion candidate only if the Bonferroni interval of candidate minus champion is wholly below -min_practical_effect and every promotion-gate check passes"
    data_requirements: tuple[str, ...] = field(default_factory=tuple)

    def sha256(self) -> str:
        return hashlib.sha256(canonical_json(asdict(self)).encode()).hexdigest()


class RegistryError(RuntimeError):
    pass


def folder(spec_id: str, base: Path = BASE) -> Path:
    return Path(base) / spec_id


def register(spec: Spec, base: Path = BASE, now: datetime | None = None) -> str:
    if spec.track not in TRACKS:
        raise RegistryError(f"unknown track {spec.track!r}")
    d = folder(spec.id, base)
    p = d / "spec.json"
    body = {"spec": asdict(spec), "spec_sha256": spec.sha256()}
    if p.exists():
        old = json.loads(p.read_text(encoding="utf-8"))
        if old["spec_sha256"] != body["spec_sha256"]:
            raise RegistryError(f"{spec.id}: the registered spec differs; a changed protocol needs a new experiment id")
        return "ALREADY_REGISTERED"
    d.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({**body, "registered_at": (now or datetime.now(UTC)).isoformat()}, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return "REGISTERED"


def record_result(spec: Spec, result: dict, base: Path = BASE, now: datetime | None = None) -> str:
    d = folder(spec.id, base)
    p = d / "spec.json"
    if not p.exists():
        raise RegistryError(f"{spec.id}: not registered; register the spec before computing anything")
    reg = json.loads(p.read_text(encoding="utf-8"))
    if reg["spec_sha256"] != spec.sha256() or reg["spec_sha256"] != hashlib.sha256(canonical_json(reg["spec"]).encode()).hexdigest():
        raise RegistryError(f"{spec.id}: the spec file no longer matches its registered hash")
    r = d / "result.json"
    if r.exists():
        raise RegistryError(f"{spec.id}: already has a result; re-running to pick a better one is not allowed (new protocol = new id)")
    body = {"spec_sha256": reg["spec_sha256"], "recorded_at": (now or datetime.now(UTC)).isoformat(), **result}
    body["result_sha256"] = hashlib.sha256(canonical_json({k: v for k, v in body.items() if k != "recorded_at"}).encode()).hexdigest()
    r.write_text(json.dumps(body, indent=1, sort_keys=True, default=float) + "\n", encoding="utf-8", newline="\n")
    return "RECORDED"


def load_all(base: Path = BASE) -> list[dict]:
    out = []
    for d in sorted(p for p in Path(base).glob("*") if p.is_dir()):
        spec = json.loads((d / "spec.json").read_text(encoding="utf-8")) if (d / "spec.json").exists() else None
        res = json.loads((d / "result.json").read_text(encoding="utf-8")) if (d / "result.json").exists() else None
        if spec:
            out.append({"id": d.name, "spec": spec, "result": res})
    return out
