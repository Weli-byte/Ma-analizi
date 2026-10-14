"""Match-detail statistics stored inside a forecast artifact (ADR 0058): recent matches, head-to-head and league table.

Everything is computed from the same history the model was fitted on (openfootball results, public domain), restricted to
results available at the artifact's `now`, so a stored artifact never contains a result from after its cutoff. A team without
matches gets an empty list, a cup (no table for the running season in the files) gets `table: None` - never an invented row.
"""

from datetime import datetime

from src.ingestion.openfootball import FILES

RECENT = 8
H2H = 8


def _row(m, team_id: str, names: dict[str, str]) -> dict:
    home = m.home_id == team_id
    gf, ga = (m.home_goals, m.away_goals) if home else (m.away_goals, m.home_goals)
    opp = m.away_id if home else m.home_id
    return {
        "date": m.kickoff_utc.date().isoformat(),
        "opponent": names.get(opp, opp),
        "home": home,
        "score": f"{gf}-{ga}",
        "result": "W" if gf > ga else "D" if gf == ga else "L",
        "competition": m.league,
    }


def recent(history, team_id: str, now: datetime, names: dict[str, str], n: int = RECENT) -> list[dict]:
    rows = [m for m in history if team_id in (m.home_id, m.away_id) and m.available_at <= now]
    return [_row(m, team_id, names) for m in sorted(rows, key=lambda m: m.kickoff_utc, reverse=True)[:n]]


def head_to_head(history, a: str, b: str, now: datetime, names: dict[str, str], n: int = H2H) -> list[dict]:
    rows = [m for m in history if {m.home_id, m.away_id} == {a, b} and m.available_at <= now]
    return [
        {
            "date": m.kickoff_utc.date().isoformat(),
            "home": names.get(m.home_id, m.home_id),
            "away": names.get(m.away_id, m.away_id),
            "score": f"{m.home_goals}-{m.away_goals}",
            "competition": m.league,
        }
        for m in sorted(rows, key=lambda m: m.kickoff_utc, reverse=True)[:n]
    ]


def league_table(history, league: str, now: datetime, names: dict[str, str]) -> dict | None:
    """Table of the newest season of a domestic league in the history; None for a cup or a league without matches."""
    if league not in FILES:
        return None
    ms = [m for m in history if m.league == league and m.available_at <= now]
    if not ms:
        return None
    season = max(m.season for m in ms)
    stats: dict[str, dict] = {}
    for m in (x for x in ms if x.season == season):
        for team, gf, ga in (
            (m.home_id, m.home_goals, m.away_goals),
            (m.away_id, m.away_goals, m.home_goals),
        ):
            s = stats.setdefault(team, {"team_id": team, "team": names.get(team, team), "p": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0})  # fmt: skip
            s["p"] += 1
            s["gf"] += gf
            s["ga"] += ga
            s["w" if gf > ga else "d" if gf == ga else "l"] += 1
    rows = sorted(
        stats.values(), key=lambda s: (-(3 * s["w"] + s["d"]), -(s["gf"] - s["ga"]), -s["gf"], s["team"])
    )
    for pos, s in enumerate(rows, 1):
        s["pos"], s["pts"], s["gd"] = pos, 3 * s["w"] + s["d"], s["gf"] - s["ga"]
    return {"season": season, "rows": rows}


def build_details(
    history, home_id: str, away_id: str, league: str, now: datetime, names: dict[str, str]
) -> dict:
    return {
        "recent": {
            "home": recent(history, home_id, now, names),
            "away": recent(history, away_id, now, names),
        },
        "h2h": head_to_head(history, home_id, away_id, now, names),
        "table": league_table(history, league, now, names),
    }
