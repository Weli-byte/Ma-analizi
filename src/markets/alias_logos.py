"""`python -m src.markets.alias_logos --stats DIR --out DIR`: download the club logos named in the Highlightly store.

Standard library only (it runs on a GitHub runner without the project's dependencies). Logos are club trademarks:
they are used for the owner's LOCAL alias review (identification only, Highlightly terms 6.3), kept in the private data
repository and never published. A logo that cannot be downloaded is skipped and reported.
"""

import argparse
import json
import urllib.request
from pathlib import Path

LOGO = "https://highlightly.net/soccer/images/teams/{}.png"


def team_ids(stats_dir: Path) -> set[int]:
    ids: set[int] = set()
    for p in sorted(stats_dir.glob("*_*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                ids.update((r["home"]["id"], r["away"]["id"]))
    return ids


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stats", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    ok, failed = 0, []
    for tid in sorted(team_ids(Path(a.stats))):
        dest = out / f"{tid}.png"
        if dest.exists():
            ok += 1
            continue
        try:
            with urllib.request.urlopen(LOGO.format(tid), timeout=20) as r:  # noqa: S310 - fixed https host
                dest.write_bytes(r.read())
            ok += 1
        except Exception as e:  # noqa: BLE001 - report and continue
            failed.append((tid, type(e).__name__))
    print(json.dumps({"downloaded_or_present": ok, "failed": failed[:20], "n_failed": len(failed)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
