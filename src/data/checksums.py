"""Expected-checksum registry CLI.

    python -m src.data.checksums verify
    python -m src.data.checksums pin [--file E0_2324.csv ...] [--status pinned_observed]

`pinned_observed` = sha256 recorded from the file as first observed (detects later drift, does NOT
prove the file equals the provider's original). `verified_official` may only be set when the value
was independently confirmed against the provider; the CLI never sets it implicitly.
"""

import argparse
import json
import sys
from pathlib import Path

from src.cli_utils import configure_output
from src.config import config_dir_for, load_config

from .manifest import ChecksumMismatch, build_manifest
from .raw_validation import sha256_bytes

ROOT = Path(__file__).resolve().parents[2]
STATUSES = ("verified_official", "pinned_observed")


def main(argv: list[str] | None = None) -> int:
    configure_output()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default=str(ROOT))
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("verify")
    pp = sub.add_parser("pin")
    pp.add_argument("--file", nargs="*", default=None)
    pp.add_argument("--status", default="pinned_observed", choices=STATUSES)
    pp.add_argument("--basis", default="observed at pin time; not independently verified")
    a = p.parse_args(argv)
    root = Path(a.root)
    cdir = config_dir_for(root)
    data_cfg = load_config("data", cdir)
    leagues = load_config("leagues", cdir)
    raw_dir = root / data_cfg.raw_dir
    expected_path = root / data_cfg.expected_checksums

    if a.cmd == "verify":
        try:
            m = build_manifest(raw_dir, root / data_cfg.provenance_dir, expected_path, leagues, data_cfg)
        except ChecksumMismatch as e:
            print(f"CHECKSUM MISMATCH: {e}", file=sys.stderr)
            return 2
        unknown = [e.filename for e in m.entries if e.expected_checksum_status == "unknown"]
        print(f"{len(m.entries)} files verified; unknown expected checksum: {unknown or 'none'}")
        return 1 if unknown else 0

    current = {}
    if expected_path.exists():
        current = json.loads(expected_path.read_text(encoding="utf-8")).get("files", {})
    names = a.file or sorted(p.name for p in raw_dir.glob("*.csv"))
    for name in names:
        digest = sha256_bytes((raw_dir / name).read_bytes())
        if name in current and current[name]["sha256"] != digest:
            print(f"refusing to re-pin {name}: differs from recorded checksum", file=sys.stderr)
            return 2
        current.setdefault(name, {"sha256": digest, "status": a.status, "basis": a.basis})
    expected_path.parent.mkdir(parents=True, exist_ok=True)
    expected_path.write_text(
        json.dumps({"schema": 1, "files": dict(sorted(current.items()))}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"pinned {len(names)} files -> {expected_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
