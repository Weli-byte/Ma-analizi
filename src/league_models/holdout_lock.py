"""Sprint 5 (ADR 0067): the one-shot lock of the frozen holdout.

The holdout scores live in the Sprint 1 identity rows but are dropped from the Sprint 2 `Universe` on purpose. This module is the ONLY door to them:

  * the access is written to an append-only log BEFORE any score is returned (a crash after the door opens still counts as consumed);
  * a second access is refused, whatever the reason; new data means a new frozen period and a new ADR, never a second read of this one;
  * the caller must present the sha256 of the pre-registration manifest (policy, configuration, candidate selection) that was written before the read;
  * the fixture set read must equal the frozen listing of the protocol manifest (ids and hash).

A rehearsal (pseudo-holdout = the last development season) uses its own log in a scratch directory and never touches the real one."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from src.evaluation.dataset import EvalRow

from .competitions import ROOT
from .splits import HOLDOUT, BenchConfig, Universe, ids_hash, to_eval_row

HOLDOUT_LOG = ROOT / "docs" / "league_models" / "s5_holdout_access_log.json"


class HoldoutLockError(PermissionError):
    """The frozen holdout was already read, or the request does not meet the pre-registration conditions."""


@dataclass(frozen=True)
class Access:
    status: str
    entry: dict


def read_log(path: Path = HOLDOUT_LOG) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["entries"] if path.is_file() else []


def is_consumed(path: Path = HOLDOUT_LOG) -> bool:
    return any(e.get("status") == "CONSUMED" for e in read_log(path))


def _write(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"definition": "append-only access log of the frozen holdout (Sprint 5). One CONSUMED entry closes the holdout for good.", "entries": entries}
    path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def consume(path: Path, *, purpose: str, prereg_sha256: str, fixture_ids_sha256: str, n_fixtures: int, git_sha: str | None, dirty: bool | None, now: datetime | None = None) -> dict:
    """Close the door behind the single reader. Refuses a second access, an empty pre-registration hash and a dirty checkout."""
    if is_consumed(path):
        raise HoldoutLockError("the frozen holdout was already evaluated once; it cannot be read again (new data needs a new frozen period and an ADR)")
    if not prereg_sha256 or len(prereg_sha256) != 64:
        raise HoldoutLockError("the holdout is read only with the sha256 of a pre-registration manifest written before the read")
    if dirty:
        raise HoldoutLockError("the working tree has uncommitted changes: the code that touches the holdout must be the committed code")
    entry = {"event": "HOLDOUT_READ", "status": "CONSUMED", "purpose": purpose, "utc": (now or datetime.now(UTC)).isoformat(), "prereg_sha256": prereg_sha256,
             "fixture_ids_sha256": fixture_ids_sha256, "fixtures": n_fixtures, "git_sha": git_sha}  # fmt: skip
    _write(path, [*read_log(path), entry])
    return entry


def holdout_rows(audit, bcfg: BenchConfig, universe: Universe, *, log_path: Path, prereg_sha256: str, git_sha: str | None, dirty: bool | None, purpose: str = "s5_final_holdout") -> list[EvalRow]:
    """The scores of the frozen holdout, once. The lock is closed first; the returned fixture set must equal the frozen listing."""
    wanted = {r.canonical_fixture_id for r in audit.identity_rows if r.status == "FINISHED" and universe.roles.get((r.canonical_league_id, r.season_id)) == HOLDOUT}
    if ids_hash(sorted(wanted)) != ids_hash(universe.holdout_ids) or wanted != set(universe.holdout_ids):
        raise HoldoutLockError("the holdout fixtures differ from the frozen listing of the protocol manifest")
    consume(log_path, purpose=purpose, prereg_sha256=prereg_sha256, fixture_ids_sha256=ids_hash(universe.holdout_ids), n_fixtures=len(wanted), git_sha=git_sha, dirty=dirty)
    rows = [to_eval_row(r) for r in sorted(audit.identity_rows, key=lambda x: (x.kickoff_utc, x.canonical_fixture_id)) if r.canonical_fixture_id in wanted]
    return rows


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
