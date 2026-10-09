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
from src.llm.forecast import LEAGUES, load_upcoming_rows
from src.mlops.oplog import heartbeat
from src.odds.store import OddsStore
from src.odds.value import snapshots
from src.versioning import canonical_json

from .data import ingested_stat_matches, load_stat_matches, merge
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


def write_artifact(
    root: Path, mm, cfg_hash: str, now: datetime, key: str, provider_fixture_id: str, league: str,
    home_id: str, away_id: str, kickoff: datetime, data_version: str, feature_version: str, odds: dict | None,
    source_note: dict | None = None,
) -> Path | None:  # fmt: skip
    """One immutable, content-hashed artifact; an existing file for the same second is never overwritten."""
    intel = intelligence(mm, home_id, away_id, league, tuple(odds["odds"]) if odds else None)
    body = {
        "fixture_key": key, "provider_fixture_id": provider_fixture_id, "league": league,
        "home_id": home_id, "away_id": away_id, "kickoff_utc": kickoff.isoformat(),
        "information_cutoff": now.isoformat(), "data_version": data_version,
        "feature_version": feature_version, "model_version": MODEL_VERSION, "config_hash": cfg_hash,
        "market_source": odds, "history": source_note, "intelligence": intel,
    }  # fmt: skip
    body["content_hash"] = hashlib.sha256(canonical_json(body).encode()).hexdigest()
    body["generated_at"] = now.isoformat()
    d = root / "artifacts" / "markets" / key
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"intel-{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    if path.exists():
        return None
    path.write_text(json.dumps(body, indent=1, sort_keys=True), encoding="utf-8")
    return path


def run(root: Path, leagues: list[str], n: int, force: bool, now: datetime | None = None) -> list[dict]:
    cdir = config_dir_for(root)
    cfg = load_config("markets", cdir)
    now = now or datetime.now(UTC).replace(microsecond=0)
    cfg_hash = hashlib.sha256(canonical_json(cfg.model_dump()).encode()).hexdigest()[:12]
    written = []
    if cfg.history_source == "openfootball":
        from src.data.teams import TeamDirectory

        from .of_source import load_world

        world = load_world(root, TeamDirectory.load(cdir / "team_aliases.yaml"), cfg, now)
        mm = fit_markets(world.history, now, cfg)
        wanted = {LEAGUES[c][0] for c in leagues}
        counts: dict[str, int] = {}
        todo = []
        for league, f in world.upcoming:
            if league in wanted and counts.get(league, 0) < n:
                counts[league] = counts.get(league, 0) + 1
                todo.append((league, f))
        directory = TeamDirectory.load(cdir / "team_aliases.yaml")
        for league, f in todo:
            country = {"EPL": "ENG", "LALIGA": "ESP"}[league]
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
                 "unresolved_history_rows": world.unresolved, "rows_without_score": world.skipped_no_score},
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
        git("fetch", "--quiet", "origin", branch)
        names = (
            git("ls-tree", "-r", "--name-only", f"origin/{branch}", "--", "artifacts/markets")
            .decode()
            .split()
        )
    except subprocess.CalledProcessError as e:
        return f"sync-remote: branch {branch!r} not available yet ({e.stderr.decode(errors='replace')[:100].strip()})"
    n = 0
    for name in names:
        dest = Path(root) / name
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(git("show", f"origin/{branch}:{name}"))
            n += 1
    return f"sync-remote: {len(names)} files on origin/{branch}, {n} new"


def main(argv=None) -> int:
    configure_output()
    load_dotenv()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--league", default="all", choices=["all", *sorted(LEAGUES)])
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
    leagues = sorted(LEAGUES) if a.league == "all" else [a.league]
    out = run(Path(a.root), leagues, a.n, a.force)
    for w in out:
        print(json.dumps(w))
    print(f"markets: {len(out)} artifact(s) written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
