"""Goal-market report: label audit, probability consistency, live baselines, stored development-fold evidence (ADR 0088).

`python -m src.goalmkts.report --markets-root <markets-data checkout> --out docs/goalmkts/sprint10_goal_markets.md`

A function of the stored data and the committed league-programme files (`docs/league_models/s4c_metrics.json`, `s4c_paired.json`, which are READ, never recomputed here: the frozen holdout of the
programme is consumed and not touched).
"""

import argparse
import json
from pathlib import Path

from src.cli_utils import configure_output
from src.markets.track import wilson

from .consistency import audit
from .live import HIST_BTTS_RATE, HIST_OVER_RATE, build_rows, evaluate

REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs" / "league_models"
NAMES = {"over_2_5": "over/under 2.5", "btts": "both teams to score"}


def _row(name: str, s: dict) -> str:
    return f"| {name} | {s['n']} | {s['log_loss']:.4f} | {s['brier']:.4f} | {100 * s['accuracy']:.1f} | {100 * s['mean_prediction']:.1f} | {100 * s['observed_rate']:.1f} |"


def _pair(name: str, p: dict) -> str:
    return f"| {name} | {p['delta']:+.4f} | {p['ci95'][0]:+.4f} to {p['ci95'][1]:+.4f} | {p['blocks']} | {p['verdict']} |"


def historical_section() -> list[str]:
    metrics = json.loads((DOCS / "s4c_metrics.json").read_text(encoding="utf-8"))
    paired = json.loads((DOCS / "s4c_paired.json").read_text(encoding="utf-8"))
    lines = ["## Development-fold evidence (league programme, stored, 10,712 matches, no live row, holdout untouched)", "",
             "Mean log loss per candidate (lower is better); *train rate* is the constant prevalence of the training rows of each fold.", "",
             "| target | candidate | log loss | Brier | accuracy % | log loss minus train rate |", "|---|---|---|---|---|---|"]  # fmt: skip
    for t in ("over_under", "btts"):
        ref = metrics[t]["train_rate"]["ALL"]["log_loss"]
        for name in ("train_rate", "production_global", "B_dc_league", "P_dc_sep", "H_dc_hier", "G_base", "G_extra", "P_gbm_sep"):
            m = metrics[t][name]["ALL"]
            lines.append(f"| {t} | {name} | {m['log_loss']:.4f} | {m['brier']:.4f} | {100 * m['accuracy']:.1f} | {m['log_loss'] - ref:+.4f} |")
    lines += ["", "Paired block-bootstrap results already stored (all competitions, `ALL`):", "", "| target | candidate | reference | delta | 95 % interval | verdict |", "|---|---|---|---|---|---|"]
    for p in paired:
        if p["group"] == "ALL" and p["target"] in ("over_under", "btts") and (p["candidate"], p["reference"]) in (("H_dc_hier", "production_global"), ("G_base", "production_global"), ("H_dc_hier", "B_dc_league"), ("P_gbm_sep", "P_dc_sep")):
            lines.append(f"| {p['target']} | {p['candidate']} | {p['reference']} | {p['delta']:+.4f} | {p['ci95'][0]:+.4f} to {p['ci95'][1]:+.4f} | {p['verdict']} |")
    lines += ["", "No paired interval against the *train rate* is stored (the row-level losses are not kept); only the means above, so a gap to the prevalence baseline is a point estimate here."]
    return lines


def build(rows: list[dict], rep: dict, ev: dict, cons: dict, generated: str) -> str:
    lines = [
        "# Sprint 10 - over/under 2.5 and both teams to score (ADR 0088)", "", f"{generated} · `python -m src.goalmkts.report`.", "",
        "## Label contract (read from the code, not assumed)",
        "- Goals are the **90-minute** score: football-data.org `regularTime` when a match went beyond 90 minutes (`live_results.score_90`), openfootball cup lines use the 90-minute figure and an extra-time result without it is unusable, the odds-provider scores and the ESPN backfill are accepted only in the league phase / as 90-minute scores. Awarded and abandoned matches are not in the record.",
        "- Over 2.5 = home + away goals > 2; BTTS yes = both sides scored. Exact score is the single most probable cell of the score grid (not optimised).", "",
        "## Probability consistency of the published forecasts",
        f"- Artifacts checked: **{cons['artifacts']}** (unreadable {cons['unreadable']}; with a stored total-goals distribution {cons['with_total_distribution']}); artifacts with at least one violated invariant: **{cons['with_violation']}**" + (f" {cons['by_invariant']}" if cons["by_invariant"] else "") + ".",
        "- Invariants: probabilities in [0, 1]; 1X2 (model and headline) sums to 1; over + under = 1 on every line; BTTS yes + no = 1; double chance and draw-no-bet follow from the headline; over lines fall as the line rises; scorelines sorted and summing to at most 1; the total-goals distribution sums to 1 and gives the stored over 2.5.", "",
        "## Live record versus trivial baselines",
        f"- Settled matches {rep['settled']}, no forecast before kick-off {rep['no_forecast_before_kickoff']}, **used {rep['used']}**, of which with the experimental forecast {rep['with_experimental']}.",
    ]  # fmt: skip
    if ev.get("status") == "TOO_FEW_ROWS":
        return "\n".join([*lines, "", f"Only {ev['n']} rows: no comparison is reported.", ""])
    lines.append(f"- Leagues: {', '.join(f'{k} {v}' for k, v in sorted(ev['leagues'].items(), key=lambda kv: -kv[1]))}.")
    for t, e in ev["targets"].items():
        hits = round(e["rate_live"] * ev["n"])
        lo, hi = wilson(hits, ev["n"])
        hist = HIST_OVER_RATE if t == "over_2_5" else HIST_BTTS_RATE
        lines += [
            "", f"### {NAMES[t]}", "",
            f"- Realised rate in the live record: **{100 * e['rate_live']:.1f} %** (95 % interval {100 * lo:.1f}-{100 * hi:.1f} %); pooled rate of the development seasons: {100 * hist:.1f} %. "
            + ("The live rate is **outside** the historical rate's neighbourhood: this early-season sample is unusually high-scoring, so \"always over\" / \"always yes\" looks strong here and is not a stable benchmark." if not (lo <= hist <= hi) else "The live rate is compatible with the historical rate."),
            f"- Accuracy of the trivial strategies on the live record: always {'over' if t == 'over_2_5' else 'yes'} {100 * e['always_over_or_yes_accuracy']:.1f} %, always {'under' if t == 'over_2_5' else 'no'} {100 * e['always_under_or_no_accuracy']:.1f} %.", "",
            "| candidate | n | log loss | Brier | accuracy % | mean prediction % | observed % |", "|---|---|---|---|---|---|---|",
            _row("production forecast", e["all_rows"]["production"]), _row("prevalence, historical constant", e["all_rows"]["prevalence_history"]), _row("prevalence, live expanding (as of each match)", e["all_rows"]["prevalence_live_expanding"]), "",
            "| paired log-loss difference | delta | 95 % interval | weeks | verdict |", "|---|---|---|---|---|",
            _pair("production minus prevalence (historical)", e["paired_all_rows"]["production_vs_prevalence_history"]), _pair("production minus prevalence (live expanding)", e["paired_all_rows"]["production_vs_prevalence_live"]),
        ]  # fmt: skip
        if "experimental_rows" in e:
            lines += ["", f"Rows that carry the experimental forecast ({ev['n_experimental']}):", "", "| candidate | n | log loss | Brier | accuracy % | mean prediction % | observed % |", "|---|---|---|---|---|---|---|",
                      _row("production forecast", e["experimental_rows"]["production"]), _row("experimental (hierarchical)", e["experimental_rows"]["experimental"]), _row("prevalence, historical constant", e["experimental_rows"]["prevalence_history"]), "",
                      "| paired log-loss difference | delta | 95 % interval | weeks | verdict |", "|---|---|---|---|---|",
                      _pair("experimental minus production", e["paired_experimental_rows"]["experimental_vs_production"]), _pair("experimental minus prevalence (historical)", e["paired_experimental_rows"]["experimental_vs_prevalence_history"]),
                      _pair("production minus prevalence (historical)", e["paired_experimental_rows"]["production_vs_prevalence_history"])]  # fmt: skip
    lines += ["", *historical_section(), "",
              "## Reading",
              "- Accuracy at a 0.5 threshold is the plan's secondary metric; log loss and Brier decide. Mean prediction versus observed rate shows whether a forecast is shifted (calibration in the large) on this sample.",
              "- The live record spans only a few ISO weeks. With fewer than 8 weekly blocks the paired intervals are not usable and the verdict reads `TOO_FEW_WEEKLY_BLOCKS`; the point estimates are shown for orientation only.",
              "- Bookmaker-implied over/under: **not available** (the collector stores 1X2 only), so that baseline is `BLOCKED`, not approximated.",
              "- Bivariate Poisson, negative binomial and direct gradient-boosting classifiers: the league programme already tested a learned-variance hierarchical goal model, a wider variance grid (Sprint 4d, no gain) and direct classifiers (`P_gbm_sep`, `G_base`, `G_extra`) on the development folds; a bivariate Poisson or negative binomial candidate was **not added** in this sprint (it needs a row-level re-run of the nested harness on the CI runner).",
              "- Nothing here changes a served number: the experimental card keeps its own pre-registered live gate (ADR 0073).", ""]  # fmt: skip
    return "\n".join(lines)


def main(argv=None) -> int:
    configure_output()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--markets-root", required=True)
    ap.add_argument("--generated", default="generated from the stored data")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    rows, rep = build_rows(a.markets_root)
    text = build(rows, rep, evaluate(rows), audit(a.markets_root), a.generated)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text + "\n", encoding="utf-8", newline="\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
