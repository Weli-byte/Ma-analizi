"""`python -m src.markets.run [--league PL|PD|all] [--n 10] [--force]`: match intelligence artifacts for the
next real fixtures (ADR 0041).

Per fixture one immutable JSON file `artifacts/markets/<HOME>__<AWAY>__<YYYY-MM-DD>/intel-<stamp>.json`
with `information_cutoff = run time`, data/feature/model versions and a content hash. A fixture whose latest
artifact is younger than `refresh_hours` is skipped (cheap to schedule). The headline 1X2 uses the de-vigged
market only when a complete EXACT-timestamp odds set exists (ADR 0030/0041).
"""

import argparse
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.cli_utils import configure_output, load_dotenv
from src.config import config_dir_for, load_config
from src.data.dataset import resolve_dataset
from src.data_repo import data_repo_url
from src.ingestion.openfootball import FILES
from src.ingestion.openfootball_cups import COMPETITIONS as CUPS
from src.ingestion.openfootball_cups import is_final_date
from src.llm.forecast import LEAGUES, load_upcoming_rows
from src.mlops.oplog import heartbeat
from src.odds.store import OddsStore
from src.odds.value import snapshots
from src.versioning import canonical_json

from .data import ingested_stat_matches, load_stat_matches, merge
from .details import build_details
from .model import MODEL_VERSION, fit_markets
from .predict import intelligence

ROOT = Path(__file__).resolve().parents[2]


def fixture_key(home_id: str, away_id: str, kickoff: datetime) -> str:
    return f"{home_id}__{away_id}__{kickoff.astimezone(UTC).date().isoformat()}"


def latest_exact_odds(root: Path, home: str, away: str, day: str) -> dict | None:
    """Newest COMPLETE 1X2 set whose three quotes are all `exact`; the sharp reference bookmaker wins ties."""
    best = None
    for base in ("odds", "odds_remote"):
        for meta_path in (root / "artifacts" / base).glob("*/meta.json"):
            store = OddsStore(root, meta_path.parent.name, base)
            meta = store.meta()
            if (meta["home_id"], meta["away_id"], meta["kickoff_utc"][:10]) != (home, away, day):
                continue
            for (book, observed), q in snapshots(store.quotes()).items():
                if len(q) != 3 or any(x.timestamp_quality != "exact" for x in q.values()):
                    continue
                rank = (observed, "pinnacle" in book.lower())
                if best is None or rank > best[0]:
                    best = (rank, book, observed, tuple(q[s].decimal_odds for s in ("H", "D", "A")))
    if best is None:
        return None
    return {
        "bookmaker": best[1],
        "observed_at": best[2].isoformat(),
        "odds": list(best[3]),
        "quality": "exact",
    }


def latest_artifact(root: Path, key: str) -> Path | None:
    d = root / "artifacts" / "markets" / key
    files = sorted(d.glob("intel-*.json")) if d.exists() else []
    return files[-1] if files else None


def recent_form(history, team_id: str, now: datetime, names: dict[str, str], n: int = 5) -> list[dict]:
    """The team's last `n` finished matches (result known at `now`), newest first: opponent, venue, score, W/D/L."""
    rows = [m for m in history if team_id in (m.home_id, m.away_id) and m.available_at <= now]
    out = []
    for m in sorted(rows, key=lambda m: m.kickoff_utc, reverse=True)[:n]:
        home = m.home_id == team_id
        gf, ga = (m.home_goals, m.away_goals) if home else (m.away_goals, m.home_goals)
        opp = m.away_id if home else m.home_id
        out.append(
            {
                "date": m.kickoff_utc.date().isoformat(),
                "opponent": names.get(opp, opp),
                "home": home,
                "score": f"{gf}-{ga}",
                "result": "W" if gf > ga else "D" if gf == ga else "L",
            }  # fmt: skip
        )
    return out


def write_artifact(
    root: Path, mm, cfg_hash: str, now: datetime, key: str, provider_fixture_id: str, league: str,
    home_id: str, away_id: str, kickoff: datetime, data_version: str, feature_version: str, odds: dict | None,
    source_note: dict | None = None,
    count_markets: bool = False,
    form: dict | None = None,
    neutral: bool = False,  # cup final at one neutral venue: no home advantage
    details: dict | None = None,  # recent matches, head-to-head, league table (ADR 0058)
    experimental=None,  # ExperimentalModel (ADR 0072): hierarchical goal-market model shown as "Deneysel model (beta)"
) -> Path | None:  # fmt: skip
    """One immutable, content-hashed artifact; an existing file for the same second is never overwritten."""
    intel = intelligence(
        mm, home_id, away_id, league, tuple(odds["odds"]) if odds else None, include_counts=count_markets,
        neutral=neutral,
    )  # fmt: skip
    intel["neutral_venue"] = neutral
    body = {
        "fixture_key": key, "provider_fixture_id": provider_fixture_id, "league": league,
        "home_id": home_id, "away_id": away_id, "kickoff_utc": kickoff.isoformat(),
        "information_cutoff": now.isoformat(), "data_version": data_version,
        "feature_version": feature_version, "model_version": MODEL_VERSION, "config_hash": cfg_hash,
        "market_source": odds, "history": source_note, "form": form, "intelligence": intel,
    }  # fmt: skip
    if details is not None:
        body["details"] = details
    if experimental is not None:
        exp_block = experimental.predict(home_id, away_id, league, kickoff, neutral)
        if exp_block is not None:
            body["experimental"] = exp_block
    body["content_hash"] = hashlib.sha256(canonical_json(body).encode()).hexdigest()
    body["generated_at"] = now.isoformat()
    d = root / "artifacts" / "markets" / key
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"intel-{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    if path.exists():
        return None
    path.write_text(json.dumps(body, indent=1, sort_keys=True), encoding="utf-8")
    return path


def fixtures_from_odds_stores(root: Path, league: str, now: datetime) -> list[dict]:
    """Upcoming fixtures of `league` known from stored odds events (meta.json), newest-soonest first."""
    seen: dict[str, dict] = {}
    for base in ("odds", "odds_remote"):
        for meta_path in (root / "artifacts" / base).glob("*/meta.json"):
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            ko = datetime.fromisoformat(meta["kickoff_utc"])
            if meta.get("league") == league and ko > now:
                seen[meta["fixture_id"]] = {
                    "fixture_id": meta["fixture_id"], "home_id": meta["home_id"],
                    "away_id": meta["away_id"], "kickoff_utc": ko,
                }  # fmt: skip
    return sorted(seen.values(), key=lambda x: x["kickoff_utc"])


def fixtures_with_fallback(root: Path, league: str, now: datetime) -> list[dict]:
    """Odds-store fixtures, completed with football-data.org's schedule (ADR 0059) so a league it covers (the Champions
    League) still gets forecasts when the odds provider is unavailable; the odds-store entry wins on a duplicate."""
    from .live_results import load_store

    seen = {
        (f["home_id"], f["away_id"], f["kickoff_utc"].date()): f
        for f in fixtures_from_odds_stores(root, league, now)
    }
    for f in load_store(root, league).get("upcoming", []):
        ko = datetime.fromisoformat(f["kickoff_utc"])
        key = (f["home_id"], f["away_id"], ko.date())
        if ko > now and key not in seen:
            seen[key] = {
                "fixture_id": f["fixture_id"],
                "home_id": f["home_id"],
                "away_id": f["away_id"],
                "kickoff_utc": ko,
            }
    return sorted(seen.values(), key=lambda x: x["kickoff_utc"])


def run(root: Path, leagues: list[str], n: int, force: bool, now: datetime | None = None) -> list[dict]:
    cdir = config_dir_for(root)
    cfg = load_config("markets", cdir)
    now = now or datetime.now(UTC).replace(microsecond=0)
    cfg_hash = hashlib.sha256(canonical_json(cfg.model_dump()).encode()).hexdigest()[:12]
    written = []
    if cfg.history_source == "openfootball":
        from src.data.teams import TeamDirectory

        from .of_source import load_world

        directory = TeamDirectory.load(cdir / "team_aliases.yaml")
        world = load_world(root, directory, cfg, now)
        history, stats_report = world.history, None
        if cfg.stats_source == "highlightly":
            from .stats_overlay import attach_stats

            history, stats_report = attach_stats(world.history, root, directory, cfg)
        mm = fit_markets(history, now, cfg)
        from .experimental import ExperimentalModel

        exp, exp_status = ExperimentalModel.fit(history, now)
        print(f"experimental model: {exp_status}" + (f" ({exp.rows} matches, selected {exp.hier.info['per_target']['over_under']['chosen']})" if exp else ""))
        names = {t: v["canonical_name"] for t, v in directory.teams.items()}
        count_markets = cfg.stats_plan == "pro"  # the free plan's terms differ (clause 4.1): research only
        wanted = {LEAGUES[c][0] if c in LEAGUES else c for c in leagues}
        counts: dict[str, int] = {}
        todo = []
        for league, f in world.upcoming:
            if league in wanted and counts.get(league, 0) < n:
                counts[league] = counts.get(league, 0) + 1
                todo.append((league, f))
        for league, f in todo:
            country = FILES[league][2]
            h = directory.resolve("openfootball", f.home_team_raw_name, country, f.kickoff_utc.date())
            a = directory.resolve("openfootball", f.away_team_raw_name, country, f.kickoff_utc.date())
            if h.team_id is None or a.team_id is None:
                continue  # never auto-registered
            key = fixture_key(h.team_id, a.team_id, f.kickoff_utc)
            if _fresh(root, key, now, cfg.refresh_hours) and not force:
                continue
            odds = latest_exact_odds(root, h.team_id, a.team_id, f.kickoff_utc.date().isoformat())
            path = write_artifact(
                root, mm, cfg_hash, now, key, f"of-{f.provider_fixture_id}", league, h.team_id, a.team_id,
                f.kickoff_utc, world.data_version, "none (rate model)", odds,
                {"history_source": "openfootball (public domain)", "stale_files": world.stale_files,
                 "unresolved_history_rows": world.unresolved, "rows_without_score": world.skipped_no_score,
                 "statistics": stats_report},
                count_markets,
                {"home": recent_form(history, h.team_id, now, names), "away": recent_form(history, a.team_id, now, names)},
                details=build_details(history, h.team_id, a.team_id, league, now, names), experimental=exp,
            )  # fmt: skip
            if path is not None:
                written.append({"fixture_key": key, "path": str(path)})
        # leagues the open source has no upcoming fixtures for (e.g. Super Lig): the fixtures known from the
        # odds stores (The Odds API events) are the schedule; clubs there are already resolved
        for league in sorted(wanted - set(counts)):
            known = fixtures_with_fallback(root, league, now)
            for fx in known[:n]:
                neutral = is_final_date(league, fx["kickoff_utc"], [x["kickoff_utc"] for x in known])
                key = fixture_key(fx["home_id"], fx["away_id"], fx["kickoff_utc"])
                if _fresh(root, key, now, cfg.refresh_hours) and not force:
                    continue
                odds = latest_exact_odds(
                    root, fx["home_id"], fx["away_id"], fx["kickoff_utc"].date().isoformat()
                )
                path = write_artifact(
                    root, mm, cfg_hash, now, key, fx["fixture_id"], league, fx["home_id"], fx["away_id"],
                    fx["kickoff_utc"], world.data_version, "none (rate model)", odds,
                    {"history_source": "openfootball (public domain)",
                     "fixtures_from": "football-data.org schedule" if fx["fixture_id"].startswith("fd-") else "The Odds API events",
                     "missing_season_files": world.missing_files, "statistics": stats_report},
                    count_markets,
                    {
                        "home": recent_form(history, fx["home_id"], now, names),
                        "away": recent_form(history, fx["away_id"], now, names),
                    },
                    neutral,
                    build_details(history, fx["home_id"], fx["away_id"], league, now, names), experimental=exp,
                )  # fmt: skip
                if path is not None:
                    written.append({"fixture_key": key, "path": str(path)})
        return written

    ref = resolve_dataset(root / load_config("data", cdir).processed_dir)
    matches = merge(load_stat_matches(ref), ingested_stat_matches(root))
    mm = fit_markets(matches, now, cfg)
    for code in leagues:
        bundle = load_upcoming_rows(root, code, None, n)
        for row in bundle.rows:
            key = fixture_key(row.home_id, row.away_id, row.kickoff_utc)
            if _fresh(root, key, now, cfg.refresh_hours) and not force:
                continue
            odds = latest_exact_odds(root, row.home_id, row.away_id, row.kickoff_utc.date().isoformat())
            path = write_artifact(
                root, mm, cfg_hash, now, key, row.fixture_id, row.league_id, row.home_id, row.away_id,
                row.kickoff_utc, bundle.data_version, bundle.feature_version, odds,
                {"history_source": "football-data.co.uk (RESEARCH ONLY)"},
            )  # fmt: skip
            if path is not None:
                written.append({"fixture_key": key, "path": str(path)})
    return written


def _fresh(root: Path, key: str, now: datetime, hours: float) -> bool:
    prev = latest_artifact(root, key)
    if not prev:
        return False
    age = now - datetime.fromisoformat(json.loads(prev.read_text(encoding="utf-8"))["generated_at"])
    return age < timedelta(hours=hours)


def sync_remote(root: Path, branch: str = "markets-data") -> str:
    """Copy artifacts the cloud workflow committed to `branch` into artifacts/markets (add-only: artifacts
    are immutable, an existing local file is never replaced)."""
    import subprocess

    def git(*args: str) -> bytes:
        return subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, check=True, timeout=120
        ).stdout

    try:
        git("fetch", "--quiet", data_repo_url(), branch)
        names = (
            git(
                "ls-tree",
                "-r",
                "--name-only",
                "FETCH_HEAD",
                "--",
                "artifacts/markets",
                "artifacts/injuries",
                "artifacts/results",
            "artifacts/crests",
            )
            .decode()
            .split()
        )
    except subprocess.CalledProcessError as e:
        return f"sync-remote: branch {branch!r} not available yet ({e.stderr.decode(errors='replace')[:100].strip()})"
    n = 0
    for name in names:
        dest = Path(root) / name
        # forecasts are immutable (add-only); the results store and the track record are growing fact tables: newer copy wins
        mutable = name.startswith("artifacts/results/") or name.endswith("/track_record.json")
        if dest.exists() and not mutable:
            continue
        data = git("show", f"FETCH_HEAD:{name}")
        if not dest.exists() or dest.read_bytes() != data:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            n += 1
    return f"sync-remote: {len(names)} files on the data repo branch {branch}, {n} new"


def main(argv=None) -> int:
    configure_output()
    load_dotenv()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument(
        "--league", default="all", choices=["all", *sorted(LEAGUES), *sorted(FILES), *sorted(CUPS)]
    )
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--sync-remote", action="store_true", help="only copy what the cloud workflow committed")
    ap.add_argument("--no-sync", action="store_true", help="do not pull cloud artifacts before running")
    a = ap.parse_args(argv)
    heartbeat("markets")
    if a.sync_remote:
        print(sync_remote(Path(a.root)))
        return 0
    if not a.no_sync:
        print(sync_remote(Path(a.root)))  # cloud artifacts first (best effort, add-only)
    cfg = load_config("markets", config_dir_for(Path(a.root)))
    everything = [*sorted(FILES), *sorted(CUPS)] if cfg.history_source == "openfootball" else sorted(LEAGUES)
    leagues = everything if a.league == "all" else [a.league]
    out = run(Path(a.root), leagues, a.n, a.force)
    for w in out:
        print(json.dumps(w))
    print(f"markets: {len(out)} artifact(s) written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
