"""The sprint 17 experiment matrix (ADR 0096): specs registered first, results recorded once.

    python -m src.experiments.lab register                       # writes every spec (pre-registration), computes nothing
    python -m src.experiments.lab build-oof --out <oof .jsonl>        # out-of-fold rows 2020-24 with expected goals and fit time (heavy: runs in the cloud, experiments-lab.yml)
    python -m src.experiments.lab run --oof-rates <oof .jsonl with expected goals> --markets-root <markets-data checkout>
    python -m src.experiments.lab report                          # docs/phase3/sprint17_report.md from the recorded results

Champion: the production market model `markets-1.0.0` (frozen in `docs/phase3/experiments/champion.json`). Every comparison uses the same fixtures and the same cutoffs for champion and
candidate. Intervals: the project's block bootstrap over ISO weeks, alpha divided by the number of experiments of the same family (Bonferroni). Naive baselines (home win, always over,
always yes, prevalence) are part of the matrix. A track without the data or the budget is recorded as NOT_RUN with its reason; a mock result never enters.
"""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from src.cli_utils import configure_output
from src.league_models import bench_metrics as bm

from . import gates
from .registry import BASE, Spec, load_all, record_result, register

REPO = Path(__file__).resolve().parents[2]
HISTORY = ("2020-21", "2021-22", "2022-23")
TEST = ("2023-24",)
BOOT, SEED = 2000, 20261101

SPECS = (
    Spec("S17-E01-champion-vs-naive-oof", "The champion beats the naive baselines on the untouched 2023-24 season", "RETROSPECTIVE_OOF", "1x2, over/under 2.5, BTTS", "champion (model only)",
         ("prevalence of 2020-23 (1X2 shares, over rate, BTTS rate)", "always home", "always over", "always yes"), window="2023-24 out-of-fold", min_practical_effect=0.0,
         decision_rule="CHAMPION_BEATS_BASELINE when the Bonferroni interval (3 markets) of champion minus prevalence is wholly below 0", data_requirements=("OOF rows 2020-24",)),
    Spec("S17-E02-champion-vs-naive-live", "The published forecasts beat the naive baselines on the live record", "PROSPECTIVE_LIVE", "1x2, over/under 2.5, BTTS", "champion (published headline)",
         ("prevalence of 2020-23", "always home", "always over", "always yes"), window="live record from 2026-10-09", min_practical_effect=0.0,
         decision_rule="CHAMPION_BEATS_BASELINE when the Bonferroni interval is wholly below 0 and at least 8 weekly blocks exist; TOO_FEW_WEEKLY_BLOCKS otherwise"),
    Spec("S17-E03-calibration", "A calibrator improves the champion", "STORED_EVIDENCE", "1x2, over/under 2.5, BTTS", "temperature / Platt / isotonic / vector / Dirichlet / league-level", ("identity champion",),
         window="fit 2020-22, select 2022-23, test 2023-24 (ADR 0089)", data_requirements=("docs/calibsel/sprint11_evaluation_manifest.json",)),
    Spec("S17-E04-odds", "Mixing the model into the de-vigged market improves the 1X2 headline", "STORED_EVIDENCE", "1x2", "convex blend / power-devig consensus", ("market weight 1.0 (production)",),
         window="131 live rows (ADR 0087)", data_requirements=("docs/oddsens/sprint09_market_vs_model.md",)),
    Spec("S17-E05-league-models", "League-specific or hierarchical models replace the global model", "STORED_EVIDENCE", "1x2, over/under 2.5, BTTS", "league programme candidates", ("global model",),
         window="development folds and the one-shot holdout (ADR 0063-0071)", data_requirements=("docs/league_models/s5_approval_decisions.json", "docs/league_models/s4c_paired.json")),
    Spec("S17-E06-bivariate-poisson", "A bivariate Poisson score grid beats the champion's Dixon-Coles grid", "RETROSPECTIVE_OOF", "1x2, over/under 2.5, BTTS", "bivariate Poisson (shape fitted on 2020-23)",
         ("champion Dixon-Coles grid",), window="2023-24 out-of-fold", data_requirements=("OOF rows with expected goals",)),
    Spec("S17-E07-negative-binomial", "Negative binomial margins beat the champion's Dixon-Coles grid", "RETROSPECTIVE_OOF", "1x2, over/under 2.5, BTTS", "negative binomial (dispersion fitted on 2020-23)",
         ("champion Dixon-Coles grid",), window="2023-24 out-of-fold", data_requirements=("OOF rows with expected goals",)),
    Spec("S17-E08-xg", "xG features improve the champion", "NOT_RUN", "1x2, over/under 2.5, BTTS", "+xG (ADR 0085)", ("champion",), data_requirements=("1,500 matches with complete as-of xG", "Highlightly PRO for product use")),
    Spec("S17-E09-lineups", "Line-up features improve the champion", "NOT_RUN", "1x2, over/under 2.5, BTTS", "+line-ups (ADR 0086)", ("champion",), data_requirements=("800 fixtures with complete as-of line-up features",)),
    Spec("S17-E10-llm", "An LLM forecast or commentary improves the champion", "NOT_RUN", "1x2", "LLM providers (ADR 0024 / 0026)", ("champion",), data_requirements=("real authorised calls with recorded cost; the owner's budget is 0",)),
    Spec("S17-E11-stacking", "A trained stacker over market and model improves the headline", "NOT_RUN", "1x2", "OOF logistic stacker (ADR 0087)", ("market weight 1.0",), data_requirements=("600 rows with three or more bookmakers",)),
)
NOT_RUN_REASON = {
    "S17-E08-xg": "data gate not met: 49 of 1,500 matches with complete as-of xG when ADR 0085 was written; the free plan is research only",
    "S17-E09-lineups": "data gate not met: 0 of 800 fixtures with complete as-of line-up features when ADR 0086 was written",
    "S17-E10-llm": "NOT_CONFIGURED: real calls cost money and the owner's budget is 0; mock or replayed answers never enter the comparison",
    "S17-E11-stacking": "data gate not met: 131 of 600 rows with three or more bookmakers when ADR 0087 was written; odds collection is paused (quota)",
}


def _ll(target: str, p, y) -> np.ndarray:
    p, y = np.asarray(p, float), np.asarray(y)
    if target == "1x2":
        return -np.log(np.clip(p[np.arange(len(y)), y], 1e-12, 1))
    q = np.clip(p, 1e-12, 1 - 1e-12)
    return -(y * np.log(q) + (1 - y) * np.log(1 - q))


def _paired(a: np.ndarray, b: np.ndarray, kickoffs: list[str], alpha: float) -> dict:
    blocks = [bm.iso_week_block(datetime.fromisoformat(k)) for k in kickoffs]
    d = bm.paired_block_bootstrap(a, b, blocks, BOOT, SEED, alpha)
    return {"delta": float(d.delta), "interval": [float(d.lower), float(d.upper)], "blocks": int(d.n_blocks), "alpha": alpha}


def _prevalence(hist: list[dict]) -> dict:
    n = len(hist)
    return {"1x2": [sum(r["y"] == k for r in hist) / n for k in range(3)], "over_2_5": sum(r["o"] for r in hist) / n, "btts": sum(r["b"] for r in hist) / n}


def champion_vs_naive_oof(rows: list[dict]) -> dict:
    hist = [r for r in rows if r["season"] in HISTORY]
    test = [r for r in rows if r["season"] in TEST]
    prev = _prevalence(hist)
    k = [r["kickoff"] for r in test]
    out = {"n": len(test), "prevalence": prev, "markets": {}, "leakage": gates.leakage_oof(rows, HISTORY, TEST)}
    for t, pk, yk in (("1x2", "p", "y"), ("over_2_5", "po", "o"), ("btts", "pb", "b")):
        y = [r[yk] for r in test]
        champ = _ll(t, [r[pk] for r in test], y)
        base = _ll(t, [prev[t]] * len(test), y)
        out["markets"][t] = {"champion_log_loss": float(champ.mean()), "prevalence_log_loss": float(base.mean()), "paired": _paired(champ, base, k, 0.05 / 3)}
    hits = {"always_home": float(np.mean([r["y"] == 0 for r in test])), "always_over": float(np.mean([r["o"] for r in test])), "always_yes": float(np.mean([r["b"] for r in test])),
            "champion_1x2": float(np.mean([int(np.argmax(r["p"])) == r["y"] for r in test]))}  # fmt: skip
    out["accuracy"] = hits
    out["decision"] = "CHAMPION_BEATS_BASELINE" if all(m["paired"]["interval"][1] < 0 for m in out["markets"].values()) else "NOT_DEMONSTRATED_FOR_EVERY_MARKET"
    out["per_market_decision"] = {t: ("BEATS_PREVALENCE" if m["paired"]["interval"][1] < 0 else "NOT_DEMONSTRATED") for t, m in out["markets"].items()}
    return out


def champion_vs_naive_live(markets_root, prev: dict) -> dict:
    from src.markets.track import load_artifacts

    root = Path(markets_root)
    track = json.loads((root / "artifacts" / "markets" / "track_record.json").read_text(encoding="utf-8"))
    arts = load_artifacts(root)
    rows = []
    for m in track["matches"]:
        cand = arts.get(m["key"], [])
        if not cand:
            continue
        kick = datetime.fromisoformat(cand[0]["kickoff_utc"])
        before = [a for a in cand if datetime.fromisoformat(a["information_cutoff"]) < kick]
        if not before:
            continue
        i = max(before, key=lambda x: x["information_cutoff"])["intelligence"]
        h, a = (int(x) for x in m["score"].split("-"))
        rows.append({"kickoff": kick.isoformat(), "y": 0 if h > a else 1 if h == a else 2, "o": int(h + a > 2), "b": int(h > 0 and a > 0),
                     "p": [i["result"]["headline_probs"][x] for x in ("home", "draw", "away")], "po": next(x["over"] for x in i["goals"]["over_under"] if x["line"] == 2.5), "pb": i["goals"]["btts_yes"]})  # fmt: skip
    out = {"n": len(rows), "markets": {}, "leakage": gates.leakage_live(arts)}
    k = [r["kickoff"] for r in rows]
    for t, pk, yk in (("1x2", "p", "y"), ("over_2_5", "po", "o"), ("btts", "pb", "b")):
        y = [r[yk] for r in rows]
        champ, base = _ll(t, [r[pk] for r in rows], y), _ll(t, [prev[t]] * len(rows), y)
        p = _paired(champ, base, k, 0.05 / 3)
        verdict = "TOO_FEW_WEEKLY_BLOCKS" if p["blocks"] < 8 else ("BEATS_PREVALENCE" if p["interval"][1] < 0 else "NOT_DEMONSTRATED")
        out["markets"][t] = {"champion_log_loss": float(champ.mean()), "prevalence_log_loss": float(base.mean()), "paired": p, "verdict": verdict}
    out["accuracy"] = {"always_home": float(np.mean([r["y"] == 0 for r in rows])), "always_over": float(np.mean([r["o"] for r in rows])), "always_yes": float(np.mean([r["b"] for r in rows])),
                       "champion_1x2": float(np.mean([int(np.argmax(r["p"])) == r["y"] for r in rows]))}  # fmt: skip
    out["decision"] = "TOO_FEW_WEEKLY_BLOCKS" if any(m["verdict"] == "TOO_FEW_WEEKLY_BLOCKS" for m in out["markets"].values()) else ("CHAMPION_BEATS_BASELINE" if all(m["verdict"] == "BEATS_PREVALENCE" for m in out["markets"].values()) else "NOT_DEMONSTRATED_FOR_EVERY_MARKET")
    return out


def goal_family(rows: list[dict], family: str, n_family_experiments: int = 2) -> dict:
    from src.calibsel import goalfamilies as gf
    from src.calibsel import protocol

    hist = [r for r in rows if r["season"] in HISTORY]
    test = [r for r in rows if r["season"] in TEST]
    leak = gates.leakage_oof(rows, HISTORY, TEST)
    shape = gf.fit_shape(hist, family)
    again = gf.fit_shape(hist, family)
    cand, champ = gf.predict(test, family, shape), gf.predict(test, "dc", None)
    k = [r["kickoff"] for r in test]
    out = {"n": len(test), "family": family, "shape_fitted_on_2020_23": shape, "reproduced": shape == again, "leakage": leak, "markets": {}}
    alpha = 0.05 / (3 * n_family_experiments)
    for t, yk in (("1x2", "y"), ("over_2_5", "o"), ("btts", "b")):
        y = np.array([r[yk] for r in test])
        a, b = protocol.losses(t, cand[t], y), protocol.losses(t, champ[t], y)
        leagues = {}
        for lg in sorted({r["league"] for r in test}):
            idx = [i for i, r in enumerate(test) if r["league"] == lg]
            if len(idx) >= gates.MIN_LEAGUE_N:
                leagues[lg] = {"n": len(idx), **_paired(a[idx], b[idx], [k[i] for i in idx], alpha)}
        delta = _paired(a, b, k, alpha)
        ece_d = protocol.ece(t, cand[t], y) - protocol.ece(t, champ[t], y)
        gate = gates.promotion(delta, leagues, ece_d, 1.0, 0, leak["status"], out["reproduced"], 0.002)
        out["markets"][t] = {"candidate_log_loss": float(a.mean()), "champion_log_loss": float(b.mean()), "paired": delta, "ece_delta": float(ece_d), "leagues": leagues, "gate": gate}
    out["decision"] = "PROMOTION_CANDIDATE" if any(m["gate"]["decision"] == "PROMOTION_CANDIDATE" for m in out["markets"].values()) else "NO_GAIN"
    return out


def stored(spec_id: str) -> dict:
    if spec_id == "S17-E03-calibration":
        m = json.loads((REPO / "docs" / "calibsel" / "sprint11_evaluation_manifest.json").read_text(encoding="utf-8"))
        return {"source": "docs/calibsel/sprint11_evaluation_manifest.json", "per_target": {t: {"chosen": r["chosen"], "status": r["status"], "test_paired": r.get("test_paired")} for t, r in m["registry"].items()},
                "decision": "NO_GAIN" if all(r["status"] == "KEEP_CHAMPION" for r in m["registry"].values()) else "SEE_SOURCE"}  # fmt: skip
    if spec_id == "S17-E04-odds":
        return {"source": "ADR 0087, docs/oddsens/sprint09_market_vs_model.md", "selected_market_weight": 1.0, "model_only_minus_market_report_half": 0.0028,
                "power_consensus_minus_production_report_half": -0.0066, "gate": "300 report rows to change the market rule; 65 today", "decision": "NO_GAIN (production already uses the market at weight 1.0; the power-consensus rule waits for its gate)"}  # fmt: skip
    if spec_id == "S17-E05-league-models":
        s5 = json.loads((REPO / "docs" / "league_models" / "s5_approval_decisions.json").read_text(encoding="utf-8"))
        counts = s5["status_counts"]
        return {"source": "docs/league_models/s5_approval_decisions.json (ADR 0063-0071)", "status_counts": counts,
                "decision": "NO_GAIN (no league model approved; the global model stays)" if counts.get("APPROVED_FOR_CANARY", 0) == 0 else "SEE_SOURCE"}
    raise KeyError(spec_id)


def champion(base: Path = BASE) -> dict:
    """Freeze the champion the experiments compare against: model version, the markets config bytes, written once."""
    from src.markets.model import MODEL_VERSION

    p = Path(base) / "champion.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    cfg = (REPO / "configs" / "markets.yaml").read_bytes()
    body = {"model_version": MODEL_VERSION, "markets_config_sha256": hashlib.sha256(cfg).hexdigest(), "calibrator": "identity (ADR 0089)", "headline_1x2": "de-vigged market at weight 1.0 when exact odds exist (ADR 0087)",
            "frozen_at": datetime.now(UTC).isoformat()}  # fmt: skip
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(body, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return body


def register_all(base: Path = BASE) -> dict:
    champion(base)
    return {s.id: register(s, base) for s in SPECS}


def run(oof_rates: Path | None, markets_root: Path | None, base: Path = BASE) -> dict:
    from src.calibsel.oof import read

    register_all(base)
    rows = read(oof_rates) if oof_rates and Path(oof_rates).exists() else None
    done = {}
    for s in SPECS:
        if (base / s.id / "result.json").exists():
            done[s.id] = "ALREADY_RECORDED"
            continue
        if s.track == "NOT_RUN":
            res = {"status": "NOT_RUN", "reason": NOT_RUN_REASON[s.id], "decision": "NOT_RUN"}
        elif s.track == "STORED_EVIDENCE":
            res = {"status": "STORED_EVIDENCE", **stored(s.id)}
        elif s.id == "S17-E01-champion-vs-naive-oof":
            if rows is None:
                done[s.id] = "WAITING_FOR_DATA"
                continue
            res = {"status": "RUN", **champion_vs_naive_oof(rows)}
        elif s.id == "S17-E02-champion-vs-naive-live":
            if rows is None or markets_root is None:
                done[s.id] = "WAITING_FOR_DATA"
                continue
            res = {"status": "RUN", **champion_vs_naive_live(markets_root, _prevalence([r for r in rows if r["season"] in HISTORY]))}
        else:
            if rows is None or "lh" not in rows[0]:
                done[s.id] = "WAITING_FOR_DATA"
                continue
            res = {"status": "RUN", **goal_family(rows, "bivariate" if "bivariate" in s.id else "negbin")}
        done[s.id] = record_result(s, res, base)
    return done


def _fmt(x) -> str:
    return "n/a" if x is None else f"{x:+.4f}" if isinstance(x, float) else str(x)


def report(base: Path = BASE) -> str:
    exps = load_all(base)
    lines = ["# Phase 3 sprint 17 — ablation laboratory and promotion gates (ADR 0096)", "",
             "Every experiment below was registered (spec + SHA-256 in `docs/phase3/experiments/<id>/spec.json`) before its result was computed, and has at most one result. "
             "Deltas are candidate minus reference (negative = candidate better), with block-bootstrap intervals over ISO weeks and Bonferroni-corrected alpha. No experiment promoted anything automatically.", "",
             "| Experiment | Track | Decision |", "|---|---|---|"]  # fmt: skip
    for x in exps:
        r = x["result"] or {}
        lines.append(f"| {x['id']} | {x['spec']['spec']['track']} | {r.get('decision', 'NOT_RECORDED')} |")
    for x in exps:
        r, s = x["result"], x["spec"]["spec"]
        lines += ["", f"## {x['id']}", "", f"Hypothesis: {s['hypothesis']}. Candidate: {s['candidate']}. Baselines: {', '.join(s['baselines'])}. Window: {s['window'] or 'n/a'}.", ""]
        if not r:
            lines.append("No result recorded.")
            continue
        if r.get("status") == "NOT_RUN":
            lines.append(f"NOT_RUN — {r['reason']}.")
            continue
        if r.get("status") == "STORED_EVIDENCE":
            lines.append(f"Stored evidence ({r.get('source')}): " + json.dumps({k: v for k, v in r.items() if k not in ('status', 'source', 'spec_sha256', 'recorded_at', 'result_sha256')}, sort_keys=True, default=str))
            continue
        lines.append(f"n = {r.get('n')}; leakage gate {r.get('leakage', {}).get('status')}.")
        for t, m in (r.get("markets") or {}).items():
            p = m["paired"]
            ref = m.get("prevalence_log_loss", m.get("champion_log_loss"))
            cand = m.get("champion_log_loss") if "prevalence_log_loss" in m else m.get("candidate_log_loss")
            gate = m.get("gate", {}).get("decision") or m.get("verdict") or ""
            lines.append(f"- {t}: log loss {cand:.4f} vs {ref:.4f}; delta {_fmt(p['delta'])} [{_fmt(p['interval'][0])}, {_fmt(p['interval'][1])}], {p['blocks']} weekly blocks, alpha {p['alpha']:.4f} {gate}")
        if r.get("accuracy"):
            lines.append("- accuracy: " + ", ".join(f"{k} {100 * v:.1f} %" for k, v in r["accuracy"].items()))
        if r.get("shape_fitted_on_2020_23") is not None:
            lines.append(f"- shape fitted on 2020-23: {r['shape_fitted_on_2020_23']} (reproduced on a second fit: {r.get('reproduced')})")
    return "\n".join(lines) + "\n"


def build_oof(out: Path) -> dict:
    from src.calibsel import oof

    rows, meta = oof.build_oof(REPO, (*HISTORY, *TEST), with_rates=True)
    oof.write(rows, out)
    return {**meta, "digest": oof.digest(rows), "out": str(out)}


def main(argv=None) -> int:
    configure_output()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=("register", "build-oof", "run", "report"))
    ap.add_argument("--oof-rates", default=None)
    ap.add_argument("--markets-root", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    if a.action == "register":
        print(register_all())
    elif a.action == "build-oof":
        print(json.dumps(build_oof(Path(a.out)), indent=1))
    elif a.action == "run":
        print(json.dumps(run(Path(a.oof_rates) if a.oof_rates else None, Path(a.markets_root) if a.markets_root else None), indent=1))
    else:
        out = Path(a.out) if a.out else REPO / "docs" / "phase3" / "sprint17_report.md"
        out.write_text(report(), encoding="utf-8", newline="\n")
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
