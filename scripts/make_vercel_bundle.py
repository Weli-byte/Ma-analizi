"""`python scripts/make_vercel_bundle.py [--out dist/vercel]`: what a Vercel deployment needs (ADR 0057).

Vercel runs the app as a serverless function, so the deployment is a SNAPSHOT: the code, the configs and the
newest forecast file of every fixture (plus the newest injuries observation per day) copied from the local
`artifacts/`. To refresh the data, run `python -m src.markets.run --sync-remote` and build + deploy again.
No `.env`, key or user database is included; secrets go to Vercel's environment variables. Favourites are kept
in /tmp and reset when the function restarts (see docs/hosting.md)."""

import argparse
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc")

INDEX = '''"""Vercel entry point: the same ASGI app as `uvicorn src.api.app:create_app --factory`."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("FORECAST_USER_DB", "/tmp/users.sqlite")  # the deployment disk is read-only except /tmp

from src.api.app import create_app  # noqa: E402

app = create_app(ROOT)
'''


def _newest_per_dir(src: Path, dst: Path, pattern: str, recent_after: str | None = None) -> int:
    """Newest file of every folder; `recent_after` (ISO date) keeps only folders dated on or after it."""
    n = 0
    for d in sorted(p for p in src.glob("*") if p.is_dir()):
        if recent_after is not None and d.name.rsplit("__", 1)[-1] < recent_after:
            continue
        files = sorted(d.glob(pattern))
        if files:
            (dst / d.name).mkdir(parents=True, exist_ok=True)
            shutil.copy(files[-1], dst / d.name / files[-1].name)
            n += 1
    return n


def build(out: Path, root: Path = ROOT, now: datetime | None = None) -> list[str]:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / "index.py").write_text(INDEX, encoding="utf-8")  # Vercel finds `app` here
    shutil.copy(root / "requirements-api.lock", out / "requirements.txt")
    shutil.copytree(root / "src", out / "src", ignore=SKIP)
    shutil.copytree(root / "configs", out / "configs", ignore=SKIP)
    markets = root / "artifacts" / "markets"
    if markets.exists():
        _newest_per_dir(markets, out / "artifacts" / "markets", "intel-*.json")
        (out / "artifacts" / "markets").mkdir(parents=True, exist_ok=True)  # even with no forecast yet
        for extra in ("track_record.json", "shadow_gate.json"):  # ADR 0076: shown on the track page
            if (markets / extra).exists():
                shutil.copy(markets / extra, out / "artifacts" / "markets" / extra)
    lineups = root / "artifacts" / "lineups"  # starting line-ups, information only (ADR 0081)
    if lineups.exists():
        # the full history stays in the data repository (ADR 0086)
        recent = ((now or datetime.now(UTC)) - timedelta(days=3)).date().isoformat()
        _newest_per_dir(lineups, out / "artifacts" / "lineups", "lineup-*.json", recent_after=recent)
    injuries = root / "artifacts" / "injuries"
    if injuries.exists():
        _newest_per_dir(injuries, out / "artifacts" / "injuries", "inj-*.json")
    results = root / "artifacts" / "results"  # referees, top scorers (ADR 0059)
    if results.exists():
        (out / "artifacts" / "results").mkdir(parents=True, exist_ok=True)
        for p in results.glob("*.json"):
            shutil.copy(p, out / "artifacts" / "results" / p.name)
    return sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "dist" / "vercel"))
    files = build(Path(ap.parse_args(argv).out))
    print(f"bundle written: {len(files)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
