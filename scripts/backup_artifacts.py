"""Backup the irreplaceable state (prediction ledgers, locked stages, odds, live, llm runs, ops log) to a
timestamped zip with a sha256 manifest. `python scripts/backup_artifacts.py [--out DIR]`.
Restore: unzip into the repo root; verify with `--verify FILE`. Never includes `.env`."""

import argparse
import hashlib
import json
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

INCLUDE = ("artifacts", "reports/benchmarks")
SKIP_PARTS = {"__pycache__", ".tmp"}


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def backup(root: Path, out_dir: Path, now: datetime) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"backup-{now.strftime('%Y%m%dT%H%M%SZ')}.zip"
    manifest = {}
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for inc in INCLUDE:
            for f in sorted((root / inc).rglob("*")):
                if f.is_file() and not (SKIP_PARTS & set(f.parts)) and f.name != ".env":
                    rel = f.relative_to(root).as_posix()
                    data = f.read_bytes()
                    z.writestr(rel, data)
                    manifest[rel] = sha(data)
        z.writestr("MANIFEST.json", json.dumps({"created_utc": now.isoformat(), "files": manifest}, indent=1))
    return target


def verify(path: Path) -> list[str]:
    """Return the list of problems (empty = every file matches its recorded hash)."""
    with zipfile.ZipFile(path) as z:
        files = json.loads(z.read("MANIFEST.json"))["files"]
        return [n for n, h in files.items() if n not in z.namelist() or sha(z.read(n)) != h]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--out", default="backups")
    ap.add_argument("--verify", default=None)
    a = ap.parse_args(argv)
    if a.verify:
        bad = verify(Path(a.verify))
        print("OK" if not bad else f"CORRUPT: {bad}")
        return 1 if bad else 0
    t = backup(Path(a.root), Path(a.out), datetime.now(UTC))
    print(f"backup written: {t} (verify: {'OK' if not verify(t) else 'FAILED'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
