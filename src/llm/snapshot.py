"""Structured snapshot sent to an LLM provider. Built ONLY from fields already safe to reveal
at `information_cutoff` -- `EvalRow.features` already excludes anything not available by then
(enforced upstream by `src.features`); this function additionally never reads `EvalRow.outcome`,
`home_goals`, or `away_goals`, so a future refactor of `EvalRow` cannot silently leak the result
through this path.
"""

from datetime import datetime

from src.evaluation.dataset import EvalRow


def _current_information(row, cutoff: datetime) -> dict:
    info: dict = {"odds": {k: list(v) for k, v in sorted(row.odds.items())}}
    injuries = getattr(row, "availability", {}).get("injuries")
    if injuries and injuries.get("status") == "OBSERVED":
        # only entries the provider itself dated at/before the cutoff; observation time is not sent
        info["injuries"] = {
            "source": injuries["source"],
            "players": [
                p for p in injuries["players"] if datetime.fromisoformat(p["effective_at"]) <= cutoff
            ],
            "note": injuries["note"],
        }
    lineups = getattr(row, "availability", {}).get("lineups")
    if (
        lineups
        and lineups.get("status") == "OBSERVED"
        and datetime.fromisoformat(lineups["observed_at"]) <= cutoff  # observed at/before the cutoff only
    ):
        info["lineups"] = {k: lineups[k] for k in ("source", "home", "away", "note")}  # fetch time not sent
    return info


def build_snapshot(row: EvalRow, information_cutoff: datetime) -> dict:
    """JSON-serializable. `None`-valued features are omitted (never sent as a fake 0)."""
    return {
        "fixture_id": row.fixture_id,
        "league_id": row.league_id,
        "season": row.season,
        "kickoff_utc": row.kickoff_utc.isoformat(),
        "information_cutoff": information_cutoff.isoformat(),
        "home_team_id": row.home_id,
        "away_team_id": row.away_id,
        "permitted_historical_features": {k: v for k, v in sorted(row.features.items()) if v is not None},
        "permitted_current_information": _current_information(row, information_cutoff),
    }


FORBIDDEN_KEYS = frozenset({"outcome", "result", "home_goals", "away_goals", "score", "final_score"})


class CutoffViolation(RuntimeError):
    """The snapshot would reveal information not available at `information_cutoff`."""


def audit_snapshot(snapshot: dict, kickoff_utc: datetime, cutoff: datetime) -> None:
    """Final gate run before EVERY provider call (ADR 0024). Raises `CutoffViolation` (and so no
    call is made) if the snapshot's cutoff is after kickoff or differs from the requested cutoff,
    or if any result-bearing key appears anywhere in it."""
    if cutoff > kickoff_utc:
        raise CutoffViolation(f"information_cutoff {cutoff} is after kickoff {kickoff_utc}")
    if snapshot.get("information_cutoff") != cutoff.isoformat():
        raise CutoffViolation("snapshot information_cutoff does not match the requested cutoff")

    def walk(node, path: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if str(k).lower() in FORBIDDEN_KEYS:
                    raise CutoffViolation(f"result-bearing key {path}{k!r} in snapshot")
                walk(v, f"{path}{k}.")
        elif isinstance(node, list):
            for v in node:
                walk(v, path)

    walk(snapshot, "")
    # ADR 0028: any provider-dated item inside the snapshot must be dated at/before the cutoff
    for p in snapshot.get("permitted_current_information", {}).get("injuries", {}).get("players", []):
        if datetime.fromisoformat(p["effective_at"]) > cutoff:
            raise CutoffViolation(f"injury entry {p['player']!r} is dated after the cutoff")
