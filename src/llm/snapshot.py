"""Structured snapshot sent to an LLM provider. Built ONLY from fields already safe to reveal
at `information_cutoff` -- `EvalRow.features` already excludes anything not available by then
(enforced upstream by `src.features`); this function additionally never reads `EvalRow.outcome`,
`home_goals`, or `away_goals`, so a future refactor of `EvalRow` cannot silently leak the result
through this path.
"""

from datetime import datetime

from src.evaluation.dataset import EvalRow


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
        "permitted_historical_features": {
            k: v for k, v in sorted(row.features.items()) if v is not None
        },
        "permitted_current_information": {
            "odds": {k: list(v) for k, v in sorted(row.odds.items())},
        },
    }
