"""Copy the cached public-domain source files of the league-model programme into an archive directory (ADR 0063
decision 3), with a sha256 manifest, so exact reproduction does not depend on the public repositories' current bytes.

    python scripts/archive_league_sources.py --out <empty-dir>

Reads artifacts/markets/_cache/{openfootball,openfootball_cups}; writes the files under
`league_models/source_snapshot/` plus `MANIFEST.sha256`. It never uploads anything: the caller commits the directory
to the PRIVATE data repository. Files pinned by docs/league_models/snapshot_manifest.json are checked against it."""

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "artifacts" / "markets" / "_cache"
DIRS = ("openfootball", "openfootball_cups")
TARGET = Path("league_models") / "source_snapshot"


def collect(root: Path = ROOT) -> list[Path]:
    files = []
    for d in DIRS:
        files += sorted(p for p in (root / "artifacts" / "markets" / "_cache" / d).rglob("*") if p.is_file())
    return files


def archive(out: Path, root: Path = ROOT) -> dict:
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} is not empty: refusing to mix archives")
    files = collect(root)
    if not files:
        raise SystemExit("no cached source files found: nothing to archive")
    lines = []
    for f in files:
        rel = f.relative_to(root / "artifacts" / "markets" / "_cache").as_posix()
        dest = out / TARGET / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(f, dest)
        digest = hashlib.sha256(dest.read_bytes()).hexdigest()
        if digest != hashlib.sha256(f.read_bytes()).hexdigest():
            raise RuntimeError(f"copy of {rel} differs from the source")
        lines.append(f"{digest}  {TARGET.as_posix()}/{rel}")
    (out / TARGET / "MANIFEST.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    pinned = root / "docs" / "league_models" / "snapshot_manifest.json"
    check = {"pinned_files": 0, "pinned_and_matching": 0, "pinned_missing_or_different": []}
    if pinned.is_file():
        by_rel = {ln.split("  ", 1)[1].split("source_snapshot/", 1)[1]: ln.split("  ", 1)[0] for ln in lines}
        for item in json.loads(pinned.read_text(encoding="utf-8"))["files"]:
            check["pinned_files"] += 1
            rel = item["path"].split("_cache/", 1)[1]
            if by_rel.get(rel) == item["sha256"]:
                check["pinned_and_matching"] += 1
            else:
                check["pinned_missing_or_different"].append(item["path"])
    return {"files": len(files), "bytes": sum(f.stat().st_size for f in files), **check}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, help="empty directory to write the archive into")
    res = archive(Path(ap.parse_args(argv).out))
    sys.stdout.write(json.dumps(res, indent=2) + "\n")
    return 0 if not res["pinned_missing_or_different"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
