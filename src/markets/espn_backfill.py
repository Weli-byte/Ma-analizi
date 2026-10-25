"""One-off historical backfill of the Europa League and Conference League results from ESPN's keyless scoreboard (ADR 0074; UNOFFICIAL, RESEARCH_ONLY like ADR 0070).

    python -m src.markets.espn_backfill --from 2025-07-01 --to 2026-07-01 [--if-needed]

Why: the open public-domain files have no 2025-26 league-phase file for these competitions, so 2025-26 is missing from the history the models are fitted on. The scoreboard answers one day per
request, so the window is scanned day by day (about 1 s per 7 days). Rules: a result is added only when both clubs resolve in the team directory (an unresolved club is listed, never guessed or
registered), league-phase matches are 90-minute scores, a knockout-month match counts only when ESPN says STATUS_FULL_TIME (decided in 90 minutes); extra-time and penalty matches stay out.
`first_seen` is NOW: a backfilled result is never claimed to have been available earlier (the same availability rule as every other collected result). With `--if-needed` the scan runs once per
league and window (a marker is stored with the results) so the cloud job can call it on every run."""

import argparse
import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path

from src.config import config_dir_for
from src.data.teams import TeamDirectory
from src.ingestion import espn_fixtures

from . import live_results as lr

ROOT = Path(__file__).resolve().parents[2]


def backfill(root: Path, directory: TeamDirectory, start: date, end: date, now: datetime, if_needed: bool = False, scanner=None, leagues=lr.ESPN_LEAGUES) -> list[dict]:
    scanner = scanner or espn_fixtures.scan
    # the marker holds the window AND a signature of the ESPN club names the directory knows: a new alias (a club that did not resolve before) makes the window be scanned once more
    espn_names = sorted(a.raw_name for a in getattr(directory, "aliases", []) if a.source == espn_fixtures.SOURCE)
    marker = f"{start.isoformat()}..{end.isoformat()}#{hashlib.sha256(chr(10).join(espn_names).encode('utf-8')).hexdigest()[:8]}"
    out = []
    for league in leagues:
        store = lr.load_store(root, league)
        if if_needed and marker in store.get("espn_backfill_done", []):
            out.append({"league": league, "window": marker, "status": "ALREADY_DONE"})
            continue
        matches, errors = scanner(espn_fixtures.SLUGS[league], start, end)
        results, _, unresolved = lr.from_espn(league, matches, directory, now, full_time_in_knockout=True)
        added = lr.merge(store, results, now)
        skipped_decided_later = sum(1 for m in matches if m["completed"] and m["kickoff_utc"].month in (2, 3, 4, 5, 6) and m["status"] != "STATUS_FULL_TIME")
        # a day that failed is not marked done: the next run repeats the whole window (merge is idempotent)
        if not errors:
            store["espn_backfill_done"] = sorted({*store.get("espn_backfill_done", []), marker})
        lr.save_store(root, league, store)
        out.append({"league": league, "window": marker, "status": "OK" if not errors else f"{len(errors)} DAYS FAILED (repeat)", "matches_in_feed": len(matches), "results_added": added,
                    "knockout_matches_not_added_extra_time_or_penalties": skipped_decided_later, "unresolved_clubs": unresolved})  # fmt: skip
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--from", dest="start", default="2025-07-01")
    ap.add_argument("--to", dest="end", default="2026-07-01")
    ap.add_argument("--if-needed", action="store_true")
    a = ap.parse_args(argv)
    root = Path(a.root)
    directory = TeamDirectory.load(config_dir_for(root) / "team_aliases.yaml")
    now = datetime.now(UTC).replace(microsecond=0)
    rep = backfill(root, directory, date.fromisoformat(a.start), date.fromisoformat(a.end), now, a.if_needed)
    print(json.dumps(rep, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
