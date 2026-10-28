"""xG observations from the Highlightly statistics store, with the as-of contract of ADR 0085.

Source: `artifacts/stats/highlightly/*.jsonl` (ADR 0050), the only xG source with real data in this project. Highlightly returns `expected_goals` per team and match for most
matches it has statistics for; it gives NO publication time. What is known is when WE fetched the row (`stats_fetched_at`), so that moment is the earliest time the xG can be used as
history (`available_at_utc`). Matches whose statistics were fetched weeks after the final whistle are therefore NOT usable for any earlier prediction cutoff: a backtest on the
past would silently use numbers nobody had at that time, so the contract refuses it. A match without both xG values is kept as MISSING_XG / NO_STATISTICS, never filled.

Terms: Highlightly BASIC (free) is research only; storing and using the data in a product needs the PAID plan (ADR 0050, terms 6.1). The features stay behind a flag that is OFF.
"""

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from src.data.teams import TeamDirectory
from src.ingestion.openfootball import FILES
from src.markets.stats_store import finished, store_dir

SOURCE_ID = "highlightly"
SOURCE_VERSION = "highlightly-statistics-v1"
OBSERVED, MISSING_XG, NO_STATISTICS = "OBSERVED", "MISSING_XG", "NO_STATISTICS"


@dataclass(frozen=True)
class Observation:
    source_id: str
    source_match_id: str
    fixture_key: str
    competition_id: str
    season: int
    kickoff_utc: datetime
    home_id: str
    away_id: str
    goals_home: int
    goals_away: int
    xg_home: float | None
    xg_away: float | None
    published_at_utc: datetime | None  # the provider gives none
    ingested_at_utc: datetime | None   # when we fetched the statistics
    available_at_utc: datetime | None  # earliest time the xG may be used as history (= ingested_at_utc)
    coverage_status: str
    source_version: str
    checksum: str


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _valid(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x >= 0


def load(root, directory: TeamDirectory) -> tuple[list[Observation], dict]:
    """Every finished Highlightly match whose clubs resolve, as one Observation each (unique per source match id). Unresolved clubs and invalid values are counted, not guessed."""
    base = store_dir(root)
    rep = {"rows_read": 0, "finished": 0, "duplicates": 0, "unresolved_clubs": set(), "invalid_xg": 0}
    seen: dict[str, Observation] = {}
    for path in sorted(base.glob("*_*.jsonl")) if base.exists() else []:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            rep["rows_read"] += 1
            if not finished(r):
                continue
            rep["finished"] += 1
            kickoff = _utc(r["date"])
            country = FILES[r["league"]][2]
            h = directory.resolve(SOURCE_ID, r["home"]["name"], country, kickoff.date())
            a = directory.resolve(SOURCE_ID, r["away"]["name"], country, kickoff.date())
            if h.team_id is None or a.team_id is None:
                rep["unresolved_clubs"].update(n for n, x in ((r["home"]["name"], h), (r["away"]["name"], a)) if x.team_id is None)
                continue
            stats = r.get("stats")
            hx = ((stats or {}).get("home") or {}).get("expected_goals")
            ax = ((stats or {}).get("away") or {}).get("expected_goals")
            fetched = _utc(r["stats_fetched_at"]) if r.get("stats_fetched_at") else None
            if stats is None or fetched is None:
                status, hx, ax, fetched = NO_STATISTICS, None, None, None
            elif hx is None or ax is None:
                status, hx, ax = MISSING_XG, None, None
            elif not (_valid(hx) and _valid(ax)):
                rep["invalid_xg"] += 1
                status, hx, ax = MISSING_XG, None, None
            else:
                status = OBSERVED
            key = str(r["match_id"])
            obs = Observation(
                SOURCE_ID, key, f"hl-{key}", r["league"], int(r["season"]), kickoff, h.team_id, a.team_id, int(r["score"][0]), int(r["score"][1]),
                None if hx is None else float(hx), None if ax is None else float(ax), None, fetched, fetched if status == OBSERVED else None, status, SOURCE_VERSION,
                hashlib.sha256(json.dumps(r, sort_keys=True).encode("utf-8")).hexdigest(),
            )  # fmt: skip
            if key in seen:
                rep["duplicates"] += 1
                old = seen[key]
                if not (obs.available_at_utc and (old.available_at_utc is None or obs.available_at_utc < old.available_at_utc)):
                    continue  # the earliest observation of a match stands
            seen[key] = obs
    rep["unresolved_clubs"] = sorted(rep["unresolved_clubs"])
    return sorted(seen.values(), key=lambda o: (o.kickoff_utc, o.source_match_id)), rep


def digest(obs: list[Observation]) -> str:
    return hashlib.sha256("|".join(o.checksum for o in sorted(obs, key=lambda o: o.source_match_id)).encode("utf-8")).hexdigest()


def write_jsonl(obs: list[Observation], path: Path) -> None:
    """Deterministic, idempotent export (same input, same bytes)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for o in obs:
        d = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in o.__dict__.items()}
        lines.append(json.dumps(d, sort_keys=True))
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8", newline="\n")
