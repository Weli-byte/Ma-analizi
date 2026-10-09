"""Out-of-sample evaluation of the markets models (ADR 0041).

`python -m src.markets.evaluate` refits every `refit_block_days` days on matches whose result was already
available at the start of the block and scores the block. Only train + validation seasons are loaded: the
locked final-test seasons are never read. Every market is compared with a naive baseline and the mean loss
difference carries a bootstrap 95% interval; no market is declared a winner.
"""

import argparse
import json
from datetime import timedelta
from pathlib import Path

import numpy as np

from src.config import config_dir_for, load_config
from src.data.dataset import resolve_dataset

from .data import load_stat_matches
from .model import count_pmf, fit_markets, scoreline_matrix
from .predict import devig

ROOT = Path(__file__).resolve().parents[2]
EPS = 1e-12
SEED = 20260925


def _boot(diff: np.ndarray, n: int = 1000) -> list[float]:
    rng = np.random.default_rng(SEED)
    means = [diff[rng.integers(0, len(diff), len(diff))].mean() for _ in range(n)]
    return [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))]


def compare(name: str, model: np.ndarray, base: np.ndarray, note: str = "lower is better") -> dict:
    d = model - base
    lo, hi = _boot(d)
    return {
        "market": name, "n": len(model), "model": float(model.mean()), "baseline": float(base.mean()),
        "model_minus_baseline": float(d.mean()), "ci95": [lo, hi],
        "verdict": "model better" if hi < 0 else "baseline better" if lo > 0 else "no clear difference",
        "note": note,
    }  # fmt: skip


def rps(p: np.ndarray, y: int) -> float:
    cp, cy = np.cumsum(p), np.cumsum(np.eye(3)[y])
    return float(np.sum((cp - cy)[:-1] ** 2) / 2)


def evaluate(root: Path = ROOT) -> dict:
    cdir = config_dir_for(root)
    cfg = load_config("markets", cdir)
    ev = load_config("evaluation", cdir)
    ref = None
    if load_config("markets", cdir).history_source != "openfootball":
        ref = resolve_dataset(root / load_config("data", cdir).processed_dir)
    seasons = [*ev.train_seasons, *ev.validation_seasons]
    if set(seasons) & set(ev.final_test_seasons):
        raise ValueError("final-test seasons must never be loaded for evaluation")
    if cfg.history_source == "openfootball":
        from src.data.teams import TeamDirectory

        from .of_source import load_world

        last = max(int(x[:4]) for x in ev.validation_seasons)
        world = load_world(
            root, TeamDirectory.load(cdir / "team_aliases.yaml"), cfg, end_year=last
        )  # final-test seasons are not even downloaded
        ms = [m for m in world.history if m.season in seasons]
        ref_version = world.data_version
    else:
        ms = load_stat_matches(ref, seasons)
        ref_version = ref.data_version
    val = [m for m in ms if m.season in ev.validation_seasons]
    t0 = min(m.kickoff_utc for m in val)
    t_end = max(m.kickoff_utc for m in val)
    rows = []
    start = t0
    while start <= t_end:
        end = start + timedelta(days=cfg.refit_block_days)
        block = [m for m in val if start <= m.kickoff_utc < end]
        if block:
            mm = fit_markets(ms, start, cfg)
            for m in block:
                rows.append((m, mm))
        start = end
    L = {k: [] for k in (
        "score_ll", "score_ll_b", "r_ll", "r_ll_b", "r_ll_mkt", "r_br", "r_br_b", "r_rps", "r_rps_b",
        "o25_ll", "o25_ll_b", "btts_ll", "btts_ll_b",
    )}  # fmt: skip
    cnt = {s: {"nll": [], "nll_b": [], "ae": [], "ae_b": [], "ou": {}} for s in cfg.count_stats}
    mkt = []  # (season, model probs, market probs, outcome) for the blend
    base_rate = {}
    for lg in {m.league for m in ms}:
        tr = [m for m in ms if m.league == lg and m.season in ev.train_seasons]
        base_rate[lg] = {
            "o25": np.mean([m.home_goals + m.away_goals > 2 for m in tr]),
            "btts": np.mean([m.home_goals > 0 and m.away_goals > 0 for m in tr]),
            "res": np.bincount([0 if m.home_goals > m.away_goals else 1 if m.home_goals == m.away_goals else 2 for m in tr], minlength=3) / len(tr),
        }  # fmt: skip
    for m, mm in rows:
        lh, la = mm.goals.lambdas(m.home_id, m.away_id, m.league)
        mat = scoreline_matrix(lh, la, mm.rho, cfg.max_goals)
        x, y = min(m.home_goals, cfg.max_goals), min(m.away_goals, cfg.max_goals)
        bmat = scoreline_matrix(mm.goals.mu_home[m.league], mm.goals.mu_away[m.league], 0.0, cfg.max_goals)
        L["score_ll"].append(-np.log(max(mat[x, y], EPS)))
        L["score_ll_b"].append(-np.log(max(bmat[x, y], EPS)))
        i, j = np.indices(mat.shape)
        p = np.array([mat[i > j].sum(), mat[i == j].sum(), mat[i < j].sum()])
        out = 0 if m.home_goals > m.away_goals else 1 if m.home_goals == m.away_goals else 2
        pb = base_rate[m.league]["res"]
        L["r_ll"].append(-np.log(max(p[out], EPS)))
        L["r_ll_b"].append(-np.log(max(pb[out], EPS)))
        L["r_br"].append(float(np.sum((p - np.eye(3)[out]) ** 2)))
        L["r_br_b"].append(float(np.sum((pb - np.eye(3)[out]) ** 2)))
        L["r_rps"].append(rps(p, out))
        L["r_rps_b"].append(rps(pb, out))
        if m.closing_avg is not None:
            q = np.array(devig(m.closing_avg))
            L["r_ll_mkt"].append(-np.log(max(q[out], EPS)))
            mkt.append((m.season, p, q, out))
        over = float(mat[(i + j) > 2].sum())
        o = int(m.home_goals + m.away_goals > 2)
        po = base_rate[m.league]["o25"]
        L["o25_ll"].append(-np.log(max(over if o else 1 - over, EPS)))
        L["o25_ll_b"].append(-np.log(max(po if o else 1 - po, EPS)))
        bt = float(mat[1:, 1:].sum())
        b = int(m.home_goals > 0 and m.away_goals > 0)
        pbt = base_rate[m.league]["btts"]
        L["btts_ll"].append(-np.log(max(bt if b else 1 - bt, EPS)))
        L["btts_ll_b"].append(-np.log(max(pbt if b else 1 - pbt, EPS)))
        for stat, table in mm.counts.items():
            hs, as_ = m.home_stats[stat], m.away_stats[stat]
            if hs is None or as_ is None:
                continue
            ch, ca = table.lambdas(m.home_id, m.away_id, m.league)
            pmf = np.convolve(count_pmf(ch, table.alpha, 60), count_pmf(ca, table.alpha, 60))
            bpmf = np.convolve(
                count_pmf(table.mu_home[m.league], table.alpha, 60),
                count_pmf(table.mu_away[m.league], table.alpha, 60),
            )
            tot = min(hs + as_, 120)
            c = cnt[stat]
            c["nll"].append(-np.log(max(pmf[min(tot, len(pmf) - 1)], EPS)))
            c["nll_b"].append(-np.log(max(bpmf[min(tot, len(bpmf) - 1)], EPS)))
            c["ae"].append(abs(ch + ca - (hs + as_)))
            c["ae_b"].append(abs(table.mu_home[m.league] + table.mu_away[m.league] - (hs + as_)))
            for ln in cfg.over_under_lines.get(stat, []):
                k = int(np.floor(ln)) + 1
                po_m, po_b = float(pmf[k:].sum()), float(bpmf[k:].sum())
                yv = float(hs + as_ >= k)
                d = c["ou"].setdefault(ln, ([], []))
                d[0].append((po_m - yv) ** 2)
                d[1].append((po_b - yv) ** 2)
    A = {k: np.array(v) for k, v in L.items()}
    results = [
        compare("correct score (log score, bits-free nats)", A["score_ll"], A["score_ll_b"]),
        compare("1X2 log loss vs league base rate", A["r_ll"], A["r_ll_b"]),
        compare("1X2 Brier vs league base rate", A["r_br"], A["r_br_b"]),
        compare("1X2 RPS vs league base rate", A["r_rps"], A["r_rps_b"]),
        compare("over/under 2.5 log loss vs base rate", A["o25_ll"], A["o25_ll_b"]),
        compare("BTTS log loss vs base rate", A["btts_ll"], A["btts_ll_b"]),
    ]
    for stat, c in cnt.items():
        if not c["nll"]:
            continue
        results.append(compare(f"{stat} total: NLL vs league mean", np.array(c["nll"]), np.array(c["nll_b"])))
        results.append(
            compare(f"{stat} total: abs error vs league mean", np.array(c["ae"]), np.array(c["ae_b"]))
        )
        for ln, (a, b) in c["ou"].items():
            results.append(compare(f"{stat} over/under {ln} Brier vs league mean", np.array(a), np.array(b)))
    # headline blend: weight chosen on the first validation season, reported on the second
    blend = {}
    if mkt:
        first = ev.validation_seasons[0]
        grid = np.linspace(0, 1, 21)

        def ll(w: float, sub) -> float:
            return float(np.mean([-np.log(max(((1 - w) * p + w * q)[o], EPS)) for _, p, q, o in sub]))

        calib = [r for r in mkt if r[0] == first]
        report = [r for r in mkt if r[0] != first] or calib  # a single validation season: report where chosen
        best = float(min(grid, key=lambda w: ll(w, calib)))
        blend = {
            "chosen_on": first, "weight_market": best, "reported_on": sorted({r[0] for r in report}),
            "log_loss_model_only": ll(0.0, report), "log_loss_market_only": ll(1.0, report),
            "log_loss_blend": ll(best, report), "n_report": len(report),
            "curve_on_calibration": {f"{w:.2f}": ll(float(w), calib) for w in grid[::4]},
        }  # fmt: skip
    return {
        "data_version": ref_version, "history_source": cfg.history_source, "validation_seasons": ev.validation_seasons,
        "final_test_seasons_loaded": False, "n_matches": len(rows), "refit_block_days": cfg.refit_block_days,
        "market_1x2_log_loss_reference": float(np.mean(A["r_ll_mkt"])) if len(A["r_ll_mkt"]) else None,
        "results": results, "blend": blend,
    }  # fmt: skip


def to_markdown(rep: dict) -> str:
    lines = [
        f"# Markets evaluation ({rep['data_version']})", "",
        f"Validation {rep['validation_seasons']}, {rep['n_matches']} matches, refit every {rep['refit_block_days']} days; "
        "final-test seasons NOT loaded. Lower loss is better; no market is declared a winner.", "",
        "| market | n | model | baseline | model - baseline | 95% CI | verdict |", "|---|---|---|---|---|---|---|",
    ]  # fmt: skip
    for r in rep["results"]:
        lines.append(
            f"| {r['market']} | {r['n']} | {r['model']:.4f} | {r['baseline']:.4f} | {r['model_minus_baseline']:+.4f} | "
            f"[{r['ci95'][0]:+.4f}, {r['ci95'][1]:+.4f}] | {r['verdict']} |"
        )
    b = rep["blend"]
    if b:
        lines += [
            "", f"## Headline 1X2: model vs market vs blend (reported on {b['reported_on']}, n={b['n_report']})",
            f"- weight on the de-vigged closing market chosen on {b['chosen_on']}: **{b['weight_market']:.2f}**",
            f"- log loss model only {b['log_loss_model_only']:.4f}, market only {b['log_loss_market_only']:.4f}, "
            f"blend {b['log_loss_blend']:.4f}",
        ]  # fmt: skip
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT))
    a = ap.parse_args(argv)
    rep = evaluate(Path(a.root))
    out = Path(a.root) / "artifacts" / "markets" / "evaluation"
    out.mkdir(parents=True, exist_ok=True)
    (out / "evaluation.json").write_text(json.dumps(rep, indent=2), encoding="utf-8")
    (out / "evaluation.md").write_text(to_markdown(rep), encoding="utf-8")
    print(to_markdown(rep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
