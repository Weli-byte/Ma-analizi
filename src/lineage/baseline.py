"""The plan's 138-match reference measurement, frozen and re-verifiable (ADR 0091).

The plans quote 1X2 94/138, over/under 84/138, BTTS 76/138, exact score 10/138. They were the first 138 settled matches by date in the live record of 2026-10-27 (reproduced exactly in
ADR 0085). Later settlements (the Super Lig results added from ESPN, ADR 0090) put more matches on those dates, so "the first 138 by date" is no longer the same set. This module freezes the
set itself: `freeze` writes the 138 fixtures and their outcomes from that record, with the record's SHA-256; `verify` finds the same fixtures in any later record and checks that every
pick, result and hit is unchanged (published forecasts are immutable, so they must be). VERIFIED_BASELINE is true only when all 138 are found and identical.

    python -m src.lineage.baseline freeze --track <track_record.json of 2026-10-27> --out docs/phase3/s13_baseline_manifest.json
    python -m src.lineage.baseline verify --manifest docs/phase3/s13_baseline_manifest.json --track <current track_record.json>
"""

import argparse
import hashlib
import json
from pathlib import Path

from src.cli_utils import configure_output

FIELDS = ("result", "pick", "pick_hit", "ou_pick", "ou_hit", "btts_pick", "btts_hit", "score_pick", "score_hit", "score")
N = 138


def freeze(track_path: Path) -> dict:
    raw = Path(track_path).read_bytes()
    track = json.loads(raw)
    rows = sorted(track["matches"], key=lambda x: (x["date"], x["key"]))[:N]
    totals = {m: sum(1 for r in rows if r[m]) for m in ("pick_hit", "ou_hit", "btts_hit", "score_hit")}
    return {"schema": "baseline-manifest-v1", "source_generated_at": track["generated_at"], "source_sha256": hashlib.sha256(raw).hexdigest(), "n": len(rows), "totals": totals,
            "always_over": sum(1 for r in rows if r["over_actual"]), "fixtures": [r["key"] for r in rows], "outcomes": [[r[f] for f in FIELDS] for r in rows]}  # fmt: skip


def verify(manifest: dict, track: dict) -> dict:
    now = {m["key"]: m for m in track["matches"]}
    missing, changed = [], []
    for fx, expected in zip(manifest["fixtures"], manifest["outcomes"], strict=True):
        cur = now.get(fx)
        if cur is None:
            missing.append(fx)
        elif [cur[f] for f in FIELDS] != expected:
            changed.append(fx)
    ok = not missing and not changed and len(manifest["fixtures"]) == N
    return {"VERIFIED_BASELINE": ok, "n": len(manifest["fixtures"]), "missing": missing, "changed": changed, "totals": manifest["totals"], "always_over": manifest["always_over"],
            "current_record_generated_at": track["generated_at"]}  # fmt: skip


def main(argv=None) -> int:
    configure_output()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=("freeze", "verify"))
    ap.add_argument("--track", required=True)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    if a.action == "freeze":
        m = freeze(Path(a.track))
        Path(a.out).write_text(json.dumps(m, indent=1) + "\n", encoding="utf-8", newline="\n")
        print(json.dumps({k: v for k, v in m.items() if k not in ("fixtures", "outcomes")}))
        return 0
    res = verify(json.loads(Path(a.manifest).read_text(encoding="utf-8")), json.loads(Path(a.track).read_text(encoding="utf-8")))
    print(json.dumps(res, indent=1))
    return 0 if res["VERIFIED_BASELINE"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
