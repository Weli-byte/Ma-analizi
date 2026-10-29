"""Sprint 11 report: calibration candidates, the chronological selection, confidence tiers and the prospective reliability of the user-facing labels (ADR 0089).

    python -m src.calibsel.report --build-oof --oof <path.jsonl> --markets-root <markets-data checkout> --out-dir docs/calibsel

`--build-oof` refits the market model walk-forward on the open history (about five minutes, seasons 2020-21 to 2023-24; the final-test seasons are refused) and writes the rows; without it an
existing OOF file is read. The output is the markdown report and an immutable evaluation manifest / calibrator registry (JSON).
"""

import argparse
import json
import subprocess
from pathlib import Path

from src.cli_utils import configure_output

from . import oof as oofmod
from . import protocol
from .labels import reliability

REPO = Path(__file__).resolve().parents[2]
NAMES = {"1x2": "1X2", "over_2_5": "over/under 2.5", "btts": "both teams to score"}


def _git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30, cwd=REPO).stdout.strip() or "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def _m(s: dict) -> str:
    return f"{s['log_loss']:.4f} | {s['brier']:.4f} | {s['ece']:.4f}"


def build(rows: list[dict], res: dict, tiers: list[dict], labels: dict | None, meta: dict) -> str:
    lines = [
        "# Sprint 11 - calibration, uncertainty and model selection (ADR 0089)", "",
        f"OOF rows: {len(rows)} (digest `{meta['oof_digest'][:16]}`, history `{meta.get('data_version', '?')}`, {meta.get('fits', '?')} walk-forward fits, refit every {meta.get('refit_block_days', '?')} days) · git `{meta['git_sha'][:12]}` · `python -m src.calibsel.report`.", "",
        "## Split (by season, never mixed)", "",
        "| window | seasons | rows | use |", "|---|---|---|---|",
        *[f"| {k} | {', '.join(v['seasons'])} | {v['rows']} | {u} |" for (k, v), u in zip(res["windows"].items(), ("every candidate is fitted", "the candidate is chosen under the gate", "read once, chosen calibrator vs identity only"), strict=True)],
        "",
        "Rows are out-of-fold predictions of the production market model (model only, no odds) for the six domestic forecast leagues; the model is refitted every block on matches finished before it. The final-test seasons of `configs/evaluation.yaml` (2024-25, 2025-26) are refused by the code and never downloaded. The 2023-24 season was already used to REPORT the market blend in ADR 0041; it is untouched for calibration decisions, not pristine in general.",
        f"League-level calibrators were tried for the leagues with at least {protocol.MIN_LEAGUE_ROWS} fit + select rows: {', '.join(res['league_calibrator_leagues']) or 'none'} (the rest use the global calibrator).", "",
    ]  # fmt: skip
    for t, e in res["targets"].items():
        lines += [f"## {NAMES[t]}", "", "Select window (2022-23), every candidate fitted on 2020-22:", "", "| candidate | log loss | Brier | ECE |", "|---|---|---|---|"]
        for k, v in e["select_window"].items():
            lines.append(f"| {k} | {_m(v)} |" if "log_loss" in v else f"| {k} | {v['status']} | | |")
        si = e["test_identity"].get("slope_intercept") or e["test_identity"].get("slope_intercept_home")
        lines += ["", f"- Gate passed on the select window: **{e['gate_passed']}**; chosen: **{e['chosen']}** (refitted on 2020-23).",
                  f"- Test 2023-24, read once: identity {_m(e['test_identity'])}; chosen {_m(e['test_chosen'])} (log loss | Brier | ECE)."]  # fmt: skip
        if "test_paired" in e:
            p = e["test_paired"]
            lines.append(f"- Paired log-loss difference chosen minus identity: {p['delta']:+.4f}, interval {p['interval'][0]:+.4f} to {p['interval'][1]:+.4f} at alpha {p['alpha']:.4f} (0.05 divided by the {e['candidates_tried']} candidates tried), {p['blocks']} weekly blocks.")
        lines += [f"- Calibration of the uncalibrated champion on the test season: slope {si[0]:.3f}, intercept {si[1]:+.3f} (1 and 0 = calibrated" + ("; home-win probability" if t == "1x2" else "") + ").",
                  f"- **Status: {e['status']}**.", ""]  # fmt: skip
    lines += ["## Confidence tiers of the champion (1X2, test season 2023-24, model only)", "", "| top-pick probability | matches | share % | mean stated % | hit rate % | 95 % interval |", "|---|---|---|---|---|---|"]
    for r in tiers:
        if r["n"]:
            lines.append(f"| {r['tier']} | {r['n']} | {100 * r['coverage']:.1f} | {100 * r['mean_stated']:.1f} | {100 * r['hit_rate']:.1f} | {100 * r['ci95'][0]:.1f}-{100 * r['ci95'][1]:.1f} |")
    lines += ["", "Abstention is a separate product metric: an accuracy on the picks above a threshold is never mixed with the accuracy on all matches.", ""]
    if labels:
        lines += [f"## Prospective reliability of the labels users see (live record, {labels['matches']} settled matches)", "",
                  "| label | market | tips | mean stated % | hit rate % | 95 % interval | verdict |", "|---|---|---|---|---|---|---|"]  # fmt: skip
        for c in labels["cells"]:
            lines.append(f"| {c['label']} | {c['market']} | {c['n']} | {100 * c['mean_stated']:.1f} | {100 * c['hit_rate']:.1f} | {100 * c['ci95'][0]:.1f}-{100 * c['ci95'][1]:.1f} | {c['verdict']} |")
        lines += ["", "CONSISTENT = the hit-rate interval contains the mean stated probability. The record spans only a few weeks of one early season; a label verdict is a first read, not a guarantee.", ""]
    lines += ["## Decision", "",
              "- No calibrator is served. A candidate becomes a `SHADOW_CANDIDATE` only with a test interval wholly below 0 (alpha divided by the candidates tried) and no worse Brier or ECE; even then serving it needs its own ADR and a shadow period.",
              "- Every target cleared the gate on 2022-23, none was confirmed on 2023-24: for 1X2 and over/under the chosen calibrator was slightly worse than the identity, for BTTS slightly better but inside its interval. A gain chosen on one season did not carry over to the next, the winner's-curse risk the plan warns about.", ""]  # fmt: skip
    return "\n".join(lines)


def main(argv=None) -> int:
    configure_output()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--oof", required=True)
    ap.add_argument("--build-oof", action="store_true")
    ap.add_argument("--markets-root", default=None, help="markets-data checkout for the label reliability (optional)")
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args(argv)
    meta: dict = {}
    sidecar = Path(a.oof + ".meta.json")
    if a.build_oof:
        rows, meta = oofmod.build_oof(REPO, ("2020-21", "2021-22", "2022-23", "2023-24"))
        oofmod.write(rows, Path(a.oof))
        sidecar.write_text(json.dumps(meta, sort_keys=True), encoding="utf-8")
    elif sidecar.exists():
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
    rows = oofmod.read(Path(a.oof))
    meta |= {"oof_digest": oofmod.digest(rows), "git_sha": _git_sha()}
    res = protocol.select_and_test(rows)
    tiers = protocol.confidence_tiers(protocol.window(rows, "test"))
    labels = reliability(a.markets_root) if a.markets_root else None
    text = build(rows, res, tiers, labels, meta)
    manifest = {"oof": meta, "windows": res["windows"], "gate": {"min_gain": protocol.GATE_MIN_GAIN, "ece_tolerance": protocol.GATE_ECE_TOLERANCE, "min_league_rows": protocol.MIN_LEAGUE_ROWS},
                "test_rows_read_once": res["test_rows_read_once"], "registry": {t: {"chosen": e["chosen"], "status": e["status"], "calibrator": e["registry"], "test_paired": e.get("test_paired")} for t, e in res["targets"].items()}}  # fmt: skip
    if a.out_dir:
        d = Path(a.out_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / "sprint11_calibration_and_selection.md").write_text(text + "\n", encoding="utf-8", newline="\n")
        (d / "sprint11_evaluation_manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True, default=float) + "\n", encoding="utf-8", newline="\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
