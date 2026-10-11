"""`python -m src.markets.stats_collect [--budget N] [--from-year Y] [--league L]`: fill the Highlightly
statistics store (corners, cards, shots) within a request budget. Needs HIGHLIGHTLY_API_KEY and a network that
can reach soccer.highlightly.net (GitHub runners; the owner's ISP blocks it)."""

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from src.cli_utils import configure_output, load_dotenv
from src.config import config_dir_for, load_config
from src.ingestion.highlightly import LEAGUE_IDS, HighlightlyClient

from .stats_store import collect

ROOT = Path(__file__).resolve().parents[2]


def main(argv=None) -> int:
    configure_output()
    load_dotenv()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--budget", type=int, default=None, help="max requests this run (default: config)")
    ap.add_argument("--from-year", type=int, default=None)
    ap.add_argument("--league", choices=sorted(LEAGUE_IDS), default=None)
    a = ap.parse_args(argv)
    root = Path(a.root)
    cfg = load_config("markets", config_dir_for(root))
    key = os.environ.get("HIGHLIGHTLY_API_KEY")
    if not key:
        print("highlightly: NOT_CONFIGURED (HIGHLIGHTLY_API_KEY not set)")
        return 0
    now = datetime.now(UTC)
    current = now.year if now.month >= 7 else now.year - 1
    seasons = list(range(a.from_year or cfg.stats_from_year, current + 1))
    leagues = [a.league] if a.league else sorted(LEAGUE_IDS)
    summary = collect(root, HighlightlyClient(key), leagues, seasons, a.budget or cfg.stats_run_budget, now)
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
