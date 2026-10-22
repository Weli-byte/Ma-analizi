"""Sprint 4b (ADR 0069): do the UEFA stage features help? Development folds only; the frozen holdout is consumed and is never read.

    python -m src.league_models.stage_experiment --as-of 2026-10-20T00:00:00Z --docs-dir docs/league_models

For each Sprint 2 fold and each target a global boosted-tree model with the Sprint 4 features (`base`) and the same model with the stage columns appended (`+stage`) are
tuned with the SAME tuner and budget on the inner-validation season and scored on the UEFA evaluation rows of the fold. The paired block bootstrap on the pooled UEFA rows
(and per competition) decides with the Sprint 3 rule: SUPPORTED only if the whole 95% interval of (+stage minus base) is below 0. Nothing in the committed Sprint 3 to 5
outputs changes; an adopted extension would be a new, prospectively evaluated feature version."""

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from src.config import config_dir_for
from src.data.teams import TeamDirectory

from . import bench_metrics as bm
from . import stat_benchmark as sb
from .benchmark import outcomes_for
from .competitions import ROOT
from .competitions import load_config as load_league_config
from .coverage import collect
from .licenses import load_manifest, training_gate
from .ml_features import FeatureConfig, build_features
from .ml_models import GBM, TARGETS, full_params, load_ml_config, tune
from .splits import build_universe, dumps, load_bench_config, make_folds
from .stage_features import SCHEMA_VERSION, STAGE_COLUMNS, stage_matrix, stage_table, unmatched_stage_texts

UEFA = ("UEFA_UCL", "UEFA_UEL", "UEFA_UECL")


def fit_predict(mcfg, names, cat, Xtr, ytr, fit_m, val_m, Xev, target: str, seed: int) -> np.ndarray:
    tn = tune(mcfg, target, Xtr[fit_m], ytr[fit_m], Xtr[val_m], ytr[val_m], names, cat, seed)
    params = full_params(mcfg, tn["best_params"])
    rounds = max(10, round(tn["best_iteration"] * len(ytr) / int(fit_m.sum())))
    g = mcfg.raw["gbm"]
    m = GBM("lightgbm", target, params, names, cat, seed, g["threads"], g["class_weight"]).fit(Xtr, ytr, rounds)
    return m.predict_proba(Xev)


def run(root: Path, as_of: datetime, docs_dir: Path | None = None, audit=None, bench_config_path=None, stat_config_path=None, ml_config_path=None) -> dict:
    bcfg = load_bench_config(bench_config_path) if bench_config_path else load_bench_config()
    scfg = sb.load_stat_config(stat_config_path) if stat_config_path else sb.load_stat_config()
    mcfg = load_ml_config(ml_config_path) if ml_config_path else load_ml_config()
    league_cfg = load_league_config()
    manifest = load_manifest()
    training_gate(manifest, ["openfootball_domestic", "openfootball_cups"], "production_ml")
    if audit is None:
        directory = TeamDirectory.load(config_dir_for(root) / "team_aliases.yaml")
        audit = collect(root, league_cfg, directory, manifest, as_of)
    universe = build_universe(bcfg, audit)
    folds = make_folds(bcfg, universe, league_cfg.result_lag_hours)
    table = stage_table(audit.identity_rows)
    fcfg = FeatureConfig.from_raw(mcfg.raw, mcfg.raw["league_vocabulary"], league_cfg.result_lag_hours, bcfg.default_stage, scfg.elo_spec())
    dev = list(universe.dev)
    fm = build_features(dev, fcfg)
    ids = [r.fixture_id for r in dev]
    sm = stage_matrix(ids, table)
    X_base, X_stage = fm.X, np.hstack([fm.X, sm])
    names_base = list(fm.schema.names)
    names_stage = [*names_base, *STAGE_COLUMNS]
    pos = {f: i for i, f in enumerate(ids)}
    seed = int(mcfg.raw["seed"])
    rows_eval, p_base, p_stage = [], {t: [] for t in TARGETS}, {t: [] for t in TARGETS}
    for fold in folds:
        tr_idx = [pos[r.fixture_id] for r in fold.train_rows]
        ev_rows = [r for r in fold.eval_rows if r.league_id in UEFA]
        if not ev_rows:
            continue
        ev_idx = [pos[r.fixture_id] for r in ev_rows]
        season = np.array([r.season for r in fold.train_rows])
        by_season = season != fold.inner_validation_season
        val_m = ~by_season
        start = min(r.kickoff_utc for r, v in zip(fold.train_rows, val_m, strict=True) if v) if val_m.any() else None
        if start is None or not by_season.any():
            continue
        lag = timedelta(hours=league_cfg.result_lag_hours)
        fit_m = by_season & np.array([r.kickoff_utc + lag <= start for r in fold.train_rows])
        y = outcomes_for(list(fold.train_rows), bcfg.over_under_line)
        rows_eval += ev_rows
        for t in TARGETS:
            p_base[t].append(fit_predict(mcfg, names_base, ["league_code"], X_base[tr_idx], y[t], fit_m, val_m, X_base[ev_idx], t, seed))
            p_stage[t].append(fit_predict(mcfg, names_stage, ["league_code"], X_stage[tr_idx], y[t], fit_m, val_m, X_stage[ev_idx], t, seed))
    y_all = outcomes_for(rows_eval, bcfg.over_under_line)
    leagues = np.array([r.league_id for r in rows_eval])
    blocks = np.array([bm.iso_week_block(r.kickoff_utc) for r in rows_eval])
    groups = {"UEFA_ALL": np.ones(len(rows_eval), bool), **{lg: leagues == lg for lg in UEFA}}
    results = {}
    for t in TARGETS:
        pb, ps = np.concatenate(p_base[t]), np.concatenate(p_stage[t])
        loss = (lambda p, y: bm.rows_log_loss_1x2(p, y)) if t == "1x2" else (lambda p, y: bm.binary_log_loss_rows(np.clip(p, 1e-6, 1 - 1e-6), y))
        results[t] = {}
        for gname, sel in groups.items():
            if sel.sum() < 30:
                results[t][gname] = {"n": int(sel.sum()), "verdict": "TOO_FEW_ROWS"}
                continue
            yb = y_all[t][sel]
            d = bm.paired_block_bootstrap(loss(ps[sel], yb), loss(pb[sel], yb), blocks[sel], 2000, bcfg.bootstrap_seed, bcfg.bootstrap_alpha)
            results[t][gname] = {"n": int(sel.sum()), "blocks": d.n_blocks, "log_loss_base": round(float(loss(pb[sel], yb).mean()), 5), "log_loss_with_stage": round(float(loss(ps[sel], yb).mean()), 5),
                                 "delta_with_stage_minus_base": round(d.delta, 6), "ci95": [round(d.lower, 6), round(d.upper, 6)],
                                 "verdict": "SUPPORTED" if d.upper < 0 else ("NOT_SUPPORTED" if d.lower > 0 else "INCONCLUSIVE")}  # fmt: skip
    out = {
        "experiment": "UEFA stage features (Sprint 4b, ADR 0069)", "schema_version": SCHEMA_VERSION, "stage_columns": list(STAGE_COLUMNS), "folds": [f.eval_season for f in folds],
        "uefa_evaluation_rows": len(rows_eval), "rule": "SUPPORTED only if the whole 95% block-bootstrap interval of (with stage minus base) is below 0; 12 comparisons, so a single nominal pass is weak evidence",
        "base": "Sprint 4 features, tuned LightGBM, UEFA rows of the development folds", "stage_texts_without_a_flag": unmatched_stage_texts(table),
        "stage_coverage_uefa_rows": {"with_stage_text": int(sum(1 for r in rows_eval if table.get(r.fixture_id))), "total": len(rows_eval)}, "results": results,
        "holdout": "not read (consumed by Sprint 5); an adopted extension can only be confirmed prospectively",
    }  # fmt: skip
    if docs_dir is not None:
        docs_dir.mkdir(parents=True, exist_ok=True)
        (docs_dir / "s4b_stage_experiment.json").write_text(dumps(out), encoding="utf-8", newline="\n")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--docs-dir", default=None)
    a = ap.parse_args(argv)
    as_of = datetime.fromisoformat(a.as_of.replace("Z", "+00:00")) if a.as_of else datetime.now(UTC)
    root = Path(a.root)
    out = run(root, as_of.astimezone(UTC), (root / a.docs_dir) if a.docs_dir else None)
    sys.stdout.write(json.dumps({k: out[k] for k in ("uefa_evaluation_rows", "results")}, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

