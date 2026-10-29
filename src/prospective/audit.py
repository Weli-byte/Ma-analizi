"""Audit of the published forecasts as a prospective record (ADR 0090).

- Immutability: every `artifacts/markets/<fixture>/intel-*.json` carries `content_hash` = SHA-256 of the canonical JSON of the body without `content_hash` and `generated_at` (`src/markets/run.py`).
  The hash is recomputed; a mismatch means the file changed after it was written. Files are never rewritten (`write_artifact` refuses an existing path).
- Timing: `information_cutoff` must equal `generated_at` (one clock), and only an artifact whose cutoff is before kick-off can be a forecast; later ones are counted and never scored.
- Settlement: every fixture whose kick-off is more than SETTLE_GRACE in the past and has a pre-kick-off forecast is either in the live record (`track_record.json`) or listed as unsettled with
  its age and league. Nothing is filled in; a missing result stays missing.
"""

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

from src.versioning import canonical_json

SETTLE_GRACE = timedelta(hours=36)


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def recompute_hash(body: dict) -> str:
    core = {k: v for k, v in body.items() if k not in ("content_hash", "generated_at")}
    return hashlib.sha256(canonical_json(core).encode()).hexdigest()


def _results(markets_root) -> tuple[dict, dict]:
    """(settled results, void matches) of the live-results stores, keyed home|away|date."""
    res, void = {}, {}
    for p in sorted((Path(markets_root) / "artifacts" / "results").glob("*.json")):
        store = json.loads(p.read_text(encoding="utf-8"))
        res.update(store.get("matches", {}))
        void.update(store.get("void", {}))
    return res, void


def settlement_lag_hours(markets_root, keys: set[str]) -> dict:
    """Hours from kick-off to the first time a result was SEEN by our collectors (`first_seen`), for the settled forecast fixtures. An upper bound of the provider's publication delay:
    a result first collected by a later backfill shows that backfill's delay, not the provider's. Results with no `first_seen` (the open-history files) are not counted."""
    res, _ = _results(markets_root)
    lags = []
    for k in keys:
        r = res.get(k.replace("__", "|"))
        if r and r.get("first_seen") and r.get("kickoff_utc"):
            lags.append((_utc(r["first_seen"]) - _utc(r["kickoff_utc"])).total_seconds() / 3600)
    lags.sort()
    if not lags:
        return {"n": 0}
    q = lambda f: lags[min(len(lags) - 1, int(f * len(lags)))]  # noqa: E731
    return {"n": len(lags), "median": q(0.5), "p90": q(0.9), "max": lags[-1]}


def audit(markets_root, as_of: datetime) -> dict:
    root = Path(markets_root) / "artifacts" / "markets"
    track = json.loads((root / "track_record.json").read_text(encoding="utf-8"))
    settled = {m["key"] for m in track["matches"]}
    _, void = _results(markets_root)
    out = {"artifacts": 0, "hash_mismatch": [], "cutoff_differs_from_generated": 0, "pre_kickoff": 0, "post_kickoff": 0, "fixtures": 0,
           "fixtures_with_forecast_past_grace": 0, "settled": 0, "unsettled": [], "track_generated_at": track["generated_at"]}  # fmt: skip
    for d in sorted(p for p in root.glob("*__*__*") if p.is_dir()):
        files = sorted(d.glob("intel-*.json"))
        if not files:
            continue
        out["fixtures"] += 1
        kickoff, has_pre = None, False
        for f in files:
            body = json.loads(f.read_text(encoding="utf-8"))
            out["artifacts"] += 1
            if recompute_hash(body) != body.get("content_hash"):
                out["hash_mismatch"].append(f"{d.name}/{f.name}")
            if body["information_cutoff"] != body["generated_at"]:
                out["cutoff_differs_from_generated"] += 1
            kickoff = _utc(body["kickoff_utc"])
            if _utc(body["information_cutoff"]) < kickoff:
                out["pre_kickoff"] += 1
                has_pre = True
            else:
                out["post_kickoff"] += 1
        if has_pre and kickoff + SETTLE_GRACE <= as_of:
            out["fixtures_with_forecast_past_grace"] += 1
            if d.name in settled:
                out["settled"] += 1
            else:
                v = void.get(d.name.replace("__", "|"))
                out["unsettled"].append({"fixture": d.name, "league": json.loads(files[-1].read_text(encoding="utf-8"))["league"], "kickoff": kickoff.isoformat(), "age_days": round((as_of - kickoff).total_seconds() / 86400, 1),
                                         "reason": f"{v['status'].removeprefix('STATUS_')} ({v['source'].split(' ')[0]})" if v else "NO_RESULT_IN_ANY_SOURCE"})  # fmt: skip
    out["settlement_lag_hours"] = settlement_lag_hours(markets_root, settled)
    return out
