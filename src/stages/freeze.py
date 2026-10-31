"""Freeze the product forecast of every fixture at fixed stages, as separate immutable records (ADR 0093).

Stages: t-24h, t-90m, t-30m and kickoff (cutoff = kick-off minus 24 h / 90 min / 30 min / 0). A stage record is the published forecast that EXISTED at the stage cutoff: the newest artifact
whose information cutoff is at or before it (artifacts are immutable, so the answer does not depend on when the freezer runs; a late run is recorded as `frozen_late_minutes`, it never
picks a later artifact). The record carries the source artifact's hash, its staleness at the cutoff, the probabilities (1X2 headline and model, over 2.5, BTTS), the odds observation time
(which must not be after the cutoff: closing or later odds can never enter an earlier stage), the line-up status AT the cutoff (CONFIRMED only if a confirmed XI had been seen by then,
otherwise LINEUP_UNCONFIRMED; line-ups are information, never a feature), the calibration fallback flag (no stage-specific calibrator is validated; the identity is used) and the coherence
check of the source forecast. A stage with no forecast at its cutoff is stored as NO_FORECAST, never filled.

Idempotency key: fixture | markets | stage | model version | scheduled kick-off. A record is written once (exclusive create); a second run finds it and compares; a different content is a
conflict, logged, never overwritten. If the kick-off moves, the new schedule gets its own records and the old ones stay. A failure while building a record goes to the dead-letter file
`_failed.jsonl`; `replay` retries it and the record says `replayed: true` (it is not a new forecast: the cutoff selection is the same).

    python -m src.stages.freeze --root . [--replay]
"""

import argparse
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.cli_utils import configure_output
from src.goalmkts.consistency import check as coherence
from src.versioning import canonical_json

STAGES = (("t-24h", 24 * 60), ("t-90m", 90), ("t-30m", 30), ("kickoff", 0))
SCHEMA = "stage-record-v1"
MARKETS = "1x2+over_under_2_5+btts"


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _lineup_status(root: Path, fixture: str, cutoff: datetime, kickoff: datetime) -> str:
    d = root / "artifacts" / "lineups" / fixture
    for f in sorted(d.glob("lineup-*.json")) if d.is_dir() else []:
        body = json.loads(f.read_text(encoding="utf-8"))
        seen = _utc(body["observed_at"])
        if seen <= cutoff and seen <= kickoff:
            return "CONFIRMED"
    return "LINEUP_UNCONFIRMED"


def build_record(root: Path, fixture: str, arts: list[tuple[str, dict]], stage: str, minutes: int, now: datetime) -> dict:
    kickoff = _utc(arts[-1][1]["kickoff_utc"])  # the current schedule (the newest artifact's kick-off)
    cutoff = kickoff - timedelta(minutes=minutes)
    seen = [(n, b) for n, b in arts if _utc(b["information_cutoff"]) <= cutoff and _utc(b["information_cutoff"]) < kickoff]
    rec = {"schema": SCHEMA, "fixture": fixture, "markets": MARKETS, "stage": stage, "cutoff_utc": cutoff.isoformat(), "kickoff_utc": kickoff.isoformat(),
           "frozen_at": now.isoformat(), "frozen_late_minutes": round(max(0.0, (now - cutoff).total_seconds() / 60), 1)}  # fmt: skip
    if not seen:
        rec |= {"status": "NO_FORECAST", "model_version": arts[-1][1].get("model_version")}
    else:
        name, b = seen[-1]
        i = b["intelligence"]
        src = b.get("market_source") or None
        odds_ok = src is None or _utc(src["observed_at"]) <= cutoff
        problems = coherence(b)
        rec |= {
            "status": "OK" if odds_ok and not problems else ("ODDS_AFTER_CUTOFF" if not odds_ok else "INCOHERENT"),
            "model_version": b.get("model_version"), "source_artifact": name, "source_content_hash": b.get("content_hash"), "source_information_cutoff": b["information_cutoff"],
            "staleness_minutes": round((cutoff - _utc(b["information_cutoff"])).total_seconds() / 60, 1),
            "probabilities": {"headline_1x2": i["result"]["headline_probs"], "model_1x2": i["result"]["model_probs"],
                              "over_2_5": next(x["over"] for x in i["goals"]["over_under"] if x["line"] == 2.5), "btts_yes": i["goals"]["btts_yes"]},
            "odds": None if src is None else {"observed_at": src["observed_at"], "quality": src.get("quality")},
            "lineup_status": _lineup_status(root, fixture, cutoff, kickoff), "calibration": "CALIBRATION_FALLBACK: identity (no validated stage-specific calibrator, ADR 0089)",
            "coherence_problems": problems, "lineage_commit": (b.get("lineage") or {}).get("code_commit", "legacy_unknown"),
        }  # fmt: skip
    rec["idempotency_key"] = f"{fixture}|{MARKETS}|{stage}|{rec['model_version']}|{rec['kickoff_utc']}"
    core = {k: v for k, v in rec.items() if k not in ("frozen_at", "frozen_late_minutes", "replayed")}
    rec["record_sha256"] = hashlib.sha256(canonical_json(core).encode()).hexdigest()
    return rec


def _path(root: Path, rec: dict) -> Path:
    stamp = _utc(rec["kickoff_utc"]).strftime("%Y%m%dT%H%M")
    return root / "artifacts" / "stage_records" / rec["fixture"] / f"{rec['stage']}__ko{stamp}.json"


def write(root: Path, rec: dict) -> str:
    p = _path(root, rec)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        with p.open("x", encoding="utf-8", newline="\n") as fh:  # exclusive create: two writers cannot both win
            fh.write(json.dumps(rec, indent=1, sort_keys=True) + "\n")
        return "WRITTEN"
    except FileExistsError:
        old = json.loads(p.read_text(encoding="utf-8"))
        if old.get("record_sha256") == rec["record_sha256"]:
            return "UNCHANGED"
        with (root / "artifacts" / "stage_records" / "_conflicts.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({"key": rec["idempotency_key"], "stored": old.get("record_sha256"), "seen": rec["record_sha256"], "at": rec["frozen_at"]}) + "\n")
        return "CONFLICT"


def _load(root: Path) -> dict[str, list[tuple[str, dict]]]:
    out = {}
    for d in sorted(p for p in (root / "artifacts" / "markets").glob("*__*__*") if p.is_dir()):
        files = sorted(d.glob("intel-*.json"))
        if files:
            out[d.name] = [(f.name, json.loads(f.read_text(encoding="utf-8"))) for f in files]
    return out


def freeze(root: Path, now: datetime, only: set[tuple[str, str]] | None = None, replay: bool = False) -> dict:
    root = Path(root)
    rep = {"WRITTEN": 0, "UNCHANGED": 0, "CONFLICT": 0, "NOT_DUE": 0, "FAILED": 0, "kickoff_moved": 0}
    dlq = root / "artifacts" / "stage_records" / "_failed.jsonl"
    for fixture, arts in _load(root).items():
        if len({b["kickoff_utc"] for _, b in arts}) > 1:
            rep["kickoff_moved"] += 1
        kickoff = _utc(arts[-1][1]["kickoff_utc"])
        for stage, minutes in STAGES:
            if only is not None and (fixture, stage) not in only:
                continue
            if kickoff - timedelta(minutes=minutes) > now:
                rep["NOT_DUE"] += 1
                continue
            try:
                rec = build_record(root, fixture, arts, stage, minutes, now)
                if replay:
                    rec["replayed"] = True
                rep[write(root, rec)] += 1
            except (KeyError, ValueError, TypeError, StopIteration) as e:
                rep["FAILED"] += 1
                dlq.parent.mkdir(parents=True, exist_ok=True)
                with dlq.open("a", encoding="utf-8", newline="\n") as fh:
                    fh.write(json.dumps({"fixture": fixture, "stage": stage, "error": f"{type(e).__name__}: {e}"[:200], "at": now.isoformat()}) + "\n")
    return rep


def replay_failed(root: Path, now: datetime) -> dict:
    dlq = Path(root) / "artifacts" / "stage_records" / "_failed.jsonl"
    items = {(json.loads(x)["fixture"], json.loads(x)["stage"]) for x in dlq.read_text(encoding="utf-8").splitlines() if x.strip()} if dlq.exists() else set()
    if not items:
        return {"replayed": 0}
    rep = freeze(root, now, only=items, replay=True)
    if rep["FAILED"] == 0:
        dlq.rename(dlq.with_suffix(f".replayed-{now.strftime('%Y%m%dT%H%M%S')}.jsonl"))
    return {"replayed": len(items), **rep}


def main(argv=None) -> int:
    configure_output()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".")
    ap.add_argument("--replay", action="store_true", help="retry the dead-letter entries")
    a = ap.parse_args(argv)
    now = datetime.now(UTC)
    print(json.dumps(replay_failed(Path(a.root), now) if a.replay else freeze(Path(a.root), now)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
