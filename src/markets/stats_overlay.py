"""Attach Highlightly statistics to the openfootball history and fill its gaps (ADR 0050).

Matches are joined on (home club, away club, season): an ordered pair meets once per league season. A Highlightly
match with no openfootball counterpart (the Super Lig seasons openfootball does not publish) is added as a history
row with its score, so those clubs get a usable history. Unresolved club names are reported, never auto-registered.
"""

import json
from dataclasses import replace
from datetime import datetime, timedelta

from src.config import MarketsConfig
from src.data.teams import TeamDirectory
from src.ingestion.openfootball import FILES

from .data import STATS, StatMatch
from .stats_store import finished, store_dir


def _season_label(season: int) -> str:
    return f"{season}-{str(season + 1)[2:]}"


def _stats(side: dict | None) -> dict:
    side = side or {}
    return {k: side.get(k) for k in STATS}


def attach_stats(
    history: list[StatMatch], root, directory: TeamDirectory, cfg: MarketsConfig
) -> tuple[list[StatMatch], dict]:
    index = {(m.home_id, m.away_id, m.season): i for i, m in enumerate(history)}
    out = list(history)
    added: list[StatMatch] = []
    unresolved: set[str] = set()
    report = {"rows": 0, "matched": 0, "added": 0, "with_statistics": 0}
    base = store_dir(root)
    for path in sorted(base.glob("*_*.jsonl")) if base.exists() else []:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if not finished(r):
                continue
            report["rows"] += 1
            country = FILES[r["league"]][2]
            day = datetime.fromisoformat(r["date"].replace("Z", "+00:00"))
            h = directory.resolve("highlightly", r["home"]["name"], country, day.date())
            a = directory.resolve("highlightly", r["away"]["name"], country, day.date())
            if h.team_id is None or a.team_id is None:
                unresolved.update(
                    n for n, x in ((r["home"]["name"], h), (r["away"]["name"], a)) if x.team_id is None
                )
                continue
            season = _season_label(r["season"])
            stats = r["stats"]
            hs = _stats(stats["home"]) if stats else _stats(None)
            as_ = _stats(stats["away"]) if stats else _stats(None)
            has = any(v is not None for v in hs.values()) and any(v is not None for v in as_.values())
            i = index.get((h.team_id, a.team_id, season))
            if i is not None:
                report["matched"] += 1
                if has:
                    out[i] = replace(out[i], home_stats=hs, away_stats=as_)
                    report["with_statistics"] += 1
            else:
                hg, ag = r["score"]
                added.append(
                    StatMatch(
                        f"hl-{r['match_id']}",
                        season,
                        r["league"],
                        day,
                        day + timedelta(hours=cfg.result_lag_hours),
                        h.team_id,
                        a.team_id,
                        hg,
                        ag,
                        hs,
                        as_,
                        None,
                    )  # fmt: skip
                )
                report["added"] += 1
                report["with_statistics"] += int(has)
    merged = sorted([*out, *added], key=lambda m: m.kickoff_utc)
    report["unresolved_names"] = sorted(unresolved)
    return merged, report
