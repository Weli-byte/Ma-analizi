"""Sprint 4d (ADR 0075): does a wider variance grid, or team strength that moves from season to season, improve the hierarchical goal model? Development folds only; the frozen holdout is consumed and is never read.

    python -m src.league_models.s4d_experiment --as-of 2026-10-20T00:00:00Z --docs-dir docs/league_models

Three models per outer fold, all fitted on the training rows with every choice made on inner folds that end before the eval season:
  H_base     the Sprint 4c hierarchical model (grid of `hierarchical_dc`)
  H_ext      the same structure with the wider grid `hierarchical_extended`
  H_dyn      team strength as a random walk over seasons, grid `hierarchical_dynamic` (`dynamic_dc.py`)
Paired block bootstrap on log loss against H_base (rule of Sprint 3: SUPPORTED only if the whole 95% interval of candidate minus H_base is below 0)."""

import argparse
import dataclasses
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from src.config import config_dir_for
from src.data.teams import TeamDirectory

from . import bench_metrics as bm
from . import stat_benchmark as sb
from .benchmark import _rows_loss, outcomes_for
from .competitions import ROOT
from .competitions import load_config as load_league_config
from .coverage import collect
from .dynamic_dc import DynSpec
from .licenses import load_manifest, training_gate
from .per_league import HierarchicalDC
from .s4c_benchmark import UEFA_IDS, load_s4c
from .splits import build_universe, dumps, load_bench_config, make_folds
from .stat_models import NotTrained

TARGETS = ("1x2", "over_under", "btts")
CANDIDATES = (("H_ext", "hierarchical_extended", False), ("H_dyn", "hierarchical_dynamic", True))


def _probs(pred, target: str, line: float) -> np.ndarray:
    if target == "1x2":
        p = np.clip(pred.one_x_two(), 1e-12, 1.0)
        return p / p.sum(axis=1, keepdims=True)
    return np.clip(pred.over(line) if target == "over_under" else pred.btts(), 0.0, 1.0)


def run(root: Path, as_of: datetime, docs_dir: Path | None = None, audit=None, bench_config_path=None, stat_config_path=None, s4c_config_path=None, only_folds=None) -> dict:
    t_start = time.perf_counter()
    bcfg = load_bench_config(bench_config_path) if bench_config_path else load_bench_config()
    scfg = sb.load_stat_config(stat_config_path) if stat_config_path else sb.load_stat_config()
    s4c = load_s4c(s4c_config_path) if s4c_config_path else load_s4c()
    league_cfg = load_league_config()
    manifest = load_manifest()
    training_gate(manifest, ["openfootball_domestic", "openfootball_cups"], "production_ml")
    if audit is None:
        audit = collect(root, league_cfg, TeamDirectory.load(config_dir_for(root) / "team_aliases.yaml"), manifest, as_of)
    folds = make_folds(bcfg, build_universe(bcfg, audit), league_cfg.result_lag_hours)
    if only_folds:
        folds = [f for f in folds if f.eval_season in only_folds]
    line, lag = bcfg.over_under_line, league_cfg.result_lag_hours
    probs: dict[str, dict[str, list]] = {t: {"H_base": [], "H_ext": [], "H_dyn": []} for t in TARGETS}
    diag, rows = [], []
    for fold in folds:
        train, ev = list(fold.train_rows), list(fold.eval_rows)
        rows += ev
        bridge = sum(1 for r in train if r.league_id in UEFA_IDS) >= scfg.raw["bridge"]["min_uefa_matches_total"]
        entry = {"fold": fold.eval_season}
        fits = {"H_base": HierarchicalDC(s4c, scfg.dc_spec(), line, lag, bridge=bridge)}
        for name, section, dynamic in CANDIDATES:
            base = DynSpec(**dataclasses.asdict(scfg.dc_spec())) if dynamic else scfg.dc_spec()
            fits[name] = HierarchicalDC(s4c, base, line, lag, bridge=bridge, section=section)
        for name, mod in fits.items():
            t0 = time.perf_counter()
            try:
                mod.fit(train)
                for t in TARGETS:
                    probs[t][name].append(_probs(mod.predict(ev, t), t, line))
                entry[name] = {t: {"chosen": v["chosen"], "grid_edge": v["grid_edge"], "variance_components": v["variance_components"]} for t, v in mod.info["per_target"].items()}
            except NotTrained as e:
                for t in TARGETS:
                    probs[t][name].append(None)
                entry[name] = {"status": f"NOT_TRAINED ({e})"}
            sys.stderr.write(f"fold {fold.eval_season} {name} {round(time.perf_counter() - t0, 1)} s\n")
        diag.append(entry)
    y = outcomes_for(rows, line)
    blocks = np.array([bm.iso_week_block(r.kickoff_utc) for r in rows])
    lg = np.array([r.league_id for r in rows])
    groups = {"ALL": np.ones(len(rows), bool), **{c: lg == c for c in sorted(set(lg)) if int((lg == c).sum()) >= 30}}
    out_paired, not_trained = [], {}
    for t in TARGETS:
        for cand in ("H_ext", "H_dyn"):
            ok = all(x is not None for x in probs[t][cand]) and all(x is not None for x in probs[t]["H_base"])
            if not ok:
                not_trained[f"{cand}:{t}"] = "at least one fold did not train: no comparison is made (no silent fallback)"
                continue
            ca, cb = np.concatenate(probs[t][cand]), np.concatenate(probs[t]["H_base"])
            for g, m in groups.items():
                a, b = _rows_loss(t, "log_loss", ca[m], y[t][m]), _rows_loss(t, "log_loss", cb[m], y[t][m])
                d = bm.paired_block_bootstrap(a, b, blocks[m], bcfg.bootstrap_samples, bcfg.bootstrap_seed, bcfg.bootstrap_alpha)
                out_paired.append({"target": t, "group": g, "candidate": cand, "reference": "H_base", "n": d.n, "log_loss_candidate": round(float(a.mean()), 5), "log_loss_reference": round(float(b.mean()), 5),
                                   "delta": round(d.delta, 6), "ci95": [round(d.lower, 6), round(d.upper, 6)], "verdict": "SUPPORTED" if d.upper < 0 else ("WORSE" if d.lower > 0 else "INCONCLUSIVE")})  # fmt: skip
    edge = {}
    for name in ("H_base", "H_ext", "H_dyn"):
        picks = [v for dg in diag for v in dg.get(name, {}).values() if isinstance(v, dict) and "grid_edge" in v]
        edge[name] = {"on_edge": sum(1 for v in picks if v["grid_edge"]), "of": len(picks)}
    out = {"protocol": "lm-s4d-v1", "as_of": as_of.isoformat(), "s4c_config_fingerprint": s4c["_fingerprint"], "folds": [f.eval_season for f in folds], "rows": len(rows), "paired": out_paired, "not_compared": not_trained,
           "selections": diag, "selections_on_grid_edge": edge, "holdout": "not read (consumed by Sprint 5); nothing here approves a model"}  # fmt: skip
    if docs_dir is not None:
        docs_dir.mkdir(parents=True, exist_ok=True)
        (docs_dir / "s4d_extension.json").write_text(dumps(out), encoding="utf-8", newline="\n")
    out["wall_seconds"] = round(time.perf_counter() - t_start, 1)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--compare-committed", default=None, help="re-run into a temporary directory and compare with the committed s4d_extension.json in this docs directory")
    ap.add_argument("--atol", type=float, default=1e-3)
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--docs-dir", default=None)
    ap.add_argument("--only-folds", default=None)
    a = ap.parse_args(argv)
    as_of = datetime.fromisoformat(a.as_of.replace("Z", "+00:00")) if a.as_of else datetime.now(UTC)
    root = Path(a.root)
    if a.compare_committed:
        import tempfile

        from .s4c_benchmark import compare_to_committed

        with tempfile.TemporaryDirectory() as tmp:
            run(root, as_of.astimezone(UTC), Path(tmp))
            rep = compare_to_committed(Path(tmp), root / a.compare_committed, a.atol, files=("s4d_extension.json",))
        sys.stdout.write(json.dumps(rep, indent=2, default=str) + "\n")
        return 0 if rep["ok"] else 1
    out = run(root, as_of.astimezone(UTC), (root / a.docs_dir) if a.docs_dir else None, only_folds=a.only_folds.split(",") if a.only_folds else None)
    sys.stdout.write(json.dumps({"selections_on_grid_edge": out["selections_on_grid_edge"], "pooled": [p for p in out["paired"] if p["group"] == "ALL"], "not_compared": out["not_compared"]}, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
