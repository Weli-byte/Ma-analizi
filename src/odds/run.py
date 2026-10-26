"""S15 odds collector / value analytics / paper ledger (ADR 0030). PAPER ONLY.

    python -m src.odds.run collect [--league PL]      # read real DraftKings 1X2 lines (ESPN), append quotes
    python -m src.odds.run value   --league PL [--paper]   # edge/EV for locked pre-match forecasts
    python -m src.odds.run settle                    # settle finished paper bets, CLV, summary

Only `timestamp_quality == exact` quotes feed `value`; anything else is reported NOT_ELIGIBLE with no
numbers. Forecasts come from the locked S13 stage predictions (artifacts/snapshots/).
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.cli_utils import configure_output, load_dotenv
from src.config import config_dir_for, load_config
from src.data.teams import TeamDirectory
from src.data_repo import data_repo_url
from src.mlops.oplog import heartbeat
from src.schemas import PredictionRecord

from .espn import LEAGUE_CODES, SOURCE, EspnOddsFeed
from .paper import PaperLedger, dumps, summarize
from .schedule import due_window
from .store import OddsStore, existing_store
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


def _collect_feed(
    root: Path, league: str, feed, source: str, now: datetime | None, data_root: Path | None = None
) -> list[str]:
    cdir = config_dir_for(root)
    directory = TeamDirectory.load(cdir / "team_aliases.yaml")
    _, repo_league, country = LEAGUE_CODES[league]
    now = now or datetime.now(UTC)
    lines = []
    for ev in feed.fetch(league, now):
        if ev["status"] != "STATUS_SCHEDULED" or ev["kickoff_utc"] <= now:
            continue
        day = ev["kickoff_utc"].date()
        if country == "*":  # European cups: clubs of any country
            srcs = (source, "openfootball-cups", "openfootball")
            h = directory.resolve_any_country(srcs, ev["home_name"], day)
            a = directory.resolve_any_country(srcs, ev["away_name"], day)
        else:
            h = directory.resolve(source, ev["home_name"], country, day)
            a = directory.resolve(source, ev["away_name"], country, day)
        if h.team_id is None or a.team_id is None:
            lines.append(
                f"{ev['fixture_id']} SKIPPED unresolved team(s): {ev['home_name']} / {ev['away_name']}"
            )
            continue
        store = OddsStore(data_root or root, ev["fixture_id"])
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
    metas = [
        (base, p)
        for base in ("odds", "odds_remote")
        for p in sorted((Path(root) / "artifacts" / base).glob("*/meta.json"))
        if p.parent.name.startswith(("espn-", "oddsapi-"))
    ]
    for base, meta_path in metas:
        store = OddsStore(root, meta_path.parent.name, base)
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
            store = existing_store(root, b.fixture_id)
            if store is None:
                lines.append(f"{b.bet_id}: odds store for {b.fixture_id} not found")
                continue
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


CREDITS_FILE = "_credits.json"


CUPS = {"UCL", "UEL", "UECL"}  # European cups: no free schedule either, one call per cup_interval_hours
QUOTA_FILE = "_quota_exhausted.json"
QUOTA_PAUSE_HOURS = 6  # after a 401 "quota reached" the odds provider is not called again for this long


def odds_keys() -> list[str]:
    """The Odds API keys in order: THE_ODDS_API_KEY, then THE_ODDS_API_KEY_2 (2nd free account, ADR 0059)."""
    return [k for k in (os.environ.get(KEY_ENV), os.environ.get(KEY_ENV + "_2")) if k]


def key_id(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:8]  # files name a key by this, never by the key itself


def key_paused(root: Path, key: str, now: datetime, first: bool = True) -> str | None:
    """When this key last hit "usage quota reached" (recent only), else None. The first key also honours the
    single-key file written before two keys existed."""
    names = [f"_quota_exhausted_{key_id(key)}.json"] + ([QUOTA_FILE] if first else [])
    for base in ("odds", "odds_remote"):
        for name in names:
            p = Path(root) / "artifacts" / base / name
            if p.exists():
                at = json.loads(p.read_text(encoding="utf-8"))["at"]
                if now - datetime.fromisoformat(at) < timedelta(hours=QUOTA_PAUSE_HOURS):
                    return at
    return None


def key_remaining(root: Path, key: str, first: bool = True) -> int | None:
    names = [f"_credits_{key_id(key)}.json"] + ([CREDITS_FILE] if first else [])
    for name in names:
        p = Path(root) / "artifacts" / "odds" / name
        if p.exists():
            v = json.loads(p.read_text(encoding="utf-8")).get("remaining")
            return None if v is None else int(v)
    return None


def active_key(root: Path, now: datetime, reserve: int = 0) -> str | None:
    """The first key not paused that has at least `reserve` credits (unknown counts as enough)."""
    for i, k in enumerate(odds_keys()):
        if key_paused(root, k, now, i == 0):
            continue
        left = key_remaining(root, k, i == 0)
        if left is None or left >= reserve:
            return k
    return None


def mark_quota(root: Path, key: str, now: datetime) -> None:
    p = Path(root) / "artifacts" / "odds" / f"_quota_exhausted_{key_id(key)}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"at": now.isoformat()}), encoding="utf-8")


def quota_exhausted(root: Path, now: datetime) -> str | None:
    """Non-None while EVERY configured key is paused after a quota 401 (no key: the old single file)."""
    keys = odds_keys()
    if not keys:
        for base in ("odds", "odds_remote"):
            p = Path(root) / "artifacts" / base / QUOTA_FILE
            if p.exists():
                at = json.loads(p.read_text(encoding="utf-8"))["at"]
                if now - datetime.fromisoformat(at) < timedelta(hours=QUOTA_PAUSE_HOURS):
                    return at
        return None
    paused = [key_paused(root, k, now, i == 0) for i, k in enumerate(keys)]
    return max(paused) if all(paused) else None


def is_quota_error(e: Exception) -> bool:
    return "401" in str(
        e
    )  # The Odds API answers 401 ("Usage quota has been reached") when the credits are used up


FDORG_SCHEDULED = {"PL", "PD", "BL1", "SA", "FL1"}  # leagues whose due windows come from football-data.org


def _served_path(store_root: Path, league: str) -> Path:
    return store_root / "artifacts" / "odds" / f"_served_windows_{league}.json"


def _served_windows(store_root: Path, league: str, now: datetime) -> dict[str, str]:
    """Windows (kickoff|window index) already answered by a call; entries older than 3 days are dropped."""
    path = _served_path(store_root, league)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if now - datetime.fromisoformat(v) < timedelta(days=3)}


def collect_exact(
    root: Path, league: str, data_root: Path | None = None, now: datetime | None = None, force: bool = False
) -> list[str]:
    """Exact-timestamp odds (The Odds API), only when a collection window is open for some fixture of
    the league (one call serves the whole league; free plan = 500 credits/month, 2 per call). Meant for
    the cloud runner: the API host is unreachable from some ISPs (TLS interference)."""
    from src.ingestion.football_data_org import FootballDataOrgProvider
    from src.llm.forecast import current_season

    if not odds_keys():
        return [f"{ODDSAPI_SOURCE}: NOT_CONFIGURED (${KEY_ENV} not set)"]
    cdir = config_dir_for(root)
    cfg = load_config("odds", cdir)
    now = now or datetime.now(UTC)
    store_root = Path(data_root or root)
    paused = quota_exhausted(store_root, now)
    if paused and not force:
        return [f"{league}: the-odds-api quota reached at {paused}; paused {QUOTA_PAUSE_HOURS} h (no call)"]
    key = active_key(store_root, now, cfg.odds_api_reserve_credits)  # the reserve holds even with --force
    if key is None:
        return [
            f"{league}: SKIPPED, every key is below the reserve of {cfg.odds_api_reserve_credits} credits"
        ]
    credits_path = store_root / "artifacts" / "odds" / f"_credits_{key_id(key)}.json"

    marker = store_root / "artifacts" / "odds" / f"_last_call_{league}.json"
    if not force and league not in FDORG_SCHEDULED:
        # no free fixture schedule for this league: one call per interval, the fixtures come from the response
        if marker.exists():
            last = datetime.fromisoformat(json.loads(marker.read_text(encoding="utf-8"))["at"])
            hours = cfg.cup_interval_hours if league in CUPS else cfg.odds_only_interval_hours
            if now - last < timedelta(hours=hours):
                wait = f"{hours}h interval"
                return [f"{league}: last call {last.isoformat()}, {wait} (no credit spent)"]
    elif not force:
        fd_key = load_config("ingestion", cdir).api_key("football-data-org")
        if not fd_key:
            return ["football-data-org: NOT_CONFIGURED: cannot tell which fixtures are due"]
        horizon = now + timedelta(hours=cfg.exact_horizon_hours)
        fixtures = FootballDataOrgProvider(fd_key).list_fixtures(league, current_season(now))
        due = [
            f
            for f in fixtures
            if f.status_raw == "NS"
            and now < f.kickoff_utc <= horizon
            and due_window(f.kickoff_utc, now) is not None
        ]
        if not due:
            return [f"{league}: no collection window open (no credit spent)"]
        served = _served_windows(store_root, league, now)
        due_keys = {f"{f.kickoff_utc.isoformat()}|{due_window(f.kickoff_utc, now)}" for f in due}
        # one call answers every fixture of a window; a repeat only spends credits (ADR 0083)
        if due_keys <= set(served):
            return [f"{league}: every open window was already collected (no credit spent)"]
    feed = TheOddsApiFeed(key, cfg.odds_api_bookmakers)
    left = None
    left = key_remaining(store_root, key, key == odds_keys()[0])
    if league in CUPS and not force and left is not None and int(left) < cfg.cup_min_credits:
        # the domestic leagues come first: cups run only while the credits stay above this level
        return [f"{league}: SKIPPED, {left} credits left < cup minimum {cfg.cup_min_credits}"]
    if league in CUPS and not force:
        # cups: the free events list decides; a credit is spent only when a match is inside the horizon
        try:
            horizon = now + timedelta(hours=cfg.exact_horizon_hours)
            soon = [k for k in feed.kickoffs(league) if now < k <= horizon]
        except RuntimeError as e:
            if is_quota_error(e):
                mark_quota(store_root, key, now)
            return [f"{league}: events list failed ({e}); no credit spent"]
        if not soon:
            hrs = cfg.exact_horizon_hours
            return [f"{league}: no match within {hrs} h (free events check, no credit spent)"]
    try:
        lines = _collect_feed(root, league, feed, ODDSAPI_SOURCE, now, store_root)
    except RuntimeError as e:
        if is_quota_error(e):
            mark_quota(store_root, key, now)
            return [f"{league}: QUOTA REACHED (the-odds-api 401); paused {QUOTA_PAUSE_HOURS} h"]
        if league not in CUPS:
            raise
        return [
            f"{league}: the-odds-api call failed ({e}); sport key not confirmed yet, other leagues unaffected"
        ]
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"at": now.isoformat()}), encoding="utf-8")
    if not force and league in FDORG_SCHEDULED:
        done = {**served, **{k: now.isoformat() for k in due_keys}}
        _served_path(store_root, league).write_text(json.dumps(done, sort_keys=True), encoding="utf-8")
    credits_path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(
        {"remaining": feed.requests_remaining, "updated_at": now.isoformat(), "key": key_id(key)}
    )
    credits_path.write_text(body, encoding="utf-8")
    (store_root / "artifacts" / "odds" / CREDITS_FILE).write_text(
        body, encoding="utf-8"
    )  # the last call, any key
    lines.append(f"{league}: credits remaining {feed.requests_remaining}")
    return lines


def sync_remote(root: Path, branch: str = "odds-data") -> list[str]:
    """Copy what the cloud collector committed to `branch` (artifacts/odds/**) into
    artifacts/odds_remote/ (read-only copy; never overwrites local collection)."""
    import subprocess

    def git(*args: str) -> bytes:
        return subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, check=True, timeout=120
        ).stdout

    try:
        git("fetch", "--quiet", data_repo_url(), branch)
        names = git("ls-tree", "-r", "--name-only", "FETCH_HEAD", "--", "artifacts/odds").decode().split()
    except subprocess.CalledProcessError as e:
        why = e.stderr.decode(errors="replace")[:100].strip()
        return [f"sync-remote: branch {branch!r} not available yet ({why})"]
    n = 0
    for name in names:
        rel = name.removeprefix("artifacts/odds/")
        data = git("show", f"FETCH_HEAD:{name}")
        dest = Path(root) / "artifacts" / "odds_remote" / rel
        if not dest.exists() or dest.read_bytes() != data:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            n += 1
    return [f"sync-remote: {len(names)} files on the data repo branch {branch}, {n} updated"]


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
    ce = sub.add_parser("collect-exact")
    ce.add_argument("--league", choices=sorted(LEAGUE_CODES))
    ce.add_argument("--data-root", default=None, help="where quotes are stored (default: --root)")
    ce.add_argument("--force", action="store_true", help="ignore the collection windows (spends credits)")
    sub.add_parser("sync-remote")
    sub.add_parser("settle")
    a = p.parse_args(argv)
    root = Path(a.root)
    try:
        if a.cmd == "collect":
            leagues = [a.league] if a.league else load_config("odds", config_dir_for(root)).leagues
            lines = [line for lg in leagues for line in collect(root, lg)]
        elif a.cmd == "collect-exact":
            leagues = [a.league] if a.league else load_config("odds", config_dir_for(root)).exact_leagues
            lines = [
                line
                for lg in leagues
                for line in collect_exact(root, lg, Path(a.data_root) if a.data_root else None, force=a.force)
            ]
        elif a.cmd == "sync-remote":
            lines = sync_remote(root)
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
