"""`python -m src.odds.probe_cups`: confirm the European-cup sport keys and club names against the REAL The Odds API.

`/v4/sports` and `/v4/sports/{key}/events` do not use quota credits (documented by the provider). Prints the cup sport
keys the provider lists, then for each configured cup key the event team names and whether our club directory resolves
them (ADR 0055). Meant for the cloud runner: the API host is unreachable from some ISPs.
"""

import json
import os
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from src.config import config_dir_for
from src.data.teams import TeamDirectory

from .theoddsapi import BASE, KEY_ENV, SOURCE, SPORT_KEYS

ROOT = Path(__file__).resolve().parents[2]
CUPS = ("UCL", "UEL", "UECL")


def _get(path: str, key: str) -> list:
    url = f"{BASE}{path}?{urllib.parse.urlencode({'apiKey': key})}"
    with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310 - fixed https host
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    key = os.environ.get(KEY_ENV)
    if not key:
        print(f"NOT_CONFIGURED: ${KEY_ENV} not set")
        return 2
    sports = _get("/sports/", key)
    listed = {s["key"]: s["title"] for s in sports if "uefa" in s["key"] or "champ" in s["key"]}
    print("provider sport keys containing 'uefa'/'champ':", json.dumps(listed, ensure_ascii=False))
    directory = TeamDirectory.load(config_dir_for(ROOT) / "team_aliases.yaml")
    srcs = (SOURCE, "openfootball-cups", "openfootball")
    now = datetime.now(UTC)
    for cup in CUPS:
        sk = SPORT_KEYS[cup]
        print(f"\n{cup} key {sk}: {'LISTED' if sk in listed else 'NOT LISTED'}")
        try:
            events = _get(f"/sports/{sk}/events", key)
        except Exception as e:  # noqa: BLE001 - report and go on
            print(f"  events call failed: {type(e).__name__}: {e}")
            continue
        for ev in events:
            if datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00")) <= now:
                continue
            for side in ("home_team", "away_team"):
                r = directory.resolve_any_country(srcs, ev[side])
                print(f"  {'OK  ' if r.team_id else 'MISS'} {ev[side]!r} -> {r.team_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
