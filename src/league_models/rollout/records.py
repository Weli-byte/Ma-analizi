"""Sprint 6 (ADR 0068): immutable prospective prediction records and the append-only outcome store.

A prospective record is a pre-match prediction made at a real call time. It is never updated: the same (fixture, stage, model version, family) key is
idempotent (a rerun returns the stored record), a rerun that would store different probabilities is a conflict that is refused and reported, and a result
arrives as a SEPARATE append-only outcome that is joined at scoring time. A record of a match whose kick-off has passed, a stage outside its window or
a malformed probability is refused. A storage failure raises (fail closed): nothing is acknowledged that was not written and flushed."""

import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from src.snapshot.stages import SnapshotStage, StageState, stage_state

SCHEMA_VERSION = "lm-prospective-v1"
PROB_FIELDS = ("p_home", "p_draw", "p_away", "p_over25", "p_btts_yes")


class ProspectiveError(ValueError):
    """A record that must not be stored (post-kickoff, wrong stage, malformed probabilities)."""


class ProspectiveConflict(RuntimeError):
    """The key exists with different content: stored records are immutable."""


class StoreUnavailable(OSError):
    """The store cannot be read or written: callers fail closed and raise an alarm."""


def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)


@dataclass(frozen=True)
class ProspectiveRecord:
    fixture_id: str
    league_id: str
    kickoff_utc: str
    prediction_as_of_utc: str
    stage: str
    model_version: str
    family: str
    role: str                                   # global | candidate
    p_home: float | None
    p_draw: float | None
    p_away: float | None
    p_over25: float | None
    p_btts_yes: float | None
    snapshot_hash: str
    data_license_ids: tuple[str, ...]
    coverage_flags: dict = field(default_factory=dict)
    fallback_reason: str | None = None
    created_at: str = ""
    visible_to_users: bool = False              # a shadow record is research evidence, never shown
    schema_version: str = SCHEMA_VERSION

    def key(self) -> str:
        return f"{self.fixture_id}|{self.stage}|{self.model_version}|{self.family}"

    def core(self) -> dict:
        """What identifies the prediction (everything except the wall-clock stamps)."""
        d = asdict(self)
        for k in ("created_at", "prediction_as_of_utc"):
            d.pop(k)
        return d

    def content_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.core(), sort_keys=True, default=list).encode("utf-8")).hexdigest()

    def to_json(self) -> str:
        d = asdict(self)
        d["data_license_ids"] = list(self.data_license_ids)
        d["content_hash"] = self.content_hash()
        return json.dumps(d, sort_keys=True)


def validate(rec: ProspectiveRecord, now: datetime | None = None, atol: float = 1e-6) -> None:
    as_of, kick = _utc(rec.prediction_as_of_utc), _utc(rec.kickoff_utc)
    if as_of >= kick:
        raise ProspectiveError(f"{rec.fixture_id}: prediction_as_of {rec.prediction_as_of_utc} is not before kick-off: a post-kickoff prediction is refused")
    if now is not None and as_of > now:
        raise ProspectiveError(f"{rec.fixture_id}: prediction_as_of is in the future")
    if rec.stage not in {s.value for s in SnapshotStage}:
        raise ProspectiveError(f"unknown stage {rec.stage!r}")
    if stage_state(kick, SnapshotStage(rec.stage), as_of) != StageState.DUE:
        raise ProspectiveError(f"{rec.fixture_id}: {as_of.isoformat()} is outside the {rec.stage} window")
    if rec.visible_to_users:
        raise ProspectiveError("a shadow record cannot be user-visible")
    vals = {k: getattr(rec, k) for k in PROB_FIELDS}
    if all(v is None for v in vals.values()):
        raise ProspectiveError(f"{rec.fixture_id}: no probability at all")
    for k, v in vals.items():
        if v is not None and (not isinstance(v, float | int) or not math.isfinite(v) or v < -atol or v > 1 + atol):
            raise ProspectiveError(f"{rec.fixture_id}: {k}={v!r} is not a probability")
    one = [vals["p_home"], vals["p_draw"], vals["p_away"]]
    if any(x is None for x in one) != all(x is None for x in one):
        raise ProspectiveError(f"{rec.fixture_id}: 1X2 must be complete or absent")
    if one[0] is not None and abs(sum(one) - 1.0) > atol:
        raise ProspectiveError(f"{rec.fixture_id}: 1X2 probabilities sum to {sum(one)!r}")
    if not rec.snapshot_hash or not rec.data_license_ids:
        raise ProspectiveError(f"{rec.fixture_id}: snapshot hash and data licence ids are mandatory")


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    except OSError as e:
        raise StoreUnavailable(f"cannot read {path.name}: {e}") from e


def _append(path: Path, line: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError as e:
        raise StoreUnavailable(f"cannot append to {path.name}: {e}") from e


class ProspectiveStore:
    """Append-only JSONL. `put` is idempotent and conflict-checked."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def all(self) -> list[dict]:
        out = []
        for i, line in enumerate(_read_lines(self.path)):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise StoreUnavailable(f"{self.path.name} line {i + 1} is corrupt: {e}") from e
        return out

    def put(self, rec: ProspectiveRecord, now: datetime | None = None) -> str:
        """Returns CREATED or EXISTS. Raises ProspectiveConflict when the key holds different content."""
        stamped = ProspectiveRecord(**{**asdict(rec), "data_license_ids": tuple(rec.data_license_ids), "created_at": (now or datetime.now(UTC)).isoformat()})
        for old in self.all():
            if f"{old['fixture_id']}|{old['stage']}|{old['model_version']}|{old['family']}" == stamped.key():
                if old["content_hash"] != stamped.content_hash():
                    raise ProspectiveConflict(f"{stamped.key()} already stored with different content: records are immutable; a changed prediction needs a new model version")
                return "EXISTS"  # an idempotent replay is not re-validated against the clock: the stored record already passed
        validate(rec, now)
        _append(self.path, stamped.to_json())
        return "CREATED"


class OutcomeStore:
    """Append-only results, one per fixture. Predictions are never touched; scoring joins the two."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def all(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for i, line in enumerate(_read_lines(self.path)):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                raise StoreUnavailable(f"{self.path.name} line {i + 1} is corrupt: {e}") from e
            out.setdefault(row["fixture_id"], row)
        return out

    def put(self, fixture_id: str, home_goals: int, away_goals: int, source: str, final_at_utc: str | None, now: datetime | None = None) -> str:
        if home_goals < 0 or away_goals < 0:
            raise ProspectiveError("negative goals")
        have = self.all().get(fixture_id)
        if have:
            if (have["home_goals"], have["away_goals"]) != (home_goals, away_goals):
                _append(self.path.with_name(self.path.stem + "_conflicts.jsonl"), json.dumps({"fixture_id": fixture_id, "stored": [have["home_goals"], have["away_goals"]], "seen": [home_goals, away_goals],
                                                                                              "at": (now or datetime.now(UTC)).isoformat()}, sort_keys=True))
                raise ProspectiveConflict(f"{fixture_id}: a different final score is already stored (conflict logged, nothing overwritten)")
            return "EXISTS"
        _append(self.path, json.dumps({"fixture_id": fixture_id, "home_goals": home_goals, "away_goals": away_goals, "source": source, "final_at_utc": final_at_utc,
                                       "recorded_at": (now or datetime.now(UTC)).isoformat()}, sort_keys=True))
        return "CREATED"


class ScoreForecastStore:
    """Append-only sidecar of the goal-distribution outputs of a stored prospective record (ADR 0071): expected goals, the most likely scorelines, the 0-0 and 1-1 mass and the tail
    mass of the score grid. It is keyed exactly like the record and carries the record's content hash, so a score forecast can never be attached to a different prediction. The
    record itself is not changed (its content hash would change and every stored record would turn into a conflict). A rerun with different content is a conflict."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def all(self) -> list[dict]:
        out = []
        for i, line in enumerate(_read_lines(self.path)):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise StoreUnavailable(f"{self.path.name} line {i + 1} is corrupt: {e}") from e
        return out

    @staticmethod
    def key(fixture_id: str, stage: str, model_version: str, family: str) -> str:
        return f"{fixture_id}|{stage}|{model_version}|{family}"

    def put(self, rec: ProspectiveRecord, lam_home: float, lam_away: float, top_scores: list[tuple[str, float]], p00: float, p11: float, tail_mass: float, source: str = "", grid: list | None = None) -> str:
        if not (math.isfinite(lam_home) and math.isfinite(lam_away) and lam_home > 0 and lam_away > 0):
            raise ProspectiveError(f"{rec.fixture_id}: expected goals must be positive and finite")
        body = {"key": self.key(rec.fixture_id, rec.stage, rec.model_version, rec.family), "record_content_hash": rec.content_hash(), "expected_goals_home": round(float(lam_home), 6),
                "expected_goals_away": round(float(lam_away), 6), "top_scores": [[s, round(float(p), 6)] for s, p in top_scores], "p_0_0": round(float(p00), 6), "p_1_1": round(float(p11), 6),
                "tail_mass": round(float(tail_mass), 8), "score_source": source, "schema_version": "lm-score-forecast-v1"}  # fmt: skip
        if grid is not None:  # P(home goals = i, away goals = j) for i, j in 0..6, the realised scoreline is scored against this
            body["score_grid_0_6"] = [[round(float(x), 6) for x in row[:7]] for row in grid[:7]]
        digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()
        for old in self.all():
            if old["key"] == body["key"]:
                if old["content_hash"] != digest:
                    raise ProspectiveConflict(f"{body['key']}: a score forecast with different content is already stored")
                return "EXISTS"
        _append(self.path, json.dumps({**body, "content_hash": digest}, sort_keys=True))
        return "CREATED"
