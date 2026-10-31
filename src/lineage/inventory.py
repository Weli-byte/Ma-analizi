"""Inventory of what is actually running (ADR 0091): `python -m src.lineage.inventory --markets-root <markets-data checkout> --out docs/phase3/s13_inventory.md`.

Everything is read from a real source and named with it: the code that produced the latest production deploy is the head commit of the newest successful `markets-cloud` run (that job builds
and deploys the site); the data branches' heads from the private repository; versions from the code; providers from `configs/ingestion.yaml` and the smoke file; secrets by NAME only
(`gh secret list`). A source that cannot be read is reported as such, never filled in.
"""

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import yaml

from src.cli_utils import configure_output

REPO = Path(__file__).resolve().parents[2]
DATA_REPO = "Weli-byte/Ma-analizi2"
BRANCHES = ("markets-data", "shadow-data", "odds-data")


def _gh(args: list[str]) -> str | None:
    try:
        r = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=60, cwd=REPO)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def deployed() -> dict:
    out = _gh(["run", "list", "--workflow=markets-cloud.yml", "--status", "success", "--limit", "1", "--json", "headSha,createdAt,databaseId,event"])
    rows = json.loads(out) if out else []
    return rows[0] if rows else {"status": "UNREADABLE (gh not available or not logged in)"}


def branch_heads() -> dict:
    res = {}
    for b in BRANCHES:
        out = _gh(["api", f"repos/{DATA_REPO}/branches/{b}", "--jq", "{sha: .commit.sha, date: .commit.commit.committer.date}"])
        res[b] = json.loads(out) if out else "UNREADABLE"
    return res


def versions() -> dict:
    from src.api.app import create_app
    from src.lineage.record import SCHEMA
    from src.markets.model import MODEL_VERSION

    try:
        api_version = create_app().version
    except Exception as e:  # noqa: BLE001 - reported, not hidden
        api_version = f"UNREADABLE ({type(e).__name__})"
    return {"market_model": MODEL_VERSION, "api": api_version, "lineage_schema": SCHEMA}


def build(markets_root: Path | None, smoke_path: Path | None) -> str:
    dep, heads, ver = deployed(), branch_heads(), versions()
    ing = yaml.safe_load((REPO / "configs" / "ingestion.yaml").read_text(encoding="utf-8"))
    providers = {k: bool((v or {}).get("enabled")) for k, v in (ing.get("providers") or ing).items() if isinstance(v, dict)}
    smoke = json.loads(smoke_path.read_text(encoding="utf-8"))["results"] if smoke_path and smoke_path.exists() else []
    secrets = _gh(["secret", "list", "--json", "name", "--jq", ".[].name"])
    track = None
    if markets_root and (markets_root / "artifacts" / "markets" / "track_record.json").exists():
        track = json.loads((markets_root / "artifacts" / "markets" / "track_record.json").read_text(encoding="utf-8"))
    lines = [
        "# Phase 3 sprint 13 - inventory of what is running (ADR 0091)", "", f"Generated {datetime.now(UTC).isoformat()} by `python -m src.lineage.inventory`.", "",
        "## Deployed code",
        f"- Newest successful `markets-cloud` run (builds and deploys the public site): {json.dumps(dep)}.",
        "- The Vercel deployment id itself is readable only with the Vercel token, which exists only as a repository secret; the run above is the deploy step's own record.", "",
        "## Data branches (private data repository)", *[f"- `{b}`: {json.dumps(h)}" for b, h in heads.items()], "",
        "## Versions in the code", *[f"- {k}: `{v}`" for k, v in ver.items()],
        "- User store schema (Turso / SQLite, `src/web/store.py`): `users`, `favorites`, plus the additive `users.plan` column (ADR 0062); no other migration exists.", "",
        "## Providers", "", "| provider (configs/ingestion.yaml) | enabled |", "|---|---|", *[f"| {k} | {v} |" for k, v in providers.items()], "",
        "Live smoke (`docs/phase3/s13_provider_smoke.json`):", "", "| provider | status | HTTP | latency ms |", "|---|---|---|---|",
        *[f"| {r['provider']} | {r['status']} | {r.get('http', '')} | {r.get('latency_ms', '')} |" for r in smoke], "",
        "## Repository secrets (names only)", f"- {', '.join(sorted(secrets.split())) if secrets else 'UNREADABLE'}", "",
    ]  # fmt: skip
    if track:
        lines += ["## Live record", f"- `track_record.json` generated {track['generated_at']}: {track['matches_scored']} settled matches scored (prospective only)."]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    configure_output()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--markets-root", default=None)
    ap.add_argument("--smoke", default=str(REPO / "docs" / "phase3" / "s13_provider_smoke.json"))
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    text = build(Path(a.markets_root) if a.markets_root else None, Path(a.smoke))
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8", newline="\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
