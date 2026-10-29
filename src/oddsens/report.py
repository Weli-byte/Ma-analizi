"""Model versus market report on the stored prospective rows (ADR 0087).

`python -m src.oddsens.report --markets-root <markets-data checkout> --odds-root <odds-data checkout> --out docs/oddsens/sprint09_market_vs_model.md`

A function of the stored data. It states plainly when a difference is inside its bootstrap interval, that the odds are 1X2 only (no over/under quotes are collected) and that the free plan's
quota is exhausted, so the sample grows slowly.
"""

import argparse
from pathlib import Path

import numpy as np

from src.cli_utils import configure_output

from .consensus import MAX_STALENESS, MIN_BOOKS
from .dataset import build_rows

REPO = Path(__file__).resolve().parents[2]


def _fmt(m: dict) -> str:
    return f"{m['log_loss']:.4f} | {m['brier']:.4f} | {m['rps']:.4f} | {100 * m['accuracy']:.1f} | {m['ece']:.3f}"


def _verdict(p: dict) -> str:
    lo, hi = p["ci95"]
    return "better, interval excludes 0" if hi < 0 else "worse, interval excludes 0" if lo > 0 else "inside the interval (no evidence either way)"


def build(rows: list[dict], rep: dict, ev: dict, generated: str) -> str:
    lines = [
        "# Sprint 9 - market versus model on prospective rows (ADR 0087)", "",
        f"{generated} · source: stored exact-timestamp 1X2 quotes of The Odds API (cloud collector) and the published forecasts of the live track record · `python -m src.oddsens.report`.", "",
        "## Rows",
        f"- Settled matches in the live record: {rep['settled']}; no forecast before kick-off: {rep['no_forecast_before_kickoff']}; no stored odds: {rep['no_odds_store']}; fewer than {rep['min_books']} usable bookmakers at the forecast cutoff: {rep['fewer_than_min_books']}; **used: {rep['used']}**.",
        f"- A bookmaker counts only with a complete H/D/A triple from one moment (10 min), fetched and stamped by the provider at or before the forecast cutoff and at most {int(MAX_STALENESS.total_seconds() / 3600)} h old. The market is rebuilt as of that cutoff; closing odds are not used.",
    ]  # fmt: skip
    if ev.get("status") == "TOO_FEW_ROWS":
        return "\n".join([*lines, "", f"Only {ev['n']} rows: no evaluation is reported.", ""])
    books = [r["n_books"] for r in rows]
    stale = [r["staleness_min"] for r in rows]
    diff = [float(np.abs(r["market"]["normalization"] - r["p_stored_market"]).max()) for r in rows if r["p_stored_market"] is not None]
    lines += [
        f"- Bookmakers per row: median {int(np.median(books))}, min {min(books)}, max {max(books)}; mean staleness {np.mean(stale):.0f} min; rows with a Pinnacle triple: {ev['sharp_available']}; leagues: {', '.join(f'{k} {v}' for k, v in sorted(ev['leagues'].items(), key=lambda kv: -kv[1]))}.",
        f"- Sanity: the rebuilt consensus differs from the market probability stored in the published forecast by at most {max(diff):.3f} (mean of the per-row maxima {np.mean(diff):.3f}) over {len(diff)} rows." if diff else "- No stored market probability to compare with.", "",
        f"## Chronological split: {ev['n_selection']} rows select, {ev['n_report']} rows report", "",
        f"- Vig removal chosen on the selection half: **{ev['selected_method']}** (log loss " + ", ".join(f"{k} {v:.4f}" for k, v in ev["method_log_loss_selection"].items()) + ").",
        f"- Convex blend weight of the market chosen on the selection half: **w = {ev['selected_weight']}** (0 = model only, 1 = market only).", "",
        "### Report half (not used for any choice)", "", "| candidate | log loss | Brier | RPS | accuracy % | ECE |", "|---|---|---|---|---|---|",
        *[f"| {k} | {_fmt(v)} |" for k, v in ev["report"].items()], "",
        "Paired log-loss difference against *market only (normalization)*, negative = better, 95 % bootstrap interval over matches:", "",
        "| candidate | mean difference | 95 % interval | reading |", "|---|---|---|---|",
        *[f"| {k} | {p['mean']:+.4f} | {p['ci95'][0]:+.4f} to {p['ci95'][1]:+.4f} | {_verdict(p)} |" for k, p in ev["report_paired_vs_market"].items()], "",
        "### All rows (descriptive, selection and report mixed: no decision is taken from this table)", "", "| candidate | log loss | Brier | RPS | accuracy % | ECE |", "|---|---|---|---|---|---|",
        *[f"| {k} | {_fmt(v)} |" for k, v in ev["all_rows_descriptive"].items()], "",
        "| candidate | mean difference vs market | 95 % interval | reading |", "|---|---|---|---|",
        *[f"| {k} | {p['mean']:+.4f} | {p['ci95'][0]:+.4f} to {p['ci95'][1]:+.4f} | {_verdict(p)} |" for k, p in ev["all_rows_paired_vs_market"].items()], "",
        "## Reading",
        "- The production 1X2 headline already uses the de-vigged market with weight 1.0 whenever exact odds exist (`market_blend_weight`, ADR 0041). What differs is the market itself: production takes the newest complete triple of ONE bookmaker (Pinnacle wins ties) and normalises it; the candidates here average all usable bookmakers and may use another vig removal.",
        f"- Selected market-only candidate against the production headline (report half): {ev['report_paired_vs_production']['mean']:+.4f}, 95 % interval {ev['report_paired_vs_production']['ci95'][0]:+.4f} to {ev['report_paired_vs_production']['ci95'][1]:+.4f} ({_verdict(ev['report_paired_vs_production'])}); all rows, descriptive: {ev['all_paired_vs_production']['mean']:+.4f} ({ev['all_paired_vs_production']['ci95'][0]:+.4f} to {ev['all_paired_vs_production']['ci95'][1]:+.4f}).",
        f"- Gate to replace the production market rule (pre-registered, ADR 0087): at least {ev['switch_gate']['needed']} report-half rows, an interval wholly below 0 against the production headline and no worse Brier. Report rows today: {ev['switch_gate']['report_rows']} → **{ev['switch_gate']['status']}**, production unchanged.",
        "- A negative difference of the blend or the model against the market that stays inside its interval is no evidence; the production rule changes only after the pre-registered gate of ADR 0087 is met.",
        f"- Trained stacking (OOF logistic): **{ev['stacking']['status']}**" + (f" - {ev['stacking']['reason']}." if "reason" in ev["stacking"] else "."),
        "- Over/under 2.5 and BTTS: no market quotes for them are collected (only 1X2), so no odds-aware ensemble is possible for those markets; collecting totals would cost further credits of a quota that is exhausted.",
        "- Accuracy is secondary; log loss and Brier decide. ECE with this few rows is noise.", "",
    ]  # fmt: skip
    return "\n".join(lines)


def main(argv=None) -> int:
    configure_output()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--markets-root", required=True)
    ap.add_argument("--odds-root", required=True)
    ap.add_argument("--min-books", type=int, default=MIN_BOOKS)
    ap.add_argument("--generated", default="")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    rows, rep = build_rows(a.markets_root, a.odds_root, a.min_books)
    from .evaluate import evaluate as run

    text = build(rows, rep, run(rows), a.generated or "generated from the stored data")
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text + "\n", encoding="utf-8", newline="\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
