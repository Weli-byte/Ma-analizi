"""Line-up observations with an as-of contract and immutable stage snapshots (ADR 0086).

Source: the files of `src/markets/lineups.py` (`artifacts/lineups/<fixture_key>/lineup-*.json`, ESPN public summary, UNOFFICIAL, research only, ADR 0081). ESPN gives NO announcement time: what is
known is `observed_at` (when our collector, running every ten minutes, first saw the XI), so `source_published_at_utc` is empty and the announcement can only be bounded from above.
An observation seen after kick-off is kept but can never enter a pre-match snapshot. A fixture without a confirmed XI at a stage is `UNKNOWN` (never PREDICTED, never filled).
Players carry a PROVISIONAL key (team id + normalised name): there is no licensed player directory (no transfers, no loans), so a renamed or transferred player is a new key.
"""

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

CONFIRMED, UNKNOWN = "CONFIRMED", "UNKNOWN"
STAGES = {"t-24h": timedelta(hours=24), "t-90m": timedelta(minutes=90), "t-30m": timedelta(minutes=30)}
SNAPSHOT_VERSION = "lineup-snapshot-v1"


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def player_key(team_id: str, name: str) -> str:
    """Provisional player key: team id + lower-case ASCII name without punctuation (NOT a canonical player id)."""
    flat = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()
    return f"{team_id}|{' '.join(''.join(c if c.isalnum() else ' ' for c in flat).split())}"


@dataclass(frozen=True)
class LineupObservation:
    fixture_key: str
    league: str
    kickoff_utc: datetime
    home_id: str
    away_id: str
    source_id: str
    source_match_id: str
    observed_at_utc: datetime
    source_published_at_utc: datetime | None   # ESPN gives none
    lineup_status: str                         # CONFIRMED: 11 starters on both sides
    pre_kickoff: bool                          # observed_at <= kickoff
    formation: dict                            # {"home": "4-2-3-1", "away": ...}
    starters: dict                             # {"home": (player keys...), "away": (...)}
    goalkeepers: dict                          # {"home": key|None, "away": key|None}
    bench_size: dict
    lineup_sha256: str
    checksum: str


def _side(team_id: str, side: dict) -> tuple[tuple[str, ...], str | None, int]:
    starters = side["starters"]
    keys = tuple(player_key(team_id, p["name"]) for p in starters if p.get("name"))
    gk = next((player_key(team_id, p["name"]) for p in starters if p.get("position") == "G" and p.get("name")), None)
    return keys, gk, len(side.get("substitutes") or [])


def load(root) -> tuple[list[LineupObservation], dict]:
    """Every stored line-up file as an observation (all versions). Invalid files are quarantined and counted, never repaired."""
    base = Path(root) / "artifacts" / "lineups"
    rep = {"files": 0, "quarantined": 0, "quarantine_reasons": {}}
    out: list[LineupObservation] = []
    for path in sorted(base.glob("*__*__*/lineup-*.json")) if base.exists() else []:
        rep["files"] += 1
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
            home, away = body["lineups"]["home"], body["lineups"]["away"]
            hk, hg, hb = _side(body["home_id"], home)
            ak, ag, ab = _side(body["away_id"], away)
            if len(hk) != 11 or len(ak) != 11 or len(set(hk)) != 11 or len(set(ak)) != 11:
                raise ValueError("NOT_ELEVEN_DISTINCT_STARTERS")
            observed, kickoff = _utc(body["observed_at"]), _utc(body["kickoff_utc"])
            out.append(LineupObservation(
                path.parent.name, body["league"], kickoff, body["home_id"], body["away_id"], body["source"], str(body["espn_event_id"]), observed, None, CONFIRMED, observed <= kickoff,
                {"home": home.get("formation"), "away": away.get("formation")}, {"home": hk, "away": ak}, {"home": hg, "away": ag}, {"home": hb, "away": ab}, body["lineup_sha256"],
                hashlib.sha256(path.read_bytes()).hexdigest(),
            ))  # fmt: skip
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as e:
            rep["quarantined"] += 1
            reason = str(e) if isinstance(e, ValueError) else type(e).__name__
            rep["quarantine_reasons"][reason] = rep["quarantine_reasons"].get(reason, 0) + 1
    return sorted(out, key=lambda o: (o.kickoff_utc, o.fixture_key, o.observed_at_utc)), rep


def snapshot_at(versions: list[LineupObservation], cutoff: datetime) -> LineupObservation | None:
    """The newest pre-kick-off observation seen at or before `cutoff` for ONE fixture, or None (= UNKNOWN). Later versions and post-kick-off sightings are invisible."""
    seen = [o for o in versions if o.pre_kickoff and o.observed_at_utc <= cutoff]
    return max(seen, key=lambda o: o.observed_at_utc) if seen else None


def stage_snapshots(versions: list[LineupObservation]) -> dict[str, dict]:
    """{stage: snapshot dict} for one fixture's versions. Pure and deterministic: the same observations give the same bytes."""
    kickoff = versions[0].kickoff_utc
    out = {}
    for stage, delta in STAGES.items():
        cutoff = kickoff - delta
        o = snapshot_at(versions, cutoff)
        body = {
            "snapshot_version": SNAPSHOT_VERSION, "fixture_key": versions[0].fixture_key, "stage": stage, "cutoff_utc": cutoff.isoformat(), "kickoff_utc": kickoff.isoformat(),
            "lineup_status": UNKNOWN if o is None else CONFIRMED,
            "observed_at_utc": None if o is None else o.observed_at_utc.isoformat(), "source_published_at_utc": None, "lineup_sha256": None if o is None else o.lineup_sha256,
            "formation": None if o is None else o.formation, "starters": None if o is None else {k: list(v) for k, v in o.starters.items()},
        }  # fmt: skip
        body["snapshot_sha256"] = hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()
        out[stage] = body
    return out


def by_fixture(obs: list[LineupObservation]) -> dict[str, list[LineupObservation]]:
    out: dict[str, list[LineupObservation]] = {}
    for o in obs:
        out.setdefault(o.fixture_key, []).append(o)
    return out


def write_snapshots(root, obs: list[LineupObservation], now: datetime) -> dict:
    """Write `artifacts/lineup_snapshots/<fixture_key>/<stage>.json` once. A stage file that already exists must be byte-identical (observations are immutable, so a cutoff view cannot change);
    a different content is reported as a conflict and NOT overwritten. Only stages whose cutoff has passed at `now` are written: before it the view could still grow."""
    rep = {"written": 0, "unchanged": 0, "conflicts": [], "not_due": 0}
    base = Path(root) / "artifacts" / "lineup_snapshots"
    for key, versions in by_fixture(obs).items():
        for stage, body in stage_snapshots(versions).items():
            if _utc(body["cutoff_utc"]) > now:
                rep["not_due"] += 1
                continue
            path = base / key / f"{stage}.json"
            text = json.dumps(body, indent=1, sort_keys=True)
            if path.exists():
                if path.read_text(encoding="utf-8") == text:
                    rep["unchanged"] += 1
                else:
                    rep["conflicts"].append(f"{key}/{stage}")
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
            rep["written"] += 1
    return rep
