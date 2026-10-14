"""Referee and player information for the match page, INFORMATION ONLY (ADR 0059).

Source: football-data.org free tier (verified 2026-10-14): every match carries its referee (name, nationality) once appointed,
and `/competitions/{code}/scorers` lists the top scorers (goals, assists, penalties, matches) for PL, PD, BL1, SA, FL1, DED,
PPL and CL. Collected by `live_results` into `artifacts/results/<league>.json`. Nothing here enters a probability: a referee's
record over a handful of matches is noise, and there is no history to validate a player effect on. Referee numbers are
computed from the results we hold for the running season (the source has no cards, so no card averages are shown).
"""

import json
from datetime import datetime, timedelta
from pathlib import Path


def _stores(root: Path) -> list[dict]:
    out = []
    for p in sorted((Path(root) / "artifacts" / "results").glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except ValueError:
            continue
    return out


def referee_for(root: Path, home_id: str, away_id: str, kickoff: datetime) -> dict | None:
    """The appointed referee of an upcoming fixture, when the source already names one."""
    for st in _stores(root):
        for f in st.get("upcoming", []):
            if (
                f["home_id"] == home_id
                and f["away_id"] == away_id
                and abs(datetime.fromisoformat(f["kickoff_utc"]) - kickoff) < timedelta(hours=6)
            ):
                return f.get("referee")
    return None


def referee_stats(root: Path, name: str) -> dict:
    rows = [
        m
        for st in _stores(root)
        for m in st.get("matches", {}).values()
        if (m.get("referee") or {}).get("name") == name
    ]
    n = len(rows)
    if not n:
        return {"matches": 0}
    goals = [m["home_goals"] + m["away_goals"] for m in rows]
    return {
        "matches": n,
        "avg_goals": sum(goals) / n,
        "home_win": sum(m["home_goals"] > m["away_goals"] for m in rows) / n,
        "draw": sum(m["home_goals"] == m["away_goals"] for m in rows) / n,
        "away_win": sum(m["home_goals"] < m["away_goals"] for m in rows) / n,
        "over_2_5": sum(g > 2 for g in goals) / n,
        "recent": [
            {
                "date": m["kickoff_utc"][:10],
                "home_id": m["home_id"],
                "away_id": m["away_id"],
                "score": f"{m['home_goals']}-{m['away_goals']}",
            }  # fmt: skip
            for m in sorted(rows, key=lambda m: m["kickoff_utc"], reverse=True)[:5]
        ],
    }


def scorers_for(root: Path, team_id: str, n: int = 5) -> list[dict]:
    """The team's top scorers per competition we hold, best first."""
    out = []
    for st in _stores(root):
        rows = [s for s in st.get("scorers", []) if s.get("team_id") == team_id]
        out += [{**s, "competition": st.get("league")} for s in sorted(rows, key=lambda s: -s["goals"])[:n]]
    return out


def people_for(root: Path, home_id: str, away_id: str, kickoff: datetime) -> dict:
    ref = referee_for(root, home_id, away_id, kickoff)
    return {
        "referee": ref,
        "referee_stats": referee_stats(root, ref["name"]) if ref else None,
        "scorers": {"home": scorers_for(root, home_id), "away": scorers_for(root, away_id)},
    }
