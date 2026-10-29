"""As-of safe line-up features (ADR 0086). Computed for one fixture and one stage cutoff from CONFIRMED line-ups seen at or before that cutoff.

Features (every one is NaN / None with a reason when its inputs are missing, never a zero):
- `lineup_status` CONFIRMED / UNKNOWN for the fixture at the stage.
- per team: `formation`, `formation_change` and `starters_changed_n` / `goalkeeper_change` against the team's previous confirmed line-up (a line-up of an EARLIER fixture seen by the cutoff),
  `bench_size`, and `usual_xi_missing_n` = how many of the team's usual XI (the 11 most frequent starters of its last five confirmed line-ups, needs at least three) are not starting.
Not built, because no licensed data exists: player impact (minutes, xA, shrinkage), injuries and suspensions (the API-Football account is suspended, ESPN lists none), rest and fatigue.
The features are behind `lineup_features_enabled()` (env LINEUP_FEATURES_ENABLED=1), OFF by default: `fixture_features` returns None while it is off unless an experiment passes `force=True`.
"""

import os
from collections import Counter
from datetime import datetime

from .lineup_asof import CONFIRMED, UNKNOWN, LineupObservation, by_fixture, snapshot_at

FEATURE_VERSION = "lineup-v1"
NAN = float("nan")
USUAL_XI_MIN_LINEUPS = 3
USUAL_XI_WINDOW = 5


def lineup_features_enabled(environ=None) -> bool:
    return (os.environ if environ is None else environ).get("LINEUP_FEATURES_ENABLED", "0") == "1"


def _previous(team: str, history: dict[str, list[LineupObservation]], kickoff: datetime, cutoff: datetime, exclude: str) -> list[tuple[str, tuple[str, ...], str | None, str | None]]:
    """The team's confirmed line-ups of earlier fixtures as seen at the cutoff, oldest first: (fixture_key, starters, goalkeeper, formation)."""
    rows = []
    for key, versions in history.items():
        if key == exclude or versions[0].kickoff_utc >= kickoff or team not in (versions[0].home_id, versions[0].away_id):
            continue
        o = snapshot_at(versions, cutoff)  # the team's earlier match counts with whatever was seen of it by the cutoff
        if o is None:
            continue
        s = "home" if o.home_id == team else "away"
        rows.append((versions[0].kickoff_utc, key, o.starters[s], o.goalkeepers[s], o.formation[s]))
    rows.sort()
    return [(k, st, gk, fm) for _, k, st, gk, fm in rows]


def team_features(team: str, side: str, current: LineupObservation | None, history: dict[str, list[LineupObservation]], kickoff: datetime, cutoff: datetime, fixture_key: str) -> dict:
    out: dict = {"lineup_status": UNKNOWN if current is None else CONFIRMED}
    if current is None:
        return out | {"formation": None, "formation_change": None, "starters_changed_n": NAN, "goalkeeper_change": None, "bench_size": NAN, "usual_xi_missing_n": NAN,
                      "missing_reason": "NO_CONFIRMED_LINEUP_AT_CUTOFF"}  # fmt: skip
    prev = _previous(team, history, kickoff, cutoff, fixture_key)
    xi = set(current.starters[side])
    out |= {"formation": current.formation[side], "bench_size": float(current.bench_size[side]), "missing_reason": None}
    if prev:
        _, p_starters, p_gk, p_form = prev[-1]
        out["formation_change"] = None if p_form is None or current.formation[side] is None else p_form != current.formation[side]
        out["starters_changed_n"] = float(len(xi - set(p_starters)))
        out["goalkeeper_change"] = None if p_gk is None or current.goalkeepers[side] is None else p_gk != current.goalkeepers[side]
    else:
        out |= {"formation_change": None, "starters_changed_n": NAN, "goalkeeper_change": None}
    recent = prev[-USUAL_XI_WINDOW:]
    if len(recent) >= USUAL_XI_MIN_LINEUPS:
        count = Counter(p for _, st, _, _ in recent for p in st)
        usual = {p for p, _ in sorted(count.items(), key=lambda kv: (-kv[1], kv[0]))[:11]}
        out["usual_xi_missing_n"] = float(len(usual - xi))
    else:
        out["usual_xi_missing_n"] = NAN
    return out


def fixture_features(obs: list[LineupObservation], fixture_key: str, stage_cutoff: datetime, force: bool = False) -> dict | None:
    """Features of one fixture at one stage cutoff, or None while the flag is off. `obs` is every stored observation (all fixtures and versions)."""
    if not (force or lineup_features_enabled()):
        return None
    history = by_fixture(obs)
    versions = history.get(fixture_key)
    if not versions:
        return {"feature_version": FEATURE_VERSION, "cutoff_utc": stage_cutoff.isoformat(), "fixture_key": fixture_key, "lineup_status": UNKNOWN, "missing_reason": "FIXTURE_NOT_OBSERVED"}
    kickoff = versions[0].kickoff_utc
    current = snapshot_at(versions, stage_cutoff)
    return {
        "feature_version": FEATURE_VERSION, "cutoff_utc": stage_cutoff.isoformat(), "fixture_key": fixture_key, "lineup_status": UNKNOWN if current is None else CONFIRMED,
        "home": team_features(versions[0].home_id, "home", current, history, kickoff, stage_cutoff, fixture_key),
        "away": team_features(versions[0].away_id, "away", current, history, kickoff, stage_cutoff, fixture_key),
    }  # fmt: skip
