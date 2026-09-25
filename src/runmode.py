"""Scientific run modes and the policy each one enforces (see docs/adr/0012 and versioning.md)."""

from dataclasses import dataclass
from enum import StrEnum


class RunMode(StrEnum):
    DEVELOPMENT = "development"  # fast, local, flexible
    RESEARCH = "research"  # reproducible + fully logged
    STRICT = "strict"  # every provenance and quality rule enforced
    FINAL = "final"  # STRICT + the only mode that may unlock final-test data


@dataclass(frozen=True)
class ModePolicy:
    require_real_git_sha: bool  # 'unknown' SHA forbidden
    require_clean_git: bool  # dirty working tree forbidden (otherwise dirty=true is recorded)
    require_complete_provenance: bool  # experiment provenance fields must all be present
    fail_on_quality_errors: bool
    fail_on_quality_warnings: bool
    require_checksums_known: bool  # raw files must have a pinned/verified checksum
    allow_final_access: bool
    tolerate_unexpected_missing_features: bool


POLICIES: dict[RunMode, ModePolicy] = {
    RunMode.DEVELOPMENT: ModePolicy(False, False, False, False, False, False, False, True),
    RunMode.RESEARCH: ModePolicy(True, False, True, True, False, False, False, True),
    RunMode.STRICT: ModePolicy(True, True, True, True, True, True, False, False),
    RunMode.FINAL: ModePolicy(True, True, True, True, True, True, True, False),
}


def policy(mode: RunMode | str) -> ModePolicy:
    return POLICIES[RunMode(mode)]
