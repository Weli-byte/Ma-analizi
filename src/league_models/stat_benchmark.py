"""Sprint 3 (ADR 0065): league statistical candidates on the Sprint 2 folds.

    python -m src.league_models.stat_benchmark --as-of 2026-10-20T00:00:00Z --dry-run
    python -m src.league_models.stat_benchmark --as-of 2026-10-20T00:00:00Z --output-dir artifacts/league_models/stat/run --docs-dir docs/league_models
    python -m src.league_models.stat_benchmark --verify DIR | --reproduce DIR | --compare-committed docs/league_models

Candidates (all fitted per outer fold on training rows only, parameters frozen before the eval season, predictions immutable):
  A_dc                 global ridge Poisson / Dixon-Coles (the reference of this sprint)
  B_dc_league          A + competition intercept / home advantage + country levels (the cross-league bridge)
  C_dc_local           one DC per competition on that competition's own history
  D_blend              (1 - w) * B + w * C per competition, w learned on the inner-validation block and shrunk toward 0
  E_elo_bridge         per-country time-aware Elo with UEFA-learned country offsets
  F_elo_local          one Elo per competition on its own history
  ablations of A / B   A_dc_norho, A_dc_rest, A_dc_shrink, B_dc_league_nohome (league-hypothesis tests)
  references           elo_s2 (the existing global Elo), historical_prior
The frozen holdout is never read. A candidate that fails to train is NOT_TRAINED and is replaced by an explicit, counted
fallback; nothing is silently defaulted. The existing production models are untouched."""

import argparse
import dataclasses
import hashlib
import json
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import yaml

from src.config import config_dir_for, load_config
from src.data.teams import TeamDirectory
from src.evaluation.dataset import EvalRow
from src.models import build_models

from . import bench_metrics as bm
from .benchmark import (
    FoldResult,
    _walk,
    collect_eval,
    git_sha,
    outcomes_for,
    register_run,
    score_all,
    sha256_file,
    verify_run,
)
from .competitions import ROOT
from .competitions import load_config as load_league_config
from .coverage import AuditResult, collect
from .licenses import load_manifest, manifest_hash, training_gate
from .splits import (
    BenchConfig,
    Fold,
    assert_disjoint,
    build_universe,
    dumps,
    history_leak_check,
    load_bench_config,
    make_folds,
    protocol_manifest,
)
from .stat_models import (
    NOT_TRAINED,
    DCSpec,
    EloSpec,
    MatrixPrediction,
    NotTrained,
    RidgePoissonDC,
    TimeAwareElo,
    country_of,
    rest_features,
)

STAT_CONFIG = ROOT / "configs" / "league_models" / "stat_models.yaml"
UEFA_IDS = frozenset({"UEFA_UCL", "UEFA_UEL", "UEFA_UECL"})
LICENSED_PROVIDERS = ["openfootball_domestic", "openfootball_cups"]
MATRIX_MODELS = ("A_dc", "A_dc_norho", "A_dc_rest", "A_dc_shrink", "B_dc_league", "B_dc_league_nohome", "C_dc_local", "D_blend")
ELO_MODELS = ("E_elo_bridge", "F_elo_local")
LOCAL_TYPES = ("C_dc_local", "D_blend", "F_elo_local")
EXACT_FILES = ("protocol_manifest.json", "league_decisions.json", "hypotheses.json")
NUMERIC_FILES = ("stat_metrics.json", "paired_deltas.json", "reliability.json", "score_audit.json", "uefa_bridge.json")


@dataclasses.dataclass(frozen=True)
class StatConfig:
    raw: dict
    fingerprint: str

    def dc_spec(self, **over) -> DCSpec:
        d = self.raw["dc"]
        base = {k: d[k] for k in ("max_goals", "l2_team", "l2_country", "l2_competition", "l2_home_dev", "l2_beta", "maxiter",
                                  "rho_bounds_cap", "rest_cap_days", "rest_reference_days")}  # fmt: skip
        return DCSpec(**{**base, **over})

    def elo_spec(self, **over) -> EloSpec:
        e = self.raw["elo"]
        return EloSpec(**{**{k: e[k] for k in ("initial_rating", "k_factor", "home_advantage", "goal_difference_multiplier",
                                               "season_reversion", "promoted_penalty", "min_matches_certain", "bridge_k",
                                               "bridge_team_k_share")}, **over})  # fmt: skip


def load_stat_config(path: Path = STAT_CONFIG) -> StatConfig:
    text = path.read_text(encoding="utf-8")
    return StatConfig(yaml.safe_load(text), hashlib.sha256(text.encode("utf-8")).hexdigest())


# ------------------------------------------------------------------------------------------------ fold fitting
def _stitch(n: int, parts: list[tuple[np.ndarray, MatrixPrediction]]) -> MatrixPrediction:
    """Assemble per-competition predictions into one prediction aligned with the eval rows."""
    g = parts[0][1].matrices.shape[1]
    out = MatrixPrediction(np.zeros((n, g, g)), np.zeros(n), np.zeros(n), np.zeros(n, bool), np.zeros(n, bool), np.zeros(n), np.zeros(n))
    for idx, p in parts:
        for f in ("matrices", "lam_home", "lam_away", "seen_home", "seen_away", "tail_mass", "raw_sum"):
            getattr(out, f)[idx] = getattr(p, f)
    return out


def _fit_dc(spec: DCSpec, name: str, rows, rest) -> tuple[RidgePoissonDC | None, dict]:
    try:
        mod = RidgePoissonDC(spec, name).fit(rows, rest=rest)
        return mod, {"status": mod.status, **mod.diagnostics}
    except NotTrained as e:
        return None, {"status": NOT_TRAINED, "reason": str(e)}


def _logloss_1x2(p: np.ndarray, y: np.ndarray) -> float:
    return float(-np.mean(np.log(np.clip(p[np.arange(len(y)), y], 1e-15, 1.0))))


def learn_blend_weights(scfg: StatConfig, fold: Fold, rest: dict, comps: list[str]) -> tuple[dict[str, float], dict]:
    """w per competition on the inner-validation block (the last training season): fit B and the local DC on the earlier
    seasons only, predict the validation season, pick the 1X2-log-loss-best w on a grid, shrink it by n / (n + k). The eval
    season is never touched."""
    cfg_b = scfg.raw["blend"]
    fit_rows = [r for r in fold.train_rows if r.season != fold.inner_validation_season]
    val_rows = [r for r in fold.train_rows if r.season == fold.inner_validation_season]
    info: dict = {"validation_season": fold.inner_validation_season, "fit_matches": len(fit_rows), "validation_matches": len(val_rows)}
    weights = dict.fromkeys(comps, 0.0)
    b_mod, b_diag = _fit_dc(scfg.dc_spec(competition_terms=True, country_levels=True), "B_inner", fit_rows, None)
    if b_mod is None:
        info["status"] = "B_NOT_TRAINED_ON_INNER_FIT: all weights 0"
        info["b_diag"] = b_diag
        return weights, info
    per = {}
    for c in comps:
        v = [r for r in val_rows if r.league_id == c]
        tr_c = [r for r in fit_rows if r.league_id == c]
        entry = {"validation_matches": len(v), "local_fit_matches": len(tr_c)}
        if len(v) < cfg_b["min_validation_matches"] or len(tr_c) < scfg.raw["tiers"]["min_local_fit_matches"]:
            entry["weight"] = 0.0
            entry["reason"] = "too little validation or local history: weight 0"
            per[c] = entry
            continue
        c_mod, _ = _fit_dc(scfg.dc_spec(), f"C_inner_{c}", tr_c, None)
        if c_mod is None:
            entry["weight"] = 0.0
            entry["reason"] = "local inner fit NOT_TRAINED: weight 0"
            per[c] = entry
            continue
        pb, pc = b_mod.predict(v).one_x_two(), c_mod.predict(v).one_x_two()
        y = np.array([r.outcome for r in v])
        grid = np.arange(0.0, 1.0 + 1e-9, cfg_b["weight_grid_step"])
        losses = [_logloss_1x2((1 - w) * pb + w * pc, y) for w in grid]
        best = float(grid[int(np.argmin(losses))])
        shrunk = best * len(v) / (len(v) + cfg_b["shrink_k"])
        entry.update({"best_raw_weight": best, "weight": shrunk, "validation_log_loss_b": losses[0], "validation_log_loss_c": losses[-1],
                      "validation_log_loss_at_best": float(min(losses))})  # fmt: skip
        per[c] = entry
        weights[c] = shrunk
    info["per_competition"] = per
    info["status"] = "LEARNED"
    return weights, info


def run_fold(scfg: StatConfig, bcfg: BenchConfig, fold: Fold) -> tuple[FoldResult, dict]:
    train, ev = list(fold.train_rows), list(fold.eval_rows)
    n = len(ev)
    y1 = np.array([r.outcome for r in ev])
    rest = rest_features(train + ev, scfg.raw["dc"]["rest_cap_days"], scfg.raw["dc"]["rest_reference_days"])
    diag: dict = {"fit_status": {}, "fallbacks": {}}
    timings: dict = {}
    preds: dict[str, MatrixPrediction] = {}

    # -- bridge availability for this fold (international history before the cutoff)
    uefa_rows = [r for r in train if r.league_id in UEFA_IDS]
    per_country: dict[str, int] = {}
    for r in uefa_rows:
        for t in (r.home_id, r.away_id):
            per_country[country_of(t)] = per_country.get(country_of(t), 0) + 1
    bridge_ok = len(uefa_rows) >= scfg.raw["bridge"]["min_uefa_matches_total"]
    bridge = {
        "status": "AVAILABLE" if bridge_ok else "NOT_AVAILABLE: too few international matches; global + competition prior, UNCERTAIN",
        "uefa_training_matches": len(uefa_rows),
        "countries_with_international_matches": len(per_country),
    }

    # -- global DC family
    specs = {
        "A_dc": scfg.dc_spec(),
        "A_dc_norho": scfg.dc_spec(use_rho=False),
        "A_dc_rest": scfg.dc_spec(use_rest=True),
        "A_dc_shrink": scfg.dc_spec(l2_team=scfg.raw["dc"]["l2_team"] * scfg.raw["dc"]["shrink_multiplier"]),
        "B_dc_league": scfg.dc_spec(competition_terms=True, country_levels=bridge_ok),
        "B_dc_league_nohome": scfg.dc_spec(competition_terms=True, home_dev=False, country_levels=bridge_ok),
    }
    models: dict[str, RidgePoissonDC] = {}
    for name, spec in specs.items():
        t0 = time.perf_counter()
        mod, d = _fit_dc(spec, name, train, rest if spec.use_rest else None)
        timings[name] = {"fit_s": time.perf_counter() - t0}
        diag["fit_status"][name] = d
        if mod is None:
            if name == "A_dc":
                raise SystemExit(f"the reference A_dc did not train in fold {fold.eval_season}: {d['reason']} (fail closed)")
            diag["fallbacks"][name] = "A_dc"
            continue
        models[name] = mod
    for name in specs:
        src = models.get(name) or models["A_dc"]
        preds[name] = src.predict(ev, rest=rest if specs[name].use_rest and name in models else None)
    if "B_dc_league" in models and bridge_ok:
        m = models["B_dc_league"]
        sl = m._sl
        rows = []
        for c in m.countries:
            a, d = float(m.theta[sl["alpha"]][m.c_idx[c]]), float(m.theta[sl["delta"]][m.c_idx[c]])
            cnt = per_country.get(c, 0)
            rows.append({"country": c, "attack_offset": a, "defense_offset": d, "net_strength": a + d, "international_matches": cnt,
                         "status": "UNCERTAIN" if cnt < scfg.raw["bridge"]["min_uefa_matches_per_country"] else "AVAILABLE"})  # fmt: skip
        bridge["countries"] = sorted(rows, key=lambda r: -r["net_strength"])
    diag["bridge"] = bridge

    # -- local DC per competition, then the blend
    comps = sorted({r.league_id for r in ev})
    tiers = scfg.raw["tiers"]
    local_parts, local_status = [], {}
    for c in comps:
        idx = np.array([i for i, r in enumerate(ev) if r.league_id == c])
        tr_c = [r for r in train if r.league_id == c]
        rows_c = [ev[i] for i in idx]
        mod = None
        if len(tr_c) >= tiers["min_local_fit_matches"]:
            t0 = time.perf_counter()
            mod, d = _fit_dc(scfg.dc_spec(), f"C_{c}", tr_c, None)
            timings[f"C_{c}"] = {"fit_s": time.perf_counter() - t0}
        else:
            d = {"status": NOT_TRAINED, "reason": f"{len(tr_c)} local matches < {tiers['min_local_fit_matches']}"}
        d["local_training_matches"] = len(tr_c)
        local_status[c] = d
        if mod is None:
            diag["fallbacks"][f"C_dc_local:{c}"] = "B_dc_league"
            local_parts.append((idx, preds["B_dc_league"].take(idx)))
        else:
            local_parts.append((idx, mod.predict(rows_c)))
    diag["local_fit_status"] = local_status
    preds["C_dc_local"] = _stitch(n, local_parts)
    weights, blend_info = learn_blend_weights(scfg, fold, rest, comps)
    for c in comps:
        if local_status[c]["status"] == NOT_TRAINED:
            weights[c] = 0.0
    w_rows = np.array([weights[r.league_id] for r in ev])
    preds["D_blend"] = preds["B_dc_league"].mix(preds["C_dc_local"], w_rows)
    diag["blend"] = {**blend_info, "applied_weights": weights}

    # -- Elo family and references
    probs_1x2: dict[str, np.ndarray] = {}
    t0 = time.perf_counter()
    e_mod, e_diag = None, {}
    try:
        e_mod = TimeAwareElo(scfg.elo_spec(), "E_elo_bridge", UEFA_IDS).fit(train)
        probs, unc = e_mod.predict_proba(ev)
        e_diag = {"status": e_mod.status, **e_mod.diagnostics, "uncertain_predictions": int(sum(unc)),
                  "uefa_country_offsets": {k: round(v, 3) for k, v in sorted(e_mod.state.offsets.items())}}  # fmt: skip
        probs_1x2["E_elo_bridge"] = probs
    except NotTrained as e:
        e_diag = {"status": NOT_TRAINED, "reason": str(e)}
    timings["E_elo_bridge"] = {"fit_s": time.perf_counter() - t0}
    diag["fit_status"]["E_elo_bridge"] = e_diag
    if "E_elo_bridge" not in probs_1x2:
        raise SystemExit(f"E_elo_bridge did not train in fold {fold.eval_season}: {e_diag.get('reason')} (fail closed)")
    f_parts, f_status = np.zeros((n, 3)), {}
    for c in comps:
        idx = np.array([i for i, r in enumerate(ev) if r.league_id == c])
        tr_c = [r for r in train if r.league_id == c]
        try:
            if len(tr_c) < tiers["min_local_fit_matches"]:
                raise NotTrained(f"{len(tr_c)} local matches < {tiers['min_local_fit_matches']}")
            mod = TimeAwareElo(scfg.elo_spec(bridge=False), f"F_{c}", frozenset()).fit(tr_c)
            f_parts[idx], unc = mod.predict_proba([ev[i] for i in idx])
            f_status[c] = {"status": mod.status, "local_training_matches": len(tr_c), "uncertain_predictions": int(sum(unc))}
        except NotTrained as e:
            f_parts[idx] = probs_1x2["E_elo_bridge"][idx]
            diag["fallbacks"][f"F_elo_local:{c}"] = "E_elo_bridge"
            f_status[c] = {"status": NOT_TRAINED, "reason": str(e), "local_training_matches": len(tr_c)}
    probs_1x2["F_elo_local"] = f_parts
    diag["fit_status"]["F_elo_local"] = f_status

    model_cfg = load_config("model")
    refs = {m.model_id: m for m in build_models(["historical_prior", "elo"], elo_config=model_cfg.elo)}
    for rid, key in (("historical_prior", "historical_prior"), ("elo", "elo_s2")):
        refs[rid].fit(train)
        probs_1x2[key] = refs[rid].predict_proba(ev)

    # -- targets
    line = bcfg.over_under_line
    probs_over, probs_btts = {}, {}
    over_rate = np.mean([(r.home_goals + r.away_goals) > line for r in train])
    btts_rate = np.mean([(r.home_goals > 0 and r.away_goals > 0) for r in train])
    probs_over["train_over_rate"] = np.full(n, over_rate)
    probs_btts["train_btts_rate"] = np.full(n, btts_rate)
    probs_over["always_over"], probs_over["always_under"] = np.ones(n), np.zeros(n)
    probs_btts["always_yes"], probs_btts["always_no"] = np.ones(n), np.zeros(n)
    extras = {}
    for name in MATRIX_MODELS:
        p = preds[name]
        probs_1x2[name] = np.clip(p.one_x_two(), 0.0, 1.0)
        probs_1x2[name] = probs_1x2[name] / probs_1x2[name].sum(axis=1, keepdims=True)
        probs_over[f"{name}_totals"] = np.clip(p.over(line), 0.0, 1.0)
        probs_btts[f"{name}_goals"] = np.clip(p.btts(), 0.0, 1.0)
        extras[name] = {"p00": p.p_score(0, 0), "p11": p.p_score(1, 1), "top": p.top_score(), "tail": p.tail_mass, "raw_sum": p.raw_sum,
                        "min_cell": float(p.matrices.min()), "sum_dev": float(np.abs(p.matrices.sum(axis=(1, 2)) - 1).max()),
                        "unseen_rows": int((~(p.seen_home & p.seen_away)).sum())}  # fmt: skip
    del y1
    return FoldResult(fold, probs_1x2, probs_over, probs_btts, diag, timings), extras


# ------------------------------------------------------------------------------------------------ analysis
def score_audit(ev: dict, extras: list[dict], results: list[FoldResult]) -> dict:
    """0-0 and 1-1 sub-groups and exact-score hit rates: predicted mass against what happened, per matrix model."""
    rows: list[EvalRow] = ev["rows"]
    gh = np.array([r.home_goals for r in rows])
    ga = np.array([r.away_goals for r in rows])
    leagues = np.array([r.league_id for r in rows])
    out = {}
    for name in MATRIX_MODELS:
        p00 = np.concatenate([e[name]["p00"] for e in extras])
        p11 = np.concatenate([e[name]["p11"] for e in extras])
        top = sum([e[name]["top"] for e in extras], [])
        entry = {}
        for gname, sel in [("ALL", np.ones(len(rows), bool)), *((f"league={lg}", leagues == lg) for lg in sorted(set(leagues)))]:
            obs00, obs11 = ((gh == 0) & (ga == 0))[sel], ((gh == 1) & (ga == 1))[sel]
            hits = np.array([t == f"{a}-{b}" for t, a, b in zip(top, gh, ga, strict=True)])[sel]
            entry[gname] = {
                "n": int(sel.sum()),
                "p_0_0_mean_predicted": round(float(p00[sel].mean()), 5), "p_0_0_observed": round(float(obs00.mean()), 5),
                "p_1_1_mean_predicted": round(float(p11[sel].mean()), 5), "p_1_1_observed": round(float(obs11.mean()), 5),
                "log_loss_when_0_0": round(float(-np.log(np.clip(p00[sel][obs00], 1e-15, 1)).mean()), 4) if obs00.any() else None,
                "log_loss_when_1_1": round(float(-np.log(np.clip(p11[sel][obs11], 1e-15, 1)).mean()), 4) if obs11.any() else None,
                "top_score_hit_rate": round(float(hits.mean()), 5),
            }  # fmt: skip
        out[name] = entry
    validity = {}
    for name in MATRIX_MODELS:
        validity[name] = {
            "min_cell_probability": min(e[name]["min_cell"] for e in extras),
            "max_abs_matrix_sum_deviation_after_normalisation": max(e[name]["sum_dev"] for e in extras),
            "pre_normalisation_sum_range": [min(float(e[name]["raw_sum"].min()) for e in extras), max(float(e[name]["raw_sum"].max()) for e in extras)],
            "max_tail_mass_outside_grid": max(float(e[name]["tail"].max()) for e in extras),
            "rows_with_a_club_unseen_in_training": sum(e[name]["unseen_rows"] for e in extras),
        }  # fmt: skip
    return {"definition": "0-0 / 1-1 sub-groups, exact-score hit rate of the most likely score, matrix validity", "by_model": out, "matrix_validity": validity}


HYPOTHESES = [
    # id, competition, target, variant, base, text
    ("H1_serie_a_low_scoring_draws", "ITA1", "1x2", "A_dc", "A_dc_norho", "Serie A: the Dixon-Coles low-score correction (rho) improves 1X2"),
    ("H2_premier_league_rest_congestion", "ENG1", "1x2", "A_dc_rest", "A_dc", "Premier League: rest days before a match carry information"),
    ("H3_la_liga_home_advantage", "ESP1", "1x2", "B_dc_league", "B_dc_league_nohome", "La Liga: a competition-specific home advantage improves 1X2"),
    ("H4_bundesliga_goal_density", "GER1", "over_under", "B_dc_league_totals", "A_dc_totals", "Bundesliga: a competition goal-rate intercept improves over/under 2.5"),
    ("H5_ligue_1_strength_concentration", "FRA1", "1x2", "A_dc_shrink", "A_dc", "Ligue 1: stronger shrinkage of club strengths improves 1X2"),
    ("H6_super_lig_home_away_gap", "TUR1", "1x2", "B_dc_league", "B_dc_league_nohome", "Süper Lig: a competition-specific home advantage improves 1X2"),
    ("H8_champions_league_cross_league", "UEFA_UCL", "1x2", "B_dc_league", "A_dc", "Champions League: competition + country levels (bridge) improve 1X2"),
    ("H9_europa_league_cross_league", "UEFA_UEL", "1x2", "B_dc_league", "A_dc", "Europa League: competition + country levels (bridge) improve 1X2"),
    ("H10_conference_league_cross_league", "UEFA_UECL", "1x2", "B_dc_league", "A_dc", "Conference League: competition + country levels (bridge) improve 1X2"),
]


def _loss(ev: dict, bcfg: BenchConfig, target: str, model: str, sel: np.ndarray) -> np.ndarray:
    p = ev["probs"][target][model][sel]
    y = ev["y"][target][sel]
    return bm.rows_log_loss_1x2(p, y) if target == "1x2" else bm.binary_log_loss_rows(p, y)


def hypotheses_report(ev: dict, bcfg: BenchConfig) -> dict:
    rows: list[EvalRow] = ev["rows"]
    leagues = np.array([r.league_id for r in rows])
    blocks = np.array([bm.iso_week_block(r.kickoff_utc) for r in rows])
    out = []
    for hid, comp, target, variant, base, text in HYPOTHESES:
        sel = leagues == comp
        v_name = variant
        if not sel.any():
            out.append({"id": hid, "competition": comp, "target": target, "hypothesis": text, "verdict": "NO_EVAL_ROWS",
                        "reason": "the competition has no dev out-of-sample rows in the requested window"})  # fmt: skip
            continue
        pd_ = bm.paired_block_bootstrap(_loss(ev, bcfg, target, v_name, sel), _loss(ev, bcfg, target, base, sel), blocks[sel],
                                        bcfg.bootstrap_samples, bcfg.bootstrap_seed, bcfg.bootstrap_alpha)  # fmt: skip
        verdict = "SUPPORTED" if pd_.upper < 0 else ("NOT_SUPPORTED" if pd_.lower > 0 else "INCONCLUSIVE")
        out.append({"id": hid, "competition": comp, "target": target, "hypothesis": text, "variant": v_name, "base": base,
                    "delta_log_loss_variant_minus_base": round(pd_.delta, 6), "ci95": [round(pd_.lower, 6), round(pd_.upper, 6)],
                    "n": pd_.n, "blocks": pd_.n_blocks, "verdict": verdict,
                    "rule": "SUPPORTED: the whole 95% block interval is below 0 on dev out-of-sample data; NOT_SUPPORTED: above 0; otherwise INCONCLUSIVE"})  # fmt: skip
    out.append({"id": "H7_super_lig_squad_continuity", "competition": "TUR1", "hypothesis": "Süper Lig: squad continuity matters",
                "verdict": "NOT_TESTABLE", "reason": "the licensed universe has no squad, transfer or lineup data"})  # fmt: skip
    return {"definition": "league hypotheses tested by ablation on dev out-of-sample rows only; none is coded as a fact and no feature "
                          "weight is fixed from them", "hypotheses": out}  # fmt: skip


def decisions_report(scfg: StatConfig, folds: list[Fold], metrics: dict, deltas: dict, deltas_vs_elo: dict, results: list[FoldResult]) -> dict:
    """Model / fallback decision for every competition: the global reference stays unless a candidate shows out-of-sample
    superiority on dev rows (final holdout untouched: promotion to production is Sprint 5, this is a DEV-only verdict)."""
    tiers = scfg.raw["tiers"]
    dec = scfg.raw["decision"]
    last = folds[-1]
    out = {}
    comps = sorted({r.league_id for f in folds for r in f.eval_rows})
    candidates = ("B_dc_league", "C_dc_local", "D_blend", "E_elo_bridge", "F_elo_local")
    fallback_comps: dict[str, set] = {}
    for r in results:
        for k in r.diagnostics["fallbacks"]:
            if ":" in k:
                fallback_comps.setdefault(k.split(":", 1)[0], set()).add(k.split(":", 1)[1])
    for c in comps:
        n_train = sum(1 for r in last.train_rows if r.league_id == c)
        tier = ("LOCAL_OFF_UNDER_500" if n_train < tiers["local_off_below"] else
                "LOCAL_CANDIDATE_500_TO_1500_OOS_PROOF_REQUIRED" if n_train < tiers["local_candidate_below"] else "LOCAL_CANDIDATES_OVER_1500")  # fmt: skip
        g = f"league={c}"
        ref_ece = metrics["1x2"][g]["A_dc"]["ece_raw"]
        table = {}
        eligible = []
        for cand in candidates:
            d = deltas["1x2"].get(g, {}).get(cand)
            if d is None:
                continue
            ll, br = d["log_loss"], d["brier"]
            ece = metrics["1x2"][g][cand]["ece_raw"]
            reasons = []
            if cand in LOCAL_TYPES and tier == "LOCAL_OFF_UNDER_500":
                reasons.append(f"local models are production-ineligible under {tiers['local_off_below']} training matches")
            if c in fallback_comps.get(cand, set()):
                reasons.append("a fold fell back (NOT_TRAINED)")
            if not ll["upper"] < 0:
                reasons.append("log-loss paired interval is not entirely below 0")
            if not br["delta_vs_reference"] < 0:
                reasons.append("Brier is not better")
            if ece - ref_ece > dec["max_ece_worsening"]:
                reasons.append("raw ECE is worse than the reference by more than the allowed margin")
            table[cand] = {"delta_log_loss_vs_A_dc": ll["delta_vs_reference"], "ci95": [ll["lower"], ll["upper"]],
                           "delta_brier_vs_A_dc": br["delta_vs_reference"], "ece_raw": ece, "blocked_by": reasons}  # fmt: skip
            if not reasons:
                eligible.append((ll["delta_vs_reference"], cand))
        elo_d = deltas_vs_elo["1x2"].get(g, {})
        best = min(eligible)[1] if eligible else None
        out[c] = {
            "training_matches_last_fold": n_train, "volume_tier": tier,
            "decision": f"CANDIDATE_{best}" if best else "GLOBAL_A_dc",
            "decision_scope": "dev out-of-sample evidence only; the frozen holdout is untouched; promotion is decided in Sprint 5",
            "rationale": (f"{best} beats A_dc on the whole 95% block interval (log loss {table[best]['delta_log_loss_vs_A_dc']:+.4f}), Brier and raw ECE do not "
                          "contradict it") if best else "no candidate showed out-of-sample superiority over the global reference without a blocking reason; global model kept",  # fmt: skip
            "candidates_vs_A_dc": table,
            "elo_s2_vs_candidates": {k: {"delta_log_loss_candidate_minus_elo_s2": v["log_loss"]["delta_vs_reference"], "ci95": [v["log_loss"]["lower"], v["log_loss"]["upper"]]}
                                     for k, v in elo_d.items() if k in candidates},  # fmt: skip
        }
    return {"reference": "A_dc (global ridge Dixon-Coles); elo_s2 is reported alongside because Sprint 2 found it the stronger 1X2 reference",
            "rule": "a candidate is eligible only with: paired log-loss interval entirely < 0, Brier better, raw ECE not worse by more than "
                    f"{dec['max_ece_worsening']}, tier allowing it, and no fold fallback", "competitions": out}  # fmt: skip


def uefa_report(results: list[FoldResult]) -> dict:
    out = {}
    for r in results:
        b = r.diagnostics["bridge"]
        out[r.fold.eval_season] = b
    avail = [k for k, v in out.items() if v["status"] == "AVAILABLE"]
    return {"source": "country offsets of B_dc_league, identified only by matches between clubs of different countries "
                      "(UEFA) played before each fold's cutoff", "folds": out,
            "overall": "AVAILABLE in folds " + ", ".join(avail) if avail else "NOT_AVAILABLE"}  # fmt: skip


def model_cards(scfg: StatConfig, results: list[FoldResult], decisions: dict) -> str:
    d, e, b, t = scfg.raw["dc"], scfg.raw["elo"], scfg.raw["blend"], scfg.raw["tiers"]
    lines = ["# Sprint 3 model cards (generated by `python -m src.league_models.stat_benchmark`)", "",
             "All candidates: score-only (openfootball scores, licence-clean), fitted per outer fold on matches available at the fold cutoff, "
             "parameters frozen for the eval season, `BACKTEST_LIMITED`, DEV-only evidence. None is in production.", ""]  # fmt: skip
    cards = [
        ("A_dc", "Global ridge Poisson / Dixon-Coles. Reference of Sprint 3.", f"L2 team {d['l2_team']}, rho on 0-0/0-1/1-0/1-1, grid 0..{d['max_goals']}."),
        ("A_dc_norho / A_dc_rest / A_dc_shrink", "Ablations of A_dc: no rho; rest-days covariate; team ridge x" + str(d["shrink_multiplier"]), "Hypothesis tests only."),
        ("B_dc_league", "A_dc + competition intercept and home advantage + country levels (the cross-league bridge).", f"L2 country {d['l2_country']}, competition {d['l2_competition']}, home deviation {d['l2_home_dev']}; country levels only when the fold has >= {scfg.raw['bridge']['min_uefa_matches_total']} international training matches."),
        ("B_dc_league_nohome", "B_dc_league without the competition home-advantage deviation.", "Hypothesis test only."),
        ("C_dc_local", "One DC per competition on its own training matches.", f"NOT_TRAINED below {t['min_local_fit_matches']} local matches (explicit fallback to B_dc_league). Production-ineligible below {t['local_off_below']}."),
        ("D_blend", "(1 - w) * B_dc_league + w * C_dc_local, matrix level.", f"w per competition on the inner-validation season, grid step {b['weight_grid_step']}, shrunk by n/(n+{b['shrink_k']}); 0 below {b['min_validation_matches']} validation matches."),
        ("E_elo_bridge", "Per-country time-aware Elo with UEFA-learned country offsets.", f"K {e['k_factor']}, home advantage {e['home_advantage']}, goal-difference multiplier, season reversion {e['season_reversion']} to the country mean, promoted prior -{e['promoted_penalty']}, bridge K {e['bridge_k']}."),
        ("F_elo_local", "One Elo per competition on its own history.", "No country offsets; explicit fallback to E_elo_bridge when NOT_TRAINED."),
    ]
    for name, what, params in cards:
        statuses = []
        for r in results:
            fs = r.diagnostics["fit_status"].get(name.split(" / ")[0], {})
            statuses.append(f"{r.fold.eval_season}: {fs.get('status', 'see diagnostics') if isinstance(fs, dict) else 'see diagnostics'}")
        lines += [f"## {name}", what, "", f"- Parameters: {params}", f"- Per-fold status: {'; '.join(statuses)}",
                  "- Known limits: score-only features; UEFA finals are scored with a home advantage; clubs unseen in training get league-average strength and are counted.", ""]  # fmt: skip
    lines += ["## Decisions", "", "| Competition | Training matches (last fold) | Tier | Decision |", "|---|---|---|---|"]
    for c, v in decisions["competitions"].items():
        lines.append(f"| `{c}` | {v['training_matches_last_fold']} | {v['volume_tier']} | {v['decision']} |")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------------------------------------ run
def fit_health(results: list[FoldResult]) -> dict:
    out = {}
    for r in results:
        fs = r.diagnostics["fit_status"]
        out[r.fold.eval_season] = {
            name: {k: v for k, v in (fs[name] if isinstance(fs[name], dict) else {}).items() if k in ("status", "converged", "n_iter", "mean_abs_gradient_per_row", "rho", "reason", "mapping_optimizer")}
            for name in (*MATRIX_MODELS[:6], "E_elo_bridge")
            if name in fs
        } | {"fallbacks": r.diagnostics["fallbacks"], "local_dc": {c: v["status"] for c, v in r.diagnostics["local_fit_status"].items()},
             "blend_weights": r.diagnostics["blend"]["applied_weights"]}  # fmt: skip
    return out


def prediction_lines(results: list[FoldResult], extras: list[dict], snapshot_id: str, line: float) -> list[str]:
    lines = []
    for r, ex in zip(results, extras, strict=True):
        y = outcomes_for(list(r.fold.eval_rows), line)
        for i, row in enumerate(r.fold.eval_rows):
            base = {"fixture_id": row.fixture_id, "league": row.league_id, "season": row.season, "fold_eval_season": r.fold.eval_season,
                    "snapshot_id": snapshot_id, "track": "historical_backtest", "as_of_quality": "BACKTEST_LIMITED",
                    "y_1x2": int(y["1x2"][i]), "y_over": int(y["over_under"][i]), "y_btts": int(y["btts"][i])}  # fmt: skip
            for model, p in r.probs_1x2.items():
                rec = {**base, "model": model, "p_1x2": [round(float(v), 8) for v in p[i]]}
                if model in ex:
                    rec |= {"p_over": round(float(r.probs_over[f"{model}_totals"][i]), 8), "p_btts": round(float(r.probs_btts[f"{model}_goals"][i]), 8),
                            "p_0_0": round(float(ex[model]["p00"][i]), 8), "p_1_1": round(float(ex[model]["p11"][i]), 8), "top_score": ex[model]["top"][i]}  # fmt: skip
                lines.append(json.dumps(rec, sort_keys=True))
    return lines


def run(
    root: Path,
    as_of: datetime,
    output_dir: Path | None,
    *,
    dry_run: bool = False,
    eval_start: str | None = None,
    eval_end: str | None = None,
    bench_config_path: Path | None = None,
    stat_config_path: Path | None = None,
    docs_dir: Path | None = None,
    audit: AuditResult | None = None,
) -> dict:
    """`audit` lets a caller reuse a collected Sprint 1 audit (tests pass a synthetic one). The licence gate runs first."""
    bcfg = load_bench_config(bench_config_path) if bench_config_path else load_bench_config()
    scfg = load_stat_config(stat_config_path) if stat_config_path else load_stat_config()
    league_cfg = load_league_config()
    manifest = load_manifest()
    training_gate(manifest, LICENSED_PROVIDERS, "production_ml")
    if audit is None:
        directory = TeamDirectory.load(config_dir_for(root) / "team_aliases.yaml")
        audit = collect(root, league_cfg, directory, manifest, as_of)
    universe = build_universe(bcfg, audit)
    folds = make_folds(bcfg, universe, league_cfg.result_lag_hours)
    if eval_start:
        folds = [f for f in folds if f.eval_season >= eval_start]
    if eval_end:
        folds = [f for f in folds if f.eval_season <= eval_end]
    if not folds:
        raise SystemExit("no fold satisfies the requested window and the protocol minimums (fail closed)")
    assert_disjoint(folds, universe)
    proto = protocol_manifest(bcfg, universe, folds)
    s2_path = root / "docs" / "league_models" / "s2_protocol_manifest.json"
    parity = {"status": "NOT_VERIFIED: committed Sprint 2 protocol manifest not found"}
    if s2_path.is_file():
        s2 = json.loads(s2_path.read_text(encoding="utf-8"))
        s2_folds = {f["eval_season"]: f for f in s2["folds"]}
        same = all(s2_folds.get(f["eval_season"]) == f for f in proto["folds"])
        parity = {"status": "IDENTICAL_TO_SPRINT_2" if same else "DIFFERENT_FROM_SPRINT_2", "compared_folds": [f["eval_season"] for f in proto["folds"]]}
        if not same:
            raise SystemExit("the folds differ from the committed Sprint 2 folds: Sprint 3 must use the same folds (fail closed)")
    proto["sprint2_fold_parity"] = parity
    proto["stat_config_fingerprint_sha256"] = scfg.fingerprint
    stage = bcfg.default_stage
    leaks = {f.eval_season: len(history_leak_check([*f.train_rows, *f.eval_rows], league_cfg.result_lag_hours, stage)) for f in folds}
    snapshot_digest = hashlib.sha256("".join(f.sha256 for f in audit.source_files).encode()).hexdigest()
    snapshot_id = f"snap-{snapshot_digest[:12]}"
    plan = {"dry_run": dry_run, "snapshot_id": snapshot_id, "stage": stage.value, "folds": proto["folds"], "universe_counts": proto["universe_counts"],
            "stat_config_fingerprint_sha256": scfg.fingerprint, "sprint2_fold_parity": parity, "replay_violations_per_fold": leaks}  # fmt: skip
    if dry_run:
        return {"plan": plan}
    if any(leaks.values()):
        raise SystemExit(f"replay violations found: {leaks} (fail closed)")
    pairs = [run_fold(scfg, bcfg, f) for f in folds]
    results = [p[0] for p in pairs]
    extras = [p[1] for p in pairs]
    ref_cfg = dataclasses.replace(bcfg, reference_model=scfg.raw["reference_model"])
    ev = collect_eval(results, ref_cfg)
    metrics, deltas, reliab = score_all(ev, ref_cfg, ("1x2", "over_under", "btts"))
    elo_cfg = dataclasses.replace(bcfg, reference_model="elo_s2")
    _, deltas_vs_elo, _ = score_all(ev, elo_cfg, ("1x2",))
    decisions = decisions_report(scfg, folds, metrics, deltas, deltas_vs_elo, results)
    hyp = hypotheses_report(ev, ref_cfg)
    audit_scores = score_audit(ev, extras, results)
    uefa = uefa_report(results)
    sha, dirty = git_sha(root)
    cards = model_cards(scfg, results, decisions)
    files: dict[str, str] = {
        "fold_predictions.jsonl": "\n".join(prediction_lines(results, extras, snapshot_id, bcfg.over_under_line)) + "\n",
        "stat_metrics.json": dumps({"protocol_version": scfg.raw["protocol_version"], "snapshot_id": snapshot_id, "primary_metric": "log_loss",
                                    "reference_model": scfg.raw["reference_model"], "no_single_winner": True, "metrics": metrics,
                                    "fit_health": fit_health(results)}),  # fmt: skip
        "paired_deltas.json": dumps({"definition": "delta = model - reference on the same fixtures; negative = model better",
                                     "reference": scfg.raw["reference_model"], "deltas": deltas, "deltas_vs_elo_s2": deltas_vs_elo}),  # fmt: skip
        "reliability.json": dumps({"bins": bcfg.calibration_bins, "note": "raw probabilities, nothing calibrated in Sprint 3", "curves": reliab}),
        "score_audit.json": dumps(audit_scores),
        "uefa_bridge.json": dumps(uefa),
        "league_decisions.json": dumps(decisions),
        "hypotheses.json": dumps(hyp),
        "protocol_manifest.json": dumps(proto),
        "model_cards.md": cards,
        "leakage_audit.json": dumps({"stage": stage.value, "replay_violations_per_fold": leaks}),
    }
    hashes = {name: hashlib.sha256(text.encode("utf-8")).hexdigest() for name, text in sorted(files.items())}
    run_manifest = {
        "protocol_version": scfg.raw["protocol_version"], "snapshot_id": snapshot_id, "data_snapshot_sha256": snapshot_digest,
        "license_manifest_sha256": manifest_hash(manifest), "bench_config_fingerprint_sha256": bcfg.fingerprint,
        "stat_config_fingerprint_sha256": scfg.fingerprint, "config_fingerprint_sha256": scfg.fingerprint, "stage": stage.value,
        "as_of": as_of.isoformat(), "timezone": "UTC",
        "random_seed": {"bootstrap": bcfg.bootstrap_seed, "models": "deterministic (L-BFGS-B / BFGS, no randomness)"},
        "git_sha": sha, "git_dirty_at_run": dirty, "python": sys.version.split()[0], "numpy": np.__version__,
        "dependency_lock_sha256": sha256_file(root / "requirements.lock") if (root / "requirements.lock").is_file() else None,
        "matches_scored": len(ev["rows"]), "folds": [f.eval_season for f in folds], "output_hashes": hashes,
        "reproducibility_note": "timings.json is excluded from the hashes (wall-clock)",
    }  # fmt: skip
    files["run_manifest.json"] = dumps(run_manifest)
    summary = {"plan": plan, "run_manifest": run_manifest,
               "decisions": {c: v["decision"] for c, v in decisions["competitions"].items()}}  # fmt: skip
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            (output_dir / name).write_text(text, encoding="utf-8", newline="\n")
        (output_dir / "timings.json").write_text(dumps({r.fold.eval_season: r.timings for r in results}), encoding="utf-8", newline="\n")
        for name, expected in hashes.items():
            if sha256_file(output_dir / name) != expected:
                raise RuntimeError(f"hash mismatch after writing {name}")
        summary["registry_entry"] = register_run(output_dir, run_manifest)
    if docs_dir is not None:
        docs_dir.mkdir(parents=True, exist_ok=True)
        for name in ("protocol_manifest.json", "stat_metrics.json", "paired_deltas.json", "reliability.json", "score_audit.json", "uefa_bridge.json",
                     "league_decisions.json", "hypotheses.json", "leakage_audit.json", "run_manifest.json", "model_cards.md"):  # fmt: skip
            (docs_dir / f"s3_{name}").write_text(files[name], encoding="utf-8", newline="\n")
    return summary


def compare_to_committed(out_dir: Path, docs_dir: Path, atol: float) -> dict:
    """Cross-platform check against the committed s3_* files (exact for structural files, numeric within `atol` for metrics)."""
    exact_bad, numeric_bad = [], {}
    for name in EXACT_FILES:
        if sha256_file(out_dir / name) != sha256_file(docs_dir / f"s3_{name}"):
            exact_bad.append(name)
    for name in NUMERIC_FILES:
        bad, tr = [], []
        _walk(json.loads((docs_dir / f"s3_{name}").read_text(encoding="utf-8")), json.loads((out_dir / name).read_text(encoding="utf-8")), "", atol, bad, tr)
        if bad:
            numeric_bad[name] = bad[:25]
    return {"atol": atol, "exact_files_differing": exact_bad, "numeric_differences": numeric_bad,
            "not_compared": {"fold_predictions.jsonl": "not committed (large); covered by the metrics compared here"},
            "ok": not exact_bad and not numeric_bad}  # fmt: skip


def reproduce(root: Path, previous: Path, as_of: datetime, a) -> int:
    recorded = json.loads((root / previous / "run_manifest.json").read_text(encoding="utf-8"))["output_hashes"]
    with tempfile.TemporaryDirectory() as tmp:
        fresh = run(root, as_of, Path(tmp) / "rerun", eval_start=a.eval_start, eval_end=a.eval_end)
    diff = {k: [recorded.get(k), v] for k, v in fresh["run_manifest"]["output_hashes"].items() if recorded.get(k) != v}
    sys.stdout.write(dumps({"status": "REPRODUCED" if not diff else "NOT_REPRODUCIBLE", "compared_files": len(recorded), "differences": diff}))
    return 0 if not diff else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--eval-start", default=None)
    ap.add_argument("--eval-end", default=None)
    ap.add_argument("--output-dir", default=None)
    ap.add_argument("--docs-dir", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify", default=None, metavar="DIR")
    ap.add_argument("--reproduce", default=None, metavar="DIR")
    ap.add_argument("--compare-committed", default=None, metavar="DOCS_DIR")
    ap.add_argument("--atol", type=float, default=1e-3)
    a = ap.parse_args(argv)
    if a.verify:
        res = verify_run(Path(a.root) / a.verify)
        sys.stdout.write(dumps(res))
        return 0 if res["ok"] else 1
    as_of = datetime.fromisoformat(a.as_of.replace("Z", "+00:00")) if a.as_of else datetime.now(UTC)
    if as_of.tzinfo is None:
        sys.exit("--as-of must carry a UTC offset or Z")
    root = Path(a.root)
    if a.reproduce:
        return reproduce(root, Path(a.reproduce), as_of.astimezone(UTC), a)
    out = run(root, as_of.astimezone(UTC), (root / a.output_dir) if a.output_dir else None, dry_run=a.dry_run,
              eval_start=a.eval_start, eval_end=a.eval_end, docs_dir=(root / a.docs_dir) if a.docs_dir else None)  # fmt: skip
    sys.stdout.write(dumps(out))
    if a.compare_committed and not a.dry_run:
        if not a.output_dir:
            sys.exit("--compare-committed needs --output-dir")
        report = compare_to_committed(root / a.output_dir, root / a.compare_committed, a.atol)
        sys.stdout.write(dumps({"compare_committed": report}))
        return 0 if report["ok"] else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
