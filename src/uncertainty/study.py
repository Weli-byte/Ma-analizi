"""Sprint 16 study: thresholds from history, the coverage-risk curve on the untouched season and on the live record, tier evidence, flags and exact-score top-k (ADR 0095).

    python -m src.uncertainty.study --oof <oof rows .jsonl> --markets-root <markets-data checkout> --json-out docs/phase3/s16_selective.json --out docs/phase3/sprint16_report.md

The JSON (thresholds + the live evidence per bucket) is what the serving layer reads; it is regenerated, never edited by hand.
"""

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from src.cli_utils import configure_output
from src.markets.track import load_artifacts, wilson

from . import selective
from .measures import from_intel

OUTCOME = {"H": 0, "D": 1, "A": 2}


def live_rows(markets_root) -> list[dict]:
    root = Path(markets_root)
    track = json.loads((root / "artifacts" / "markets" / "track_record.json").read_text(encoding="utf-8"))
    arts = load_artifacts(root)
    out = []
    for m in track["matches"]:
        cand = arts.get(m["key"], [])
        if not cand:
            continue
        kick = datetime.fromisoformat(cand[0]["kickoff_utc"])
        before = [a for a in cand if datetime.fromisoformat(a["information_cutoff"]) < kick]
        if not before:
            continue
        a = max(before, key=lambda x: x["information_cutoff"])
        u = from_intel(a["intelligence"])
        out.append({"key": m["key"], "league": a["league"], "y": OUTCOME[m["result"]], "score": m["score"], "u": u, "scorelines": [s["score"] for s in a["intelligence"]["scorelines"]]})
    return out


def study(oof_rows: list[dict], live: list[dict]) -> dict:
    hist = [r for r in oof_rows if r["season"] in selective.HISTORY_SEASONS]
    test = [r for r in oof_rows if r["season"] == selective.TEST_SEASON]
    cuts = selective.thresholds([max(r["p"]) for r in hist])
    res = {"thresholds": cuts, "history_rows": len(hist), "test_rows": len(test), "live_rows": len(live),
           "test_curve": selective.curve([max(r["p"]) for r in test], [r["p"] for r in test], [r["y"] for r in test], cuts),
           "live_curve": selective.curve([r["u"]["model_top_probability"] for r in live], [r["u"]["model_probs"] for r in live], [r["y"] for r in live], cuts)}  # fmt: skip
    tiers = {}
    for name in ("40", "60", "80", "abstain"):
        rs = [r for r in live if selective.bucket(r["u"]["model_top_probability"], cuts) == name]
        hits = sum(1 for r in rs if max(range(3), key=lambda j: r["u"]["model_probs"][j]) == r["y"])
        n = len(rs)
        tiers[name] = {"n": n, "hit_rate": round(hits / n, 4) if n else None, "ci95": [round(x, 4) for x in wilson(hits, n)] if n else None,
                       "mean_stated": round(sum(r["u"]["model_top_probability"] for r in rs) / n, 4) if n else None,
                       "evidence": "INSUFFICIENT_EVIDENCE" if n < selective.MIN_TIER_EVIDENCE else "MEASURED"}  # fmt: skip
    res["live_tiers"] = tiers
    top40 = [r for r in live if selective.bucket(r["u"]["model_top_probability"], cuts) == "40"]
    res["live_top40_by_league"] = dict(Counter(r["league"] for r in top40))
    flags = {}
    for name, f in (("with exact odds", lambda r: r["u"]["has_exact_odds"]), ("model only (no exact odds)", lambda r: not r["u"]["has_exact_odds"]), ("low history", lambda r: r["u"]["low_history"])):
        rs = [r for r in live if f(r)]
        hits = sum(1 for r in rs if max(range(3), key=lambda j: r["u"]["model_probs"][j]) == r["y"])
        flags[name] = {"n": len(rs), "accuracy": round(hits / len(rs), 4) if rs else None}
    res["live_flags"] = flags
    ex = {}
    for k in (1, 3, 5):
        hits = sum(1 for r in live if r["score"] in r["scorelines"][:k])
        ex[f"top{k}"] = {"hits": hits, "n": len(live), "rate": round(hits / len(live), 4) if live else None}
    res["exact_score_topk"] = ex
    return res


def report(res: dict, generated: str) -> str:
    def rows(curve):
        return [f"| {c['level']} % | {c['threshold']:.3f} | {c['answered']} | {100 * c['coverage']:.1f} | {100 * c['accuracy']:.1f} ({100 * c['accuracy_ci95'][0]:.1f}-{100 * c['accuracy_ci95'][1]:.1f}) | {c['log_loss']:.4f} |" if c["answered"] else f"| {c['level']} % | {c['threshold']:.3f} | 0 | 0 | - | - |" for c in curve]

    head = ["| target coverage | top-probability threshold | answered | realised coverage % | accuracy % (95 %) | log loss |", "|---|---|---|---|---|---|"]
    lines = ["# Sprint 16 - uncertainty and selective prediction (ADR 0095)", "", f"{generated} · `python -m src.uncertainty.study`.", "",
             f"Thresholds from the out-of-fold predictions of 2020-21 to 2022-23 ({res['history_rows']} matches), fixed before the test season and the live record were scored.", "",
             f"## Untouched season 2023-24 (out-of-fold, model only, {res['test_rows']} matches)", "", *head, *rows(res["test_curve"]), "",
             f"## Live prospective record (the model's own 1X2, {res['live_rows']} settled matches)", "", *head, *rows(res["live_curve"]), "",
             "The accuracy at a lower coverage is the accuracy of the ANSWERED matches only; the 100 % row is every match. An abstained match stays in the coverage denominator.", "",
             "## Confidence buckets shown to users (live evidence)", "", "| bucket | matches | mean stated % | hit rate % (95 %) | evidence |", "|---|---|---|---|---|"]  # fmt: skip
    for name, t in res["live_tiers"].items():
        label = {"40": "among the 40 % most confident", "60": "40-60 %", "80": "60-80 %", "abstain": "least confident 20 % (abstain)"}[name]
        lines.append(f"| {label} | {t['n']} | {100 * t['mean_stated']:.1f} | {100 * t['hit_rate']:.1f} ({100 * t['ci95'][0]:.1f}-{100 * t['ci95'][1]:.1f}) | {t['evidence']} |" if t["n"] else f"| {label} | 0 | - | - | {t['evidence']} |")
    lines += ["", f"Top-40 % bucket by league (live): {', '.join(f'{k} {v}' for k, v in sorted(res['live_top40_by_league'].items(), key=lambda kv: -kv[1]))}. A league with a handful of matches says nothing on its own.", "",
              "## Data flags (live)", "", *[f"- {k}: {v['n']} matches, accuracy {100 * v['accuracy']:.1f} %" if v["n"] else f"- {k}: no match" for k, v in res["live_flags"].items()], "",
              "## Exact score (live): the score is a low-frequency event; the single most likely cell is rarely right", "", *[f"- {k}: {v['hits']}/{v['n']} ({100 * v['rate']:.1f} %)" for k, v in res["exact_score_topk"].items()], ""]  # fmt: skip
    return "\n".join(lines)


def main(argv=None) -> int:
    from src.calibsel.oof import read

    configure_output()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--oof", required=True)
    ap.add_argument("--markets-root", required=True)
    ap.add_argument("--json-out", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--generated", default="generated from the stored data")
    a = ap.parse_args(argv)
    res = study(read(Path(a.oof)), live_rows(a.markets_root))
    Path(a.json_out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.json_out).write_text(json.dumps({"generated": a.generated, **{k: v for k, v in res.items() if k in ("thresholds", "live_tiers", "history_rows", "live_rows")}}, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    text = report(res, a.generated)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8", newline="\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
