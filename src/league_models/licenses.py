"""Licence manifest and the fail-closed training gate (Sprint 1, plan tasks 1 and 6).

Rules: every right is explicit (ALLOWED / DENIED / UNKNOWN); UNKNOWN is never permission. Production
training needs every relevant right ALLOWED and plan_status PERMITTED; offline research needs only a
status other than BLOCKED. An unknown provider id is an error (fail closed). Not legal advice."""

import hashlib
import json
from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "configs" / "league_models" / "source_licenses.yaml"


class Right(StrEnum):
    ALLOWED = "ALLOWED"
    DENIED = "DENIED"
    UNKNOWN = "UNKNOWN"


PlanStatus = Literal["PERMITTED", "CONDITIONAL", "RESEARCH_ONLY", "BLOCKED"]


class SourceLicense(BaseModel):
    """One data source. Every field is required: a missing field is a loading error, never a silent
    UNKNOWN."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_id: str = Field(min_length=1)
    description: str
    source_url: str
    retrieved_at_utc: str
    terms_version: str
    license_evidence_path: str
    plan_status: PlanStatus
    commercial_display: Right
    commercial_ml: Right
    derivative_predictions: Right
    indefinite_retention: Right
    redistribution: Right
    branding: str
    attribution: str
    geographic_restrictions: str
    cancellation_effect: str
    legal_review_required: bool
    supplies: list[str]
    history_seasons_note: str | None = None


class LicenseGateError(RuntimeError):
    """Raised when a source may not feed the requested purpose. The message lists every offending source
    and why."""


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, SourceLicense]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    out: dict[str, SourceLicense] = {}
    for item in raw["sources"]:
        lic = SourceLicense(**item)
        if lic.provider_id in out:
            raise ValueError(f"duplicate provider_id in the licence manifest: {lic.provider_id}")
        out[lic.provider_id] = lic
    return out


def manifest_hash(manifest: dict[str, SourceLicense]) -> str:
    payload = json.dumps({k: v.model_dump(mode="json") for k, v in sorted(manifest.items())}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def evidence_exists(lic: SourceLicense, root: Path = ROOT) -> bool:
    return (root / lic.license_evidence_path).is_file()


def gate_reasons(lic: SourceLicense, purpose: str) -> list[str]:
    """Why `lic` may NOT feed `purpose` (empty list = allowed). Purposes: production_ml, research_ml,
    display."""
    if purpose == "production_ml":
        reasons = []
        if lic.plan_status != "PERMITTED":
            reasons.append(f"plan_status is {lic.plan_status}, not PERMITTED")
        for right in ("commercial_ml", "derivative_predictions", "indefinite_retention"):
            value = Right(getattr(lic, right))  # also accepts a plain string from a hand-built manifest
            if value is not Right.ALLOWED:
                reasons.append(f"{right} is {value.value}")
        return reasons
    if purpose == "research_ml":
        return ["plan_status is BLOCKED"] if lic.plan_status == "BLOCKED" else []
    if purpose == "display":
        value = Right(lic.commercial_display)
        return [] if value is Right.ALLOWED else [f"commercial_display is {value.value}"]
    raise ValueError(f"unknown purpose: {purpose!r}")


def training_gate(manifest: dict[str, SourceLicense], provider_ids: list[str], purpose: str) -> None:
    """Fail closed: raise `LicenseGateError` unless EVERY listed provider may feed `purpose`."""
    problems = []
    for pid in provider_ids:
        lic = manifest.get(pid)
        if lic is None:
            problems.append(f"{pid}: not in the licence manifest (unknown source = blocked)")
            continue
        problems.extend(f"{pid}: {r}" for r in gate_reasons(lic, purpose))
    if problems:
        raise LicenseGateError(f"{purpose} refused: " + "; ".join(problems))
