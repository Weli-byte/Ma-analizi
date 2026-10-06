"""S15 odds collector / value analytics / paper ledger (ADR 0030). PAPER ONLY.

    python -m src.odds.run collect [--league PL]      # read real DraftKings 1X2 lines (ESPN), append quotes
    python -m src.odds.run value   --league PL [--paper]   # edge/EV for locked pre-match forecasts
    python -m src.odds.run settle                    # settle finished paper bets, CLV, summary

Only `timestamp_quality == exact` quotes feed `value`; anything else is reported NOT_ELIGIBLE with no
numbers. Forecasts come from the locked S13 stage predictions (artifacts/snapshots/).
"""

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.cli_utils import configure_output, load_dotenv
from src.config import config_dir_for, load_config
from src.data.teams import TeamDirectory
from src.mlops.oplog import heartbeat
from src.schemas import PredictionRecord

from .espn import LEAGUE_CODES, SOURCE, EspnOddsFeed
from .paper import PaperLedger, dumps, summarize
from .store import OddsStore
from .theoddsapi import KEY_ENV, TheOddsApiFeed
from .theoddsapi import SOURCE as ODDSAPI_SOURCE
from .value import closing_reference, value_row

ROOT = Path(__file__).resolve().parents[2]


def collect(root: Path, league: str, now: datetime | None = None) -> list[str]:
    lines = _collect_feed(root, league, EspnOddsFeed(), SOURCE, now)  # approximate quotes (no provider time)
    key = os.environ.get(KEY_ENV)
    if key:
        lines += _collect_feed(root, league, TheOddsApiFeed(key), ODDSAPI_SOURCE, now)
    else:
        lines.append(
            f"{ODDSAPI_SOURCE}: NOT_CONFIGURED (${KEY_ENV} not set): no exact-timestamp odds source, "
            "so edge/EV/CLV stay unavailable"
        )
    return lines


def _collect_feed(root: Path, league: str, feed, source: str, now: datetime | None) -> list[str]:
    cdir = config_dir_for(root)
    directory = TeamDirectory.load(cdir / "team_aliases.yaml")
    _, repo_league, country = LEAGUE_CODES[league]
    now = now or datetime.now(UTC)
    lines = []
    for ev in feed.fetch(league, now):
        if ev["status"] != "STATUS_SCHEDULED" or ev["kickoff_utc"] <= now:
            continue
        day = ev["kickoff_utc"].date()
        h = directory.resolve(source, ev["home_name"], country, day)
        a = directory.resolve(source, ev["away_name"], country, day)
        if h.team_id is None or a.team_id is None:
            lines.append(
                f"{ev['fixture_id']} SKIPPED unresolved team(s): {ev['home_name']} / {ev['away_name']}"
            )
            continue
        store = OddsStore(root, ev["fixture_id"])
        store.write_meta(
            {
                "fixture_id": ev["fixture_id"],
                "league": repo_league,
                "kickoff_utc": ev["kickoff_utc"].isoformat(),
                "home_id": h.team_id,
                "away_id": a.team_id,
                "home_name": ev["home_name"],
                "away_name": ev["away_name"],
            }
        )
        n = store.add(ev["quotes"])
        lines.append(f"{ev['fixture_id']} {ev['home_name']} v {ev['away_name']}: {n} new quote(s)"
                     + ("" if ev["quotes"] else " (no complete 1X2 line offered)"))  # fmt: skip
    return lines


def _stage_predictions(root: Path) -> dict[tuple[str, str, str], list[PredictionRecord]]:
    """(home_id, away_id, kickoff date) -> predictions of the LATEST locked stage."""
    index: dict[tuple[str, str, str], tuple[str, list[PredictionRecord]]] = {}
    base = Path(root) / "artifacts" / "snapshots"
    for snap_path in base.glob("*/*/snapshot.json"):
        stage_dir = snap_path.parent
        if not (stage_dir / "LOCK.json").exists():
            continue
        snap = json.loads(snap_path.read_text(encoding="utf-8"))
        lock = json.loads((stage_dir / "LOCK.json").read_text(encoding="utf-8"))
        key = (snap["home_id"], snap["away_id"], snap["kickoff_utc"][:10])
        preds = [
            PredictionRecord.from_json(x)
            for x in (stage_dir / "predictions.jsonl").read_text(encoding="utf-8").splitlines()
            if x.strip()
        ]
        if key not in index or lock["generated_at"] > index[key][0]:
            index[key] = (lock["generated_at"], preds)
    return {k: v[1] for k, v in index.items()}


def value(root: Path, paper: bool) -> list[str]:
    odds_cfg = load_config("odds", config_dir_for(root))
    preds_by_key = _stage_predictions(root)
    ledger = PaperLedger(root)
    lines = []
    for meta_path in sorted((Path(root) / "artifacts" / "odds").glob("espn-*/meta.json")):
        store = OddsStore(root, meta_path.parent.name)
        meta = store.meta()
        kickoff = datetime.fromisoformat(meta["kickoff_utc"])
        preds = preds_by_key.get((meta["home_id"], meta["away_id"], meta["kickoff_utc"][:10]), [])
        if not preds:
            lines.append(f"{meta['fixture_id']}: no locked pre-match forecast yet (S13 stage not run)")
            continue
        quotes = store.quotes()
        for p in preds:
            row = value_row(p, quotes, kickoff, meta["fixture_id"])
            if row.status != "ELIGIBLE":
                ref = ""
                if row.reference_market_probs:
                    probs = tuple(round(x, 3) for x in row.reference_market_probs)
                    ref = f" | market reference (not a signal): {probs}"
                lines.append(f"{meta['fixture_id']} {p.model_id}: NOT_ELIGIBLE ({row.reason}){ref}")
                continue
            lines.append(
                f"{meta['fixture_id']} {p.model_id}: odds={tuple(round(x, 2) for x in row.odds)} "
                f"edge={tuple(round(x, 3) for x in row.edge)} ev={tuple(round(x, 3) for x in row.ev)} "
                f"overround={row.overround:.3f} latency={row.source_latency_s:.1f}s"
            )
            if paper:
                bet = ledger.place(row, kickoff, odds_cfg.min_edge, odds_cfg.min_ev, odds_cfg.stake_units)
                if bet:
                    lines.append(
                        f"   PAPER BET {bet.selection} @ {bet.odds_taken:.2f} "
                        f"(edge {bet.edge:.3f}, ev {bet.ev:.3f})"
                    )
    return lines


def settle(root: Path, now: datetime | None = None) -> list[str]:
    from src.ingestion.football_data_org import FootballDataOrgProvider
    from src.llm.forecast import LEAGUES
    from src.llm.forecast import SOURCE as FDORG

    cdir = config_dir_for(root)
    now = now or datetime.now(UTC)
    ledger = PaperLedger(root)
    done = {s.bet_id for s in ledger.settlements()}
    open_bets = [
        b for b in ledger.bets() if b.bet_id not in done and b.kickoff_utc + timedelta(hours=3) < now
    ]
    lines = []
    if open_bets:
        key = load_config("ingestion", cdir).api_key(FDORG)
        directory = TeamDirectory.load(cdir / "team_aliases.yaml")
        provider = FootballDataOrgProvider(key)
        results: dict[tuple[str, str, str], str] = {}
        start = now.year if now.month >= 7 else now.year - 1
        season = f"{start}-{str(start + 1)[2:]}"
        for code, (_, _, country) in ((c, (None, None, LEAGUES[c][1])) for c in LEAGUES):
            for f in provider.list_fixtures(code, season):
                if f.status_raw != "FT" or f.home_goals is None:
                    continue
                h = directory.resolve(FDORG, f.home_team_raw_name, country, f.kickoff_utc.date())
                a = directory.resolve(FDORG, f.away_team_raw_name, country, f.kickoff_utc.date())
                if h.team_id and a.team_id:
                    results[(h.team_id, a.team_id, f.kickoff_utc.date().isoformat())] = (
                        "H" if f.home_goals > f.away_goals else "A" if f.home_goals < f.away_goals else "D"
                    )
        for b in open_bets:
            store = OddsStore(root, b.fixture_id)
            meta = store.meta()
            outcome = results.get((meta["home_id"], meta["away_id"], meta["kickoff_utc"][:10]))
            if outcome is None:
                lines.append(f"{b.bet_id}: result not available yet")
                continue
            closing = closing_reference(store.quotes(), b.kickoff_utc)
            s = ledger.settle(b, outcome, closing, now)
            lines.append(
                f"{b.bet_id} {b.fixture_id} {b.selection}@{b.odds_taken:.2f}: {outcome} "
                f"profit={s.profit_units:+.2f} clv={s.clv}"
            )
    lines.append(dumps(summarize(ledger.bets(), ledger.settlements())))
    return lines


def main(argv: list[str] | None = None) -> int:
    configure_output()
    load_dotenv()
    heartbeat("odds")  # proves the scheduler ran this tick (gaps are alerted)
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", default=str(ROOT))
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--league", choices=sorted(LEAGUE_CODES), help="default: leagues in configs/odds.yaml")
    v = sub.add_parser("value")
    v.add_argument("--paper", action="store_true", help="record paper bets for eligible edges")
    sub.add_parser("settle")
    a = p.parse_args(argv)
    root = Path(a.root)
    try:
        if a.cmd == "collect":
            leagues = [a.league] if a.league else load_config("odds", config_dir_for(root)).leagues
            lines = [line for lg in leagues for line in collect(root, lg)]
        elif a.cmd == "value":
            lines = value(root, a.paper)
        else:
            lines = settle(root)
    except (RuntimeError, ValueError, KeyError) as e:
        print(f"ODDS RUN FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    print("\n".join(lines) if lines else "nothing to do")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
