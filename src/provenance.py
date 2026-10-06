"""Run provenance: git SHA/dirty state, Python/platform, dependency-lock hash."""

import hashlib
import platform
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .runmode import RunMode, policy

ROOT = Path(__file__).resolve().parents[1]


class ProvenanceError(RuntimeError):
    """Raised when a run mode requires provenance that is not available."""


@dataclass(frozen=True)
class GitInfo:
    sha: str  # 40 hex chars, or "unknown" (only acceptable in DEVELOPMENT)
    dirty: bool
    dirty_files: tuple[str, ...]


@dataclass(frozen=True)
class Provenance:
    git_sha: str
    git_dirty: bool
    dirty_files: tuple[str, ...]
    python_version: str
    platform: str
    dependency_lock_hash: str  # sha256 of requirements.lock, or "missing"


def _git(root: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=True, encoding="utf-8"
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout


def git_info(root: Path = ROOT) -> GitInfo:
    sha = (_git(root, "rev-parse", "HEAD") or "").strip()
    if len(sha) != 40:
        return GitInfo("unknown", True, ())
    status = _git(root, "status", "--porcelain", "--untracked-files=all") or ""
    files = tuple(sorted(line[3:].strip() for line in status.splitlines() if line.strip()))
    return GitInfo(sha, bool(files), files)


def lock_hash(root: Path = ROOT) -> str:
    lock = root / "requirements.lock"
    if not lock.exists():
        return "missing"
    return hashlib.sha256(lock.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def collect(mode: RunMode | str, root: Path = ROOT) -> Provenance:
    """Collect provenance and enforce the mode's policy. Fails loudly, never guesses."""
    mode = RunMode(mode)
    pol = policy(mode)
    g = git_info(root)
    lh = lock_hash(root)
    if pol.require_real_git_sha and g.sha == "unknown":
        raise ProvenanceError(f"{mode.value} run needs a real git commit SHA (repo has no commits?)")
    if pol.require_clean_git and g.dirty:
        raise ProvenanceError(
            f"{mode.value} run needs a clean working tree; changed files: {list(g.dirty_files)[:10]}"
        )
    if pol.require_complete_provenance and lh == "missing":
        raise ProvenanceError("requirements.lock missing: dependency_lock_hash is required")
    return Provenance(g.sha, g.dirty, g.dirty_files, sys.version.split()[0], platform.platform(), lh)
