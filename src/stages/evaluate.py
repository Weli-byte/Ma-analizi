"""Prospective stage comparison on the SAME fixtures (ADR 0093).

Only settled fixtures that have an OK record at every stage are compared, so each stage is scored on exactly the same matches. Per stage: completeness (share of due fixtures with a
forecast at that stage), mean staleness, 1X2 log loss / Brier / accuracy on the headline, over 2.5 and BTTS log loss; paired 1X2 log-loss difference of each stage against the kickoff
stage with the project's block bootstrap over ISO weeks. Fewer than MIN_BLOCKS weekly blocks: the verdict is TOO_FEW_WEEKLY_BLOCKS and only point estimates are shown.

    python -m src.stages.evaluate --root <markets-data checkout> --out docs/phase3/sprint15_stage_report.md
"""

import argparse
import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

from src.cli_utils import configure_output
from src.league_models import bench_metrics as bm

from .freeze import STAGES

MIN_BLOCKS = 8
EPS = 1e-12


def load_records(root: Path) -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = defaultdict(dict)
    base = Path(root) / "artifacts" / "stage_records"
    for f in sorted(base.glob("*__*__*/*.json")) if base.exists() else []:
        r = json.loads(f.read_text(encoding="utf-8"))
        prev = out[r["fixture"]].get(r["stage"])
        if prev is None or r["kickoff_utc"] > prev["kickoff_utc"]:  # the current schedule's record
            out[r["fixture"]][r["stage"]] = r
    return out


def evaluate(root: Path) -> dict:
    recs = load_records(root)
    track = json.loads((Path(root) / "artifacts" / "markets" / "track_record.json").read_text(encoding="utf-8"))
    score = {m["key"]: m["score"] for m in track["matches"]}
    stages = [s for s, _ in STAGES]
    completeness = {s: {"due": sum(1 for f in recs.values() if s in f), "ok": sum(1 for f in recs.values() if f.get(s, {}).get("status") == "OK")} for s in stages}
    paired = [k for k, f in recs.items() if k in score and all(f.get(s, {}).get("status") == "OK" for s in stages)]
    out = {"fixtures_with_records": len(recs), "completeness": completeness, "paired_fixtures": len(paired), "stages": {}, "paired_vs_kickoff": {}}
    if not paired:
        out["status"] = "NO_PAIRED_FIXTURES"
        return out
    losses = {}
    for s in stages:
        ll, br, acc, lo, lb, st = [], [], [], [], [], []
        for k in paired:
            r = recs[k][s]
            h, a = (int(x) for x in score[k].split("-"))
            y = 0 if h > a else 1 if h == a else 2
            p = [r["probabilities"]["headline_1x2"][x] for x in ("home", "draw", "away")]
            ll.append(-math.log(max(p[y], EPS)))
            br.append(sum((p[i] - (1 if i == y else 0)) ** 2 for i in range(3)))
            acc.append(int(max(range(3), key=lambda i: p[i]) == y))
            po, pb = r["probabilities"]["over_2_5"], r["probabilities"]["btts_yes"]
            lo.append(-math.log(max(po if h + a > 2 else 1 - po, EPS)))
            lb.append(-math.log(max(pb if h > 0 and a > 0 else 1 - pb, EPS)))
            st.append(r["staleness_minutes"])
        losses[s] = np.array(ll)
        out["stages"][s] = {"n": len(paired), "log_loss_1x2": float(np.mean(ll)), "brier_1x2": float(np.mean(br)), "accuracy_1x2": float(np.mean(acc)), "log_loss_over_2_5": float(np.mean(lo)),
                            "log_loss_btts": float(np.mean(lb)), "mean_staleness_minutes": float(np.mean(st)),
                            "lineup_confirmed_share": float(np.mean([recs[k][s]["lineup_status"] == "CONFIRMED" for k in paired]))}  # fmt: skip
    blocks = [bm.iso_week_block(datetime.fromisoformat(recs[k]["kickoff"]["kickoff_utc"])) for k in paired]
    for s in stages[:-1]:
        d = bm.paired_block_bootstrap(losses[s], losses["kickoff"], blocks, 2000, 20261031, 0.05)
        verdict = "TOO_FEW_WEEKLY_BLOCKS" if d.n_blocks < MIN_BLOCKS else ("BETTER" if d.upper < 0 else "WORSE" if d.lower > 0 else "NO_DIFFERENCE_DEMONSTRATED")
        out["paired_vs_kickoff"][s] = {"delta": float(d.delta), "ci95": [float(d.lower), float(d.upper)], "blocks": int(d.n_blocks), "verdict": verdict}
    out["status"] = "MEASURED"
    return out


def report(res: dict, generated: str) -> str:
    lines = ["# Sprint 15 - stage records and the paired stage comparison (ADR 0093)", "", f"{generated} · `python -m src.stages.evaluate`.", "",
             f"Fixtures with stage records: {res['fixtures_with_records']}; settled fixtures with an OK record at every stage (the paired set): **{res['paired_fixtures']}**.", "",
             "| stage | records | OK |", "|---|---|---|", *[f"| {s} | {c['due']} | {c['ok']} |" for s, c in res["completeness"].items()], ""]  # fmt: skip
    if res.get("status") != "MEASURED":
        return "\n".join([*lines, "No paired fixture yet: no stage comparison.", ""])
    lines += ["| stage | n | 1X2 log loss | 1X2 Brier | 1X2 accuracy % | O/U 2.5 log loss | BTTS log loss | mean staleness (min) | XI confirmed % |", "|---|---|---|---|---|---|---|---|---|"]
    for s, m in res["stages"].items():
        lines.append(f"| {s} | {m['n']} | {m['log_loss_1x2']:.4f} | {m['brier_1x2']:.4f} | {100 * m['accuracy_1x2']:.1f} | {m['log_loss_over_2_5']:.4f} | {m['log_loss_btts']:.4f} | {m['mean_staleness_minutes']:.0f} | {100 * m['lineup_confirmed_share']:.0f} |")
    lines += ["", "| stage minus kickoff (1X2 log loss) | delta | 95 % interval | weeks | verdict |", "|---|---|---|---|---|"]
    lines += [f"| {s} | {p['delta']:+.4f} | {p['ci95'][0]:+.4f} to {p['ci95'][1]:+.4f} | {p['blocks']} | {p['verdict']} |" for s, p in res["paired_vs_kickoff"].items()]
    lines += ["", "Staleness = minutes between the stage cutoff and the forecast that existed then: the forecasts are published by the four-hourly job, so a late stage often still holds an earlier forecast.", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    configure_output()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", required=True)
    ap.add_argument("--generated", default="generated from the stored records")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    text = report(evaluate(Path(a.root)), a.generated)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8", newline="\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
