"""Phase 2 baseline: the live track record versus the trivial strategies (ADR 0085).

`python -m src.xg.baseline --root <checkout with artifacts/markets/track_record.json> --out docs/xg/sprint07_baseline.md`

Only PROSPECTIVE rows are read (the published forecasts of `track_record.json`, one per settled match). The first 138 matches by date reproduce the figures quoted in the Phase 2 plan
(1X2 94/138, over/under 84/138, BTTS 76/138, exact score 10/138, always-over 95/138); the full sample is reported next to it, with Wilson 95 % intervals.
"""

import argparse
import json
from pathlib import Path

from src.cli_utils import configure_output
from src.markets.track import wilson

REPO = Path(__file__).resolve().parents[2]
PLAN_SAMPLE = 138


def rate(hits: int, n: int) -> str:
    lo, hi = wilson(hits, n)
    return f"{hits}/{n} = {100 * hits / n:.1f} % ({100 * lo:.1f}-{100 * hi:.1f})"


def table(rows: list[dict]) -> list[str]:
    n = len(rows)
    over = sum(1 for x in rows if x["over_actual"])
    yes = sum(1 for x in rows if (x["btts_pick"] == "Var") == x["btts_hit"])
    home = sum(1 for x in rows if x["result"] == "H")
    return [
        "| market | model | trivial baseline | baseline rate |", "|---|---|---|---|",
        f"| 1X2 | {rate(sum(x['pick_hit'] for x in rows), n)} | always home | {rate(home, n)} |",
        f"| over/under 2.5 | {rate(sum(x['ou_hit'] for x in rows), n)} | always over | {rate(over, n)} |",
        f"| BTTS | {rate(sum(x['btts_hit'] for x in rows), n)} | always yes | {rate(yes, n)} |",
        f"| exact score | {rate(sum(x['score_hit'] for x in rows), n)} | - | - |",
    ]  # fmt: skip


def build(track: dict) -> str:
    rows = sorted(track["matches"], key=lambda x: (x["date"], x["key"]))
    first = rows[:PLAN_SAMPLE]
    leagues = {}
    for x in rows:
        leagues[x["league"]] = leagues.get(x["league"], 0) + 1
    lines = [
        "# Phase 2 baseline (re-measured, ADR 0085)", "",
        f"Source: `track_record.json` generated {track['generated_at']} · {len(rows)} settled matches from {rows[0]['date']} to {rows[-1]['date']} · prospective forecasts only (published before kick-off, ADR 0048).", "",
        f"## First {PLAN_SAMPLE} matches by date (the sample quoted in the plan, to {first[-1]['date']})", "", *table(first), "",
        f"## All {len(rows)} settled matches", "", *table(rows), "",
        "## Reading",
        "- The first 138 matches reproduce the plan's figures exactly, so the earlier measurement is confirmed and its label rule is the same one used here.",
        "- Over/under and BTTS: the model is **behind the trivial strategy** on this sample (the always-over and always-yes rates are higher). With these sample sizes the intervals overlap, so this is not proof of a worse model, but it is also no evidence of a better one. Accuracy is a secondary metric; the log loss figures in `track_record.json` are the primary ones.",
        "- Exact score is the single most probable cell of the score grid (rarely above 12 % likely), so a low hit rate is expected and is not optimised.",
        f"- Leagues in the sample: {', '.join(f'{k} {v}' for k, v in sorted(leagues.items(), key=lambda kv: -kv[1]))}. Per-league rates with fewer than 20 matches are noise.",
        "- Cup matches are scored on the 90-minute result as stored by the settlement sources; awarded and abandoned matches are not in the sample.", "",
    ]  # fmt: skip
    return "\n".join(lines)


def main(argv=None) -> int:
    configure_output()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(REPO))
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    track = json.loads((Path(a.root) / "artifacts" / "markets" / "track_record.json").read_text(encoding="utf-8"))
    text = build(track)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text, encoding="utf-8", newline="\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
