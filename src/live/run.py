"""S14 live runner (ADR 0029):

    python -m src.live.run --source fdorg --league PL            # one pass over matches in play now
    python -m src.live.run --source openligadb --league bl1 --loop 30

Finds matches that are in play right now, polls them (REAL feeds), updates events/state and writes
immutable LIVE forecasts under artifacts/live/<fixture>/. With `--loop N` it repeats every N
seconds (>= 30) until nothing is live or `--max-minutes` elapse. No `--now` override; no LLM calls.

Probabilities need pre-match goal rates, which exist only for fixtures of leagues in the dataset
(EPL/LaLiga via the Poisson fit on train+validation seasons). Other leagues (e.g. Bundesliga) get
events and state but no forecast (`missing: prematch_rates`) -- never a made-up prior.
"""

import argparse
import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from src.cli_utils import configure_output, load_dotenv
from src.config import config_dir_for, load_config
from src.data.dataset import resolve_dataset
from src.data.teams import TeamDirectory
from src.llm.forecast import LEAGUES
from src.llm.forecast import SOURCE as FDORG_SOURCE
from src.mlops.oplog import heartbeat

from .engine import PrematchRates, process_snapshot
from .feeds import FootballDataOrgLiveFeed, OpenLigaDBFeed, parse_fdorg_match
from .store import LiveStore

ROOT = Path(__file__).resolve().parents[2]


def fit_poisson(root: Path):
    from src.snapshot.run import fit_models

    models, info = fit_models(root, ["poisson"])
    return models[0], info


def rates_for(
    model, directory: TeamDirectory, league: str, home_raw: str, away_raw: str, day
) -> PrematchRates | None:
    repo_league, country = LEAGUES[league]
    h = directory.resolve(FDORG_SOURCE, home_raw, country, day)
    a = directory.resolve(FDORG_SOURCE, away_raw, country, day)
    if h.team_id is None or a.team_id is None:
        return None
    lam_h, lam_a, seen_h, seen_a = model.expected_goals(h.team_id, a.team_id)
    note = "; ".join(
        f"{side} team unseen in training: league-average strength"
        for side, seen in (("home", seen_h), ("away", seen_a))
        if not seen
    )
    return PrematchRates(lam_h, lam_a, "poisson fit on train+validation seasons", note)


def capture_first_in_play(root: Path, m: dict) -> None:
    """Keep the first raw in-play payload seen for each match (real-shape evidence, written once)."""
    if m.get("status") not in ("IN_PLAY", "PAUSED"):
        return
    path = Path(root) / "artifacts" / "live" / "_captures" / f"fdorg_{m['id']}_{m['status']}.json"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).isoformat()
        path.write_text(
            json.dumps({"captured_at_utc": stamp, "match": m}, indent=1, sort_keys=True, ensure_ascii=False),
            encoding="utf-8",
        )


def tick_fdorg(
    root: Path, league: str, feed: FootballDataOrgLiveFeed, directory, ref, holder: dict
) -> list[str]:
    """One pass. `league` is a football-data.org code (PL, PD, ...) or ALL (every competition in the
    plan, still ONE request). The live list already carries status and score. Forecasts need
    pre-match rates, which exist only for PL/PD; other competitions get events/state only."""
    lines = []
    for m in feed.list_live_all() if league == "ALL" else feed.list_live(league):
        now = datetime.now(UTC)
        code = m.get("competition", {}).get("code", league)
        capture_first_in_play(root, m)
        sha = hashlib.sha256(json.dumps(m, sort_keys=True).encode()).hexdigest()
        snap = parse_fdorg_match(m, now, sha)
        store = LiveStore(root, snap.fixture_id)
        rates = store.load_rates()
        if rates is None and code in LEAGUES:
            if holder.get("model") is None:  # fit only when a live match needs rates, once per process
                holder["model"], info = fit_poisson(root)
                print(f"fitted poisson on {info['fit_rows']} rows")
            rates = rates_for(
                holder["model"], directory, code, m["homeTeam"]["name"], m["awayTeam"]["name"],
                snap.kickoff_utc.date(),
            )  # fmt: skip
            if rates is not None:
                store.save_rates(rates)
        res = process_snapshot(store, snap, rates, now, ref.data_version, "fv2")
        lines.append(
            f"{code} {snap.fixture_id} {snap.status.value} score={snap.score} "
            f"-> {res.status} {res.reason or ''}"
        )
    return lines


def tick_openligadb(root: Path, league: str, feed: OpenLigaDBFeed, ref) -> list[str]:
    lines = []
    for m in feed.list_current(league):
        now = datetime.now(UTC)
        snap = feed.poll(str(m["matchID"]), now)
        res = process_snapshot(LiveStore(root, snap.fixture_id), snap, None, now, ref.data_version, "fv2")
        lines.append(
            f"{snap.fixture_id} {snap.status.value} score={snap.score} -> {res.status} {res.reason or ''}"
        )
    return lines


def main(argv: list[str] | None = None) -> int:
    configure_output()
    load_dotenv()
    heartbeat("live")  # proves the scheduler ran this tick (gaps are alerted)
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--source", required=True, choices=["fdorg", "openligadb"])
    p.add_argument("--league", required=True, help="fdorg: ALL|PL|PD ; openligadb: bl1|bl2|bl3")
    p.add_argument("--loop", type=int, default=0, help="poll every N seconds (>=30); 0 = one pass")
    p.add_argument("--max-minutes", type=int, default=150)
    a = p.parse_args(argv)
    if a.loop and a.loop < 30:
        print("--loop must be >= 30 seconds (feed courtesy / free-tier limits)", file=sys.stderr)
        return 2
    root = Path(a.root)
    try:
        cdir = config_dir_for(root)
        ref = resolve_dataset(root / load_config("data", cdir).processed_dir)
        deadline = time.monotonic() + a.max_minutes * 60
        directory = fd_feed = ol_feed = None
        holder: dict = {}
        if a.source == "fdorg":
            if a.league != "ALL" and a.league not in LEAGUES:
                print(f"fdorg league must be ALL or one of {sorted(LEAGUES)}", file=sys.stderr)
                return 2
            key = load_config("ingestion", cdir).api_key(FDORG_SOURCE)
            if not key:
                print("FOOTBALL_DATA_ORG_API_KEY is not set", file=sys.stderr)
                return 2
            fd_feed = FootballDataOrgLiveFeed(key)
            directory = TeamDirectory.load(cdir / "team_aliases.yaml")
        else:
            ol_feed = OpenLigaDBFeed()
        while True:
            if a.source == "fdorg":
                lines = tick_fdorg(root, a.league, fd_feed, directory, ref, holder)
            else:
                lines = tick_openligadb(root, a.league, ol_feed, ref)
            stamp = datetime.now(UTC).strftime("%H:%M:%S")
            print(f"[{stamp}] " + (" | ".join(lines) if lines else "no live match"))
            if not a.loop or not lines or time.monotonic() > deadline:
                break
            time.sleep(a.loop)
    except (RuntimeError, ValueError, KeyError) as e:
        print(f"LIVE RUN FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
