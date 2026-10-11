"""Match-statistics store filled from Highlightly under a daily request budget (ADR 0050).

One JSONL file per league and season under `artifacts/stats/highlightly/` (private data repo, never the public
one). A row holds the match identity, the score and, once fetched, the home/away statistics. Collection is
resumable and idempotent: newest matches first, never more than `budget` requests per run, and a statistics call is
made only for finished matches that have none yet. A match whose statistics are empty is recorded as such (None).
CLI: `python -m src.markets.stats_collect`.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.ingestion.highlightly import LEAGUE_IDS, HighlightlyClient, parse_score
from src.ingestion.provider import ProviderError

LISTING_MAX_AGE = timedelta(hours=24)
RESERVE = 5  # requests left untouched for the owner's manual checks


def store_dir(root: Path) -> Path:
    return Path(root) / "artifacts" / "stats" / "highlightly"


def _path(root: Path, league: str, season: int) -> Path:
    return store_dir(root) / f"{league}_{season}.jsonl"


def load(root: Path, league: str, season: int) -> dict[int, dict]:
    p = _path(root, league, season)
    if not p.exists():
        return {}
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    return {r["match_id"]: r for r in rows}


def save(root: Path, league: str, season: int, rows: dict[int, dict]) -> None:
    p = _path(root, league, season)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(
        "".join(
            json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n"
            for r in sorted(rows.values(), key=lambda r: (r["date"], r["match_id"]))
        ),
        encoding="utf-8",
    )
    tmp.replace(p)


def _meta_path(root: Path) -> Path:
    return store_dir(root) / "_listing.json"


def _load_meta(root: Path) -> dict:
    p = _meta_path(root)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _row(m: dict, league: str, season: int) -> dict:
    return {
        "match_id": m["id"], "date": m["date"], "league": league, "season": season,
        "home": {"id": m["homeTeam"]["id"], "name": m["homeTeam"]["name"]},
        "away": {"id": m["awayTeam"]["id"], "name": m["awayTeam"]["name"]},
        "state": (m.get("state") or {}).get("description"),
        "score": parse_score(m.get("state") or {}), "stats": None, "stats_fetched_at": None,
    }  # fmt: skip


def finished(row: dict) -> bool:
    return row["state"] == "Finished" and row["score"] is not None


def collect(
    root: Path, client: HighlightlyClient, leagues: list[str], seasons: list[int], budget: int,
    now: datetime | None = None,
) -> dict:  # fmt: skip
    """Spend at most `budget` requests: refresh stale listings (newest season first), then fetch statistics for
    the newest finished matches that have none."""
    now = now or datetime.now(UTC)
    start_calls = client.calls
    meta = _load_meta(root)
    summary = {"listing_calls": 0, "statistics_calls": 0, "new_rows": 0, "stopped": None}

    def exhausted() -> bool:
        if client.calls - start_calls >= budget:
            summary["stopped"] = "budget"
            return True
        if client.remaining is not None and client.remaining <= RESERVE:
            summary["stopped"] = "plan quota"
            return True
        return False

    for season in sorted(seasons, reverse=True):
        for league in leagues:
            key = f"{league}_{season}"
            listed = meta.get(key, {}).get("listed_at")
            fresh = listed and now - datetime.fromisoformat(listed) < LISTING_MAX_AGE
            complete = meta.get(key, {}).get("complete")
            past_done = complete and season < now.year - (1 if now.month < 7 else 0)
            if fresh or past_done:
                continue  # a finished season's listing never changes; a current one is refreshed daily
            rows = load(root, league, season)
            offset, total, ok = 0, None, True
            while total is None or offset < total:
                if exhausted():
                    ok = False
                    break
                page = client.matches(LEAGUE_IDS[league], season, offset)
                summary["listing_calls"] += 1
                total = page.total
                for m in page.data:
                    if m["id"] not in rows:
                        rows[m["id"]] = _row(m, league, season)
                        summary["new_rows"] += 1
                    else:  # keep fetched statistics, refresh state and score
                        rows[m["id"]].update(
                            state=(m.get("state") or {}).get("description"),
                            score=parse_score(m.get("state") or {}),
                        )
                offset += 100
            save(root, league, season, rows)
            meta[key] = {"listed_at": now.isoformat(), "total": total, "complete": bool(ok)}
            if not ok:
                meta[key]["listed_at"] = None  # incomplete: list again next run
            _meta_path(root).parent.mkdir(parents=True, exist_ok=True)
            _meta_path(root).write_text(json.dumps(meta, indent=1, sort_keys=True), encoding="utf-8")
            if not ok:
                return summary

    todo = []
    for season in seasons:
        for league in leagues:
            for r in load(root, league, season).values():
                if finished(r) and r["stats_fetched_at"] is None:
                    todo.append((r["date"], league, season, r["match_id"]))
    by_file: dict[tuple[str, int], dict[int, dict]] = {}
    for _, league, season, mid in sorted(todo, reverse=True):
        if exhausted():
            break
        rows = by_file.setdefault((league, season), load(root, league, season))
        r = rows[mid]
        try:
            stats = client.statistics(mid)
        except ProviderError as e:
            summary["last_error"] = str(e)
            break
        summary["statistics_calls"] += 1
        r["stats"] = {"home": stats.get(r["home"]["id"]), "away": stats.get(r["away"]["id"])}
        r["stats_fetched_at"] = now.isoformat()
    for (league, season), rows in by_file.items():
        save(root, league, season, rows)
    summary["remaining_quota"] = client.remaining
    summary["pending_statistics"] = max(0, len(todo) - summary["statistics_calls"])
    return summary
