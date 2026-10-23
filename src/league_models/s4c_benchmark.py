"""Sprint 4c (ADR 0071): per-league SEPARATE models, a hierarchical Poisson model with learned variance components, nested walk-forward selection, the extended feature set and the
exact-score audit, all on the DEVELOPMENT folds of Sprint 2 (the frozen holdout is consumed by Sprint 5 and is never read; nothing here can approve a model for users).

    python -m src.league_models.s4c_benchmark --as-of 2026-10-20T00:00:00Z --docs-dir docs/league_models [--quick]

Candidates (all fitted on the training rows of each outer fold only, every choice made on inner folds that end before the eval season):
  P_dc_sep    one ridge Poisson / Dixon-Coles per competition, fitted on that competition's matches only, penalty and decay chosen per target by the nested selection
  H_dc_hier   team < country < world random effects + competition terms, variance components chosen per target by the nested selection
  G_base      tuned global LightGBM with the Sprint 4 features, tuned with the NESTED tuner (the control)
  G_extra     the same with the Sprint 4c extension features
  P_gbm_sep   one tuned LightGBM per competition and target on that competition's matches only (Sprint 4 + extension features, nested tuner)
References: the production global models (Elo for 1X2, Dixon-Coles for totals and BTTS), the Sprint 3 bridge model B_dc_league, and the Sprint 3 Elo bridge.
A paired block bootstrap (ISO-week blocks) decides each comparison with the Sprint 3 rule: SUPPORTED only if the whole 95% interval of (candidate minus reference) is below 0."""

import argparse
import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import yaml

from src.config import config_dir_for, load_config
from src.data.teams import TeamDirectory
from src.models import build_models

from . import bench_metrics as bm
from . import stat_benchmark as sb
from .benchmark import _metrics, _rows_loss, goal_target_probs, outcomes_for
from .competitions import ROOT
from .competitions import load_config as load_league_config
from .coverage import collect
from .extra_features import SCHEMA_VERSION as EXTRA_VERSION
from .extra_features import ExtraConfig, build_extra, perturbation_audit
from .extra_features import column_names as extra_columns
from .extra_features import schema_sha as extra_schema_sha
from .licenses import load_manifest, training_gate
from .ml_features import FeatureConfig, build_features
from .ml_models import GBM, TARGETS, NotTrainedML, full_params, load_ml_config
from .nested import inner_folds, tune_nested
from .per_league import HierarchicalDC, PerLeagueDC, constant_scoreline_matrices, exact_score_metrics
from .splits import build_universe, dumps, load_bench_config, make_folds
from .stat_models import NotTrained, TimeAwareElo

S4C_CONFIG = ROOT / "configs" / "league_models" / "s4c.yaml"
UEFA_IDS = frozenset({"UEFA_UCL", "UEFA_UEL", "UEFA_UECL"})
PROTOCOL = "lm-s4c-v1"

# (candidate, reference) pairs; evaluated for every target in which both exist
COMPARISONS = (
    ("P_dc_sep", "B_dc_league"), ("P_dc_sep", "production_global"), ("P_dc_sep", "E_elo_bridge"),
    ("H_dc_hier", "B_dc_league"), ("H_dc_hier", "production_global"),
    ("G_extra", "G_base"), ("G_base", "production_global"), ("G_extra", "production_global"),
    ("P_gbm_sep", "G_extra"), ("P_gbm_sep", "G_base"), ("P_gbm_sep", "production_global"), ("P_gbm_sep", "P_dc_sep"),
)  # fmt: skip


def load_s4c(path: Path = S4C_CONFIG) -> dict:
    text = path.read_text(encoding="utf-8")
    raw = yaml.safe_load(text)
    raw["_fingerprint"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return raw


# ------------------------------------------------------------------------------------------------ GBM helpers
def _positions(train_rows: list, sets) -> list[tuple[np.ndarray, np.ndarray]]:
    pos = {r.fixture_id: i for i, r in enumerate(train_rows)}
    return [(np.array([pos[r.fixture_id] for r in f.fit]), np.array([pos[r.fixture_id] for r in f.val])) for f in sets]


def fit_gbm_nested(mcfg, target: str, X: np.ndarray, y: np.ndarray, train_rows: list, names: list[str], categorical: list[str], seed: int, lag_hours: int, s4c: dict,
                   max_trials: int, n_startup: int) -> tuple[GBM, dict]:
    """Nested-tuned LightGBM on `train_rows` (X, y aligned with them). Raises NotTrainedML when no inner fold or no trial trains."""
    nc = s4c["nested"]
    folds, dropped = inner_folds(train_rows, lag_hours, int(nc["inner_folds"]), int(nc["min_inner_fit_matches"]))
    if not folds:
        raise NotTrainedML("no inner fold with enough fit matches")
    pos = _positions(train_rows, folds)
    sets = [(X[f], y[f], X[v], y[v]) for f, v in pos]
    tn = tune_nested(mcfg.raw, target, sets, names, categorical, seed, max_trials, n_startup)
    mean_fit = float(np.mean([len(f) for f, _ in pos]))
    rounds = max(10, round(tn["best_iteration"] * len(train_rows) / mean_fit))
    g = mcfg.raw["gbm"]
    model = GBM("lightgbm", target, full_params(mcfg, tn["best_params"]), names, categorical, seed, g["threads"], g["class_weight"]).fit(X, y, rounds)
    info = {"best_params": tn["best_params"], "best_mean_validation_log_loss": tn["best_mean_validation_log_loss"], "trials": tn["trials_run"], "rounds": rounds,
            "inner_folds": [{"season": f.season, "kind": f.kind, "fit": len(f.fit), "val": len(f.val)} for f in folds], "dropped_inner_folds": dropped, "train_rows": len(train_rows)}  # fmt: skip
    return model, info


# ------------------------------------------------------------------------------------------------ one outer fold
def run_fold(ctx: dict, fold) -> dict:
    scfg, bcfg, mcfg, s4c, lag = ctx["scfg"], ctx["bcfg"], ctx["mcfg"], ctx["s4c"], ctx["lag"]
    train, ev = list(fold.train_rows), list(fold.eval_rows)
    n, line = len(ev), bcfg.over_under_line
    t_all = time.perf_counter()
    pos = ctx["pos"]
    tr_idx = np.array([pos[r.fixture_id] for r in train])
    ev_idx = np.array([pos[r.fixture_id] for r in ev])
    ytr = outcomes_for(train, line)
    P: dict[str, dict[str, np.ndarray]] = {t: {} for t in TARGETS}
    M: dict[str, np.ndarray] = {}                      # score matrices for the exact-score audit
    diag: dict = {"fold": fold.eval_season, "train_matches": len(train), "eval_matches": n, "fallbacks": {}, "nested": {}, "tuning": {}, "timings": {}}

    # -- bridge availability (as Sprint 3) and the reference B_dc_league
    uefa = [r for r in train if r.league_id in UEFA_IDS]
    bridge_ok = len(uefa) >= scfg.raw["bridge"]["min_uefa_matches_total"]
    t0 = time.perf_counter()
    ref_mod, ref_diag = sb._fit_dc(scfg.dc_spec(competition_terms=True, country_levels=bridge_ok), "B_dc_league", train, None)
    if ref_mod is None:
        raise SystemExit(f"B_dc_league did not train in fold {fold.eval_season}: {ref_diag.get('reason')} (fail closed)")
    ref_pred = ref_mod.predict(ev)
    diag["timings"]["B_dc_league_s"] = round(time.perf_counter() - t0, 2)

    def put_matrix(name: str, pred, target: str) -> None:
        if target == "1x2":
            p = np.clip(pred.one_x_two(), 0.0, 1.0)
            P["1x2"][name] = p / p.sum(axis=1, keepdims=True)
        elif target == "over_under":
            P["over_under"][name] = np.clip(pred.over(line), 0.0, 1.0)
        else:
            P["btts"][name] = np.clip(pred.btts(), 0.0, 1.0)

    for t in TARGETS:
        put_matrix("B_dc_league", ref_pred, t)
    M["B_dc_league"] = ref_pred.matrices

    # -- Elo bridge and the production global models
    e_mod = TimeAwareElo(scfg.elo_spec(), "E_elo_bridge", UEFA_IDS).fit(train)
    P["1x2"]["E_elo_bridge"] = e_mod.predict_proba(ev)[0]
    model_cfg = load_config("model")
    elo, dcp = build_models(["elo", "dixon_coles"], elo_config=model_cfg.elo, poisson_config=model_cfg.poisson)
    elo.fit(train)
    dcp.fit(train)
    P["1x2"]["production_global"] = np.asarray(elo.predict_proba(ev), dtype=float)
    over, both = goal_target_probs(dcp, ev, line)
    P["over_under"]["production_global"], P["btts"]["production_global"] = over, both

    # -- constant baselines
    P["over_under"]["train_rate"] = np.full(n, float(np.mean([(r.home_goals + r.away_goals) > line for r in train])))
    P["btts"]["train_rate"] = np.full(n, float(np.mean([(r.home_goals > 0 and r.away_goals > 0) for r in train])))
    g1 = ref_pred.matrices.shape[1]
    M["constant_scoreline"] = constant_scoreline_matrices(train, n, g1)

    # -- hierarchical model with learned variance components
    t0 = time.perf_counter()
    try:
        hier = HierarchicalDC(s4c, scfg.dc_spec(), line, lag, bridge=bridge_ok).fit(train)
        for t in TARGETS:
            put_matrix("H_dc_hier", hier.predict(ev, t), t)
        M["H_dc_hier"] = hier.predict(ev, "1x2").matrices
        diag["nested"]["H_dc_hier"] = {k: v for k, v in hier.info.items() if k != "nested_table"}
        diag["nested"]["H_dc_hier"]["candidate_mean_losses"] = [{"candidate": x["candidate"], "mean_loss": x["mean_loss"]} for x in hier.info["nested_table"]]
    except NotTrained as e:
        for t in TARGETS:
            put_matrix("H_dc_hier", ref_pred, t)
        M["H_dc_hier"] = ref_pred.matrices
        diag["fallbacks"]["H_dc_hier"] = f"NOT_TRAINED ({e}): B_dc_league used for every row"
    diag["timings"]["H_dc_hier_s"] = round(time.perf_counter() - t0, 2)

    # -- separately trained per-league Poisson / Dixon-Coles models
    t0 = time.perf_counter()
    pl = PerLeagueDC(s4c, scfg.dc_spec(), line, lag).fit(train)
    cover = {}
    for t in TARGETS:
        pred, covered = pl.predict(ev, t)
        base = ref_pred if pred is None else pred
        if pred is None:
            put_matrix("P_dc_sep", ref_pred, t)
        else:
            merged_m = np.where(covered[:, None, None], pred.matrices, ref_pred.matrices)
            merged = type(pred)(merged_m, np.where(covered, pred.lam_home, ref_pred.lam_home), np.where(covered, pred.lam_away, ref_pred.lam_away), pred.seen_home, pred.seen_away, pred.tail_mass, pred.raw_sum)
            put_matrix("P_dc_sep", merged, t)
            if t == "1x2":
                M["P_dc_sep"] = merged_m
        del base
        cover[t] = int(covered.sum())
    if "P_dc_sep" not in M:
        M["P_dc_sep"] = ref_pred.matrices
    diag["nested"]["P_dc_sep"] = {c: {k: v for k, v in i.items() if k != "nested_table"} for c, i in pl.info.items()}
    diag["nested"]["P_dc_sep_candidates"] = {c: [{"candidate": x["candidate"], "mean_loss": x["mean_loss"]} for x in i["nested_table"]] for c, i in pl.info.items() if "nested_table" in i}
    diag["fallbacks"]["P_dc_sep_rows_covered_by_a_separate_model"] = {**cover, "eval_rows": n}
    diag["timings"]["P_dc_sep_s"] = round(time.perf_counter() - t0, 2)
    diag["per_league_export"] = pl.export() if ctx.get("export_models") else None

    # -- boosted trees: tuned global control, extended features, separate per-league models
    fm = ctx["fm"]
    XA, names_all = ctx["XA"], ctx["names_all"]
    base_cols = list(range(len(fm.schema.names)))
    seed = int(mcfg.raw["seed"])
    gcfg = mcfg.raw["tuning"]
    max_trials, n_startup = ctx["gbm_trials"], min(ctx["gbm_startup"], ctx["gbm_trials"])
    plcfg = s4c["per_league_gbm"]
    for t in TARGETS:
        y = ytr[t]
        for name, cols in (("G_base", base_cols), ("G_extra", list(range(XA.shape[1])))):
            t0 = time.perf_counter()
            nm = [names_all[i] for i in cols]
            try:
                model, info = fit_gbm_nested(mcfg, t, XA[tr_idx][:, cols], y, train, nm, ["league_code"], seed, lag, s4c, max_trials, n_startup)
                P[t][name] = model.predict_proba(XA[ev_idx][:, cols])
                diag["tuning"][f"{name}:{t}"] = info
            except NotTrainedML as e:
                P[t][name] = P[t]["B_dc_league"]
                diag["fallbacks"][f"{name}:{t}"] = f"NOT_TRAINED ({e}): B_dc_league used"
            diag["timings"][f"{name}:{t}_s"] = round(time.perf_counter() - t0, 2)
        # separate per-league boosted trees
        t0 = time.perf_counter()
        out = P[t]["G_extra"].copy()
        local_cols = [i for i in range(XA.shape[1]) if names_all[i] not in ctx["identity_names"]]
        nm_local = [names_all[i] for i in local_cols]
        used = {}
        for c in sorted({r.league_id for r in ev}):
            rows_c = [r for r in train if r.league_id == c]
            sel = np.array([i for i, r in enumerate(train) if r.league_id == c], dtype=int)
            ev_sel = np.array([i for i, r in enumerate(ev) if r.league_id == c], dtype=int)
            if len(rows_c) < int(plcfg["min_train_matches"]):
                used[c] = f"LOCAL_INELIGIBLE: {len(rows_c)} < {plcfg['min_train_matches']}: G_extra used"
                continue
            try:
                model, info = fit_gbm_nested(mcfg, t, XA[tr_idx[sel]][:, local_cols], y[sel], rows_c, nm_local, [], seed, lag, s4c, int(plcfg["max_trials"]), min(int(plcfg["n_startup_trials"]), int(plcfg["max_trials"])))
                out[ev_sel] = model.predict_proba(XA[ev_idx[ev_sel]][:, local_cols])
                used[c] = "SEPARATE_MODEL"
                diag["tuning"][f"P_gbm_sep:{t}:{c}"] = info
            except NotTrainedML as e:
                used[c] = f"NOT_TRAINED ({str(e)[:80]}): G_extra used"
        P[t]["P_gbm_sep"] = out
        diag["fallbacks"][f"P_gbm_sep:{t}"] = used
        diag["timings"][f"P_gbm_sep:{t}_s"] = round(time.perf_counter() - t0, 2)
    del gcfg
    diag["timings"]["fold_total_s"] = round(time.perf_counter() - t_all, 2)
    return {"fold": fold, "P": P, "M": M, "diag": diag}


# ------------------------------------------------------------------------------------------------ analysis
def group_masks(rows: list, min_rows: int = 30) -> dict[str, np.ndarray]:
    lg = np.array([r.league_id for r in rows])
    out = {"ALL": np.ones(len(rows), bool)}
    for c in sorted(set(lg)):
        m = lg == c
        if int(m.sum()) >= min_rows:
            out[c] = m
    return out


def analyse(results: list[dict], bcfg, s4c: dict) -> dict:
    rows = [r for x in results for r in x["fold"].eval_rows]
    y = outcomes_for(rows, bcfg.over_under_line)
    blocks = np.array([bm.iso_week_block(r.kickoff_utc) for r in rows])
    probs: dict[str, dict[str, np.ndarray]] = {t: {} for t in TARGETS}
    for t in TARGETS:
        for name in results[0]["P"][t]:
            probs[t][name] = np.concatenate([x["P"][t][name] for x in results])
    groups = group_masks(rows)
    metrics: dict = {t: {} for t in TARGETS}
    paired: list[dict] = []
    for t in TARGETS:
        for name, p in probs[t].items():
            metrics[t][name] = {g: {"n": int(m.sum()), **_metrics(t, p[m], y[t][m], bcfg.calibration_bins)} for g, m in groups.items()}
        for cand, ref in COMPARISONS:
            if cand not in probs[t] or ref not in probs[t]:
                continue
            for g, m in groups.items():
                a, b = _rows_loss(t, "log_loss", probs[t][cand][m], y[t][m]), _rows_loss(t, "log_loss", probs[t][ref][m], y[t][m])
                d = bm.paired_block_bootstrap(a, b, blocks[m], bcfg.bootstrap_samples, bcfg.bootstrap_seed, bcfg.bootstrap_alpha)
                paired.append({"target": t, "group": g, "candidate": cand, "reference": ref, "n": d.n, "blocks": d.n_blocks, "log_loss_candidate": round(float(a.mean()), 5), "log_loss_reference": round(float(b.mean()), 5),
                               "delta": round(d.delta, 6), "ci95": [round(d.lower, 6), round(d.upper, 6)], "verdict": "SUPPORTED" if d.upper < 0 else ("WORSE" if d.lower > 0 else "INCONCLUSIVE")})  # fmt: skip
    # exact score
    top_k = int(s4c["exact_score"]["top_k"])
    M = {name: np.concatenate([x["M"][name] for x in results]) for name in results[0]["M"]}
    exact: dict = {}
    for name, mat in M.items():
        exact[name] = {g: exact_score_metrics(mat[m], [r for r, k in zip(rows, m, strict=True) if k], top_k) for g, m in groups.items()}
    exact_paired = []
    hg = np.array([r.home_goals for r in rows])
    ag = np.array([r.away_goals for r in rows])
    gsz = next(iter(M.values())).shape[1]
    inside = (hg < gsz) & (ag < gsz)

    def row_logscore(mat):
        p = np.where(inside, mat[np.arange(len(rows)), np.minimum(hg, gsz - 1), np.minimum(ag, gsz - 1)], 0.0)
        return -np.log(np.clip(p, 1e-12, 1.0))

    ls = {name: row_logscore(mat) for name, mat in M.items()}
    for cand, ref in (("P_dc_sep", "B_dc_league"), ("H_dc_hier", "B_dc_league"), ("P_dc_sep", "constant_scoreline"), ("B_dc_league", "constant_scoreline"), ("H_dc_hier", "constant_scoreline")):
        for g, m in groups.items():
            d = bm.paired_block_bootstrap(ls[cand][m], ls[ref][m], blocks[m], bcfg.bootstrap_samples, bcfg.bootstrap_seed, bcfg.bootstrap_alpha)
            exact_paired.append({"group": g, "candidate": cand, "reference": ref, "n": d.n, "delta_log_score": round(d.delta, 6), "ci95": [round(d.lower, 6), round(d.upper, 6)],
                                 "verdict": "SUPPORTED" if d.upper < 0 else ("WORSE" if d.lower > 0 else "INCONCLUSIVE")})  # fmt: skip
    # base rates for the over/under claim audit
    rates: dict = {}
    for c in sorted({r.league_id for r in rows}):
        for s in sorted({r.season for r in rows if r.league_id == c}):
            sel = [r for r in rows if r.league_id == c and r.season == s]
            rates[f"{c}|{s}"] = {"n": len(sel), "over_2_5_rate": round(float(np.mean([(r.home_goals + r.away_goals) > bcfg.over_under_line for r in sel])), 4),
                                 "btts_rate": round(float(np.mean([(r.home_goals > 0 and r.away_goals > 0) for r in sel])), 4)}  # fmt: skip
    return {"rows": len(rows), "metrics": metrics, "paired": paired, "exact_score": exact, "exact_score_paired": exact_paired, "base_rates": rates, "groups": {g: int(m.sum()) for g, m in groups.items()}}


def decisions(paired: list[dict]) -> dict:
    """Per competition and target: which candidates are SUPPORTED against the production global model and against their family reference. Not an approval: development folds only."""
    out: dict = {"rule": "SUPPORTED only if the whole 95% interval of (candidate minus reference) log loss is below 0; development folds only, the holdout is consumed, so NO candidate can be approved from this run",
                 "comparisons": len(paired), "expected_false_supported_if_nothing_works": round(0.025 * len(paired), 1), "cells": {}}  # fmt: skip
    for p in paired:
        if p["group"] == "ALL":
            continue
        cell = out["cells"].setdefault(f"{p['group']}|{p['target']}", {"supported": [], "worse": []})
        tag = f"{p['candidate']} vs {p['reference']} ({p['delta']:+.4f})"
        if p["verdict"] == "SUPPORTED":
            cell["supported"].append(tag)
        elif p["verdict"] == "WORSE":
            cell["worse"].append(tag)
    out["supported_total"] = sum(len(c["supported"]) for c in out["cells"].values())
    out["worse_total"] = sum(len(c["worse"]) for c in out["cells"].values())
    return out


# ------------------------------------------------------------------------------------------------ run
def run(root: Path, as_of: datetime, docs_dir: Path | None = None, audit=None, quick: bool = False, export_models: bool = False, only_folds: list[str] | None = None) -> dict:
    t_start = time.perf_counter()
    bcfg = load_bench_config()
    scfg = sb.load_stat_config()
    mcfg = load_ml_config()
    s4c = load_s4c()
    league_cfg = load_league_config()
    manifest = load_manifest()
    training_gate(manifest, ["openfootball_domestic", "openfootball_cups"], "production_ml")
    if audit is None:
        directory = TeamDirectory.load(config_dir_for(root) / "team_aliases.yaml")
        audit = collect(root, league_cfg, directory, manifest, as_of)
    universe = build_universe(bcfg, audit)
    folds = make_folds(bcfg, universe, league_cfg.result_lag_hours)
    if only_folds:
        folds = [f for f in folds if f.eval_season in only_folds]
    elif quick:
        folds = folds[-2:]
    dev = list(universe.dev)
    fcfg = FeatureConfig.from_raw(mcfg.raw, mcfg.raw["league_vocabulary"], league_cfg.result_lag_hours, bcfg.default_stage, scfg.elo_spec())
    ecfg = ExtraConfig.from_raw(s4c, league_cfg.result_lag_hours, bcfg.default_stage)
    fm = build_features(dev, fcfg)
    EX = build_extra(dev, ecfg)
    XA = np.hstack([fm.X, EX])
    names_all = [*fm.schema.names, *extra_columns(ecfg)]
    identity = {n for n in fm.schema.names if fm.schema.groups[n] == "league_identity"}
    ctx = {"scfg": scfg, "bcfg": bcfg, "mcfg": mcfg, "s4c": s4c, "lag": league_cfg.result_lag_hours, "fm": fm, "XA": XA, "names_all": names_all, "identity_names": identity,
           "pos": {r.fixture_id: i for i, r in enumerate(dev)}, "export_models": export_models,
           "gbm_trials": 4 if quick else int(mcfg.raw["tuning"]["max_trials"]), "gbm_startup": 3 if quick else int(mcfg.raw["tuning"]["n_startup_trials"])}  # fmt: skip
    results = []
    for f in folds:
        res = run_fold(ctx, f)
        sys.stderr.write(f"fold {f.eval_season} done in {res['diag']['timings']['fold_total_s']} s\n")
        results.append(res)
    analysis = analyse(results, bcfg, s4c)
    dec = decisions(analysis["paired"])
    points = [datetime(2022, 1, 1, tzinfo=UTC), datetime(2023, 6, 1, tzinfo=UTC), datetime(2024, 3, 1, tzinfo=UTC)]
    audit_extra = perturbation_audit(dev, ecfg, points)
    out = {
        "protocol": PROTOCOL, "as_of": as_of.isoformat(), "quick": quick, "s4c_config_fingerprint": s4c["_fingerprint"], "ml_config_fingerprint": mcfg.fingerprint, "stat_config_fingerprint": scfg.fingerprint,
        "folds": [f.eval_season for f in folds], "extra_features": {"schema_version": EXTRA_VERSION, "sha256": extra_schema_sha(ecfg), "columns": list(extra_columns(ecfg)), "perturbation_audit": audit_extra},
        "holdout": "not read (consumed by Sprint 5); nothing in this run can be approved for users", "analysis": analysis, "decisions": dec,
        "diagnostics": [x["diag"] for x in results], "wall_seconds": round(time.perf_counter() - t_start, 1),
    }  # fmt: skip
    if docs_dir is not None and not quick:
        docs_dir.mkdir(parents=True, exist_ok=True)
        slim = [{k: v for k, v in d.items() if k not in ("per_league_export", "timings")} for d in out["diagnostics"]]   # wall-clock timings are not reproducible and stay out of the committed outputs
        parts = {
            "s4c_metrics.json": analysis["metrics"], "s4c_paired.json": analysis["paired"], "s4c_decisions.json": dec,
            "s4c_exact_score.json": {"exact_score": analysis["exact_score"], "paired": analysis["exact_score_paired"], "base_rates_by_competition_season": analysis["base_rates"]},
            "s4c_nested_selection.json": slim, "s4c_feature_audit.json": out["extra_features"],
            "s4c_run_manifest.json": {k: out[k] for k in ("protocol", "as_of", "s4c_config_fingerprint", "ml_config_fingerprint", "stat_config_fingerprint", "folds", "holdout")},
        }  # fmt: skip
        for name, obj in parts.items():
            (docs_dir / name).write_text(dumps(obj), encoding="utf-8", newline="\n")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--docs-dir", default=None)
    ap.add_argument("--quick", action="store_true", help="last two folds and few trials: a smoke run, never written to docs")
    ap.add_argument("--only-folds", default=None, help="comma separated eval seasons")
    ap.add_argument("--export-models", action="store_true")
    a = ap.parse_args(argv)
    as_of = datetime.fromisoformat(a.as_of.replace("Z", "+00:00")) if a.as_of else datetime.now(UTC)
    root = Path(a.root)
    out = run(root, as_of.astimezone(UTC), (root / a.docs_dir) if a.docs_dir else None, quick=a.quick, export_models=a.export_models, only_folds=a.only_folds.split(",") if a.only_folds else None)
    sys.stdout.write(json.dumps({"folds": out["folds"], "decisions": {k: out["decisions"][k] for k in ("comparisons", "supported_total", "worse_total", "expected_false_supported_if_nothing_works")}, "wall_seconds": out["wall_seconds"]}, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
