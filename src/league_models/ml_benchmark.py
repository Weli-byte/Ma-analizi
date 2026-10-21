"""Sprint 4 (ADR 0066): league-aware gradient-boosting adapters on the Sprint 2 / 3 folds.

    python -m src.league_models.ml_benchmark --as-of 2026-10-20T00:00:00Z --dry-run
    python -m src.league_models.ml_benchmark --as-of 2026-10-20T00:00:00Z --output-dir artifacts/league_models/ml/run --docs-dir docs/league_models
    python -m src.league_models.ml_benchmark --verify DIR | --reproduce DIR | --compare-committed docs/league_models

Candidates (every one is fitted per outer fold on that fold's training rows; parameters frozen before the eval season):
  A_gbm                  frozen global GBM: league-blind features, fixed parameters, fixed rounds (the reference of this sprint)
  A_gbm_tuned            the same league-blind features tuned with the same tuner and budget (the control: league awareness vs tuning)
  B_gbm_league_default   global GBM + league identity and league-season priors, fixed parameters (isolates the league effect)
  B_gbm_league           the same features, parameters tuned by Optuna on the inner-validation season only
  B_xgb_league           the same features with XGBoost, fixed parameters (backend cross-check)
  C_gbm_local            one GBM per competition on its own history (tier rules; otherwise an explicit LOCAL_INELIGIBLE fallback to B)
  D_gbm_blend            (1 - w) * B + w * C per competition and target, w learned on the inner-validation season and shrunk toward 0
  A_dc_ridge_tuned       the Sprint 3 global Dixon-Coles with its team ridge tuned on the inner-validation season (S3 finding 4)
  ablations of B         B_no_rest / B_no_league_priors / B_no_elo / B_no_home_away (league-hypothesis tests, no retuning)
  references (Sprint 3)  A_dc, B_dc_league, D_blend, E_elo_bridge, elo_s2, historical_prior and the naive totals / BTTS baselines
The score-only universe has no odds, xG or lineups: these are odds-free models; the odds-aware card is NOT_AVAILABLE. The frozen holdout
is never read and nothing is calibrated here (Sprint 5). The production models in src/models are untouched."""

import argparse
import dataclasses
import hashlib
import json
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from src.config import config_dir_for
from src.data.teams import TeamDirectory
from src.evaluation.dataset import EvalRow

from . import bench_metrics as bm
from . import stat_benchmark as sb
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
from .ml_features import FeatureConfig, FeatureMatrix, build_features, perturbation_audit
from .ml_models import (
    GBM,
    TARGETS,
    MLConfig,
    NotTrainedML,
    blend_weight,
    full_params,
    load_ml_config,
    local_eligibility,
    log_loss,
    mix,
    tune,
)
from .splits import (
    Fold,
    assert_disjoint,
    build_universe,
    dumps,
    history_leak_check,
    load_bench_config,
    make_folds,
    protocol_manifest,
)

LICENSED_PROVIDERS = sb.LICENSED_PROVIDERS
SUFFIX = {"over_under": "_totals", "btts": "_goals"}
GBM_MODELS = ("A_gbm", "A_gbm_tuned", "B_gbm_league_default", "B_gbm_league", "B_xgb_league", "C_gbm_local", "D_gbm_blend")
ABLATIONS = {"B_no_rest": "rest_congestion", "B_no_league_priors": "league_priors", "B_no_elo": "elo", "B_no_home_away": "home_away"}
OS_DEPENDENT_MODELS = ("B_xgb_league",)  # XGBoost CPU histograms differ between operating systems (first Linux CI run); reported, never compared across OS, never a decision candidate
EXACT_FILES = ("protocol_manifest.json", "feature_schema.json")
NUMERIC_FILES = ("ml_metrics.json", "paired_deltas.json", "paired_vs_s3.json", "paired_vs_control.json", "reliability.json", "error_analysis.json", "hypotheses.json",
                 "feature_importance.json", "tuning_trials.json", "league_decisions.json", "training_runs.json", "eligibility_evidence.json",
                 "leakage_audit.json")  # fmt: skip


def put(res: dict, name: str, target: str, p: np.ndarray) -> None:
    res[target][_model_key(target, name)] = p


def get(res: dict, name: str, target: str) -> np.ndarray:
    return res[target][_model_key(target, name)]


# ------------------------------------------------------------------------------------------------ one fold
class FoldRun:
    """Everything one outer fold produces. Nothing here reads an eval-season label: labels of eval rows are only handed to the scorer."""

    def __init__(self, mcfg: MLConfig, scfg: sb.StatConfig, bcfg, fold: Fold, fm: FeatureMatrix, snapshot_id: str, git: str | None, lag_hours: int) -> None:
        self.mcfg, self.scfg, self.bcfg, self.fold, self.fm, self.lag_hours = mcfg, scfg, bcfg, fold, fm, lag_hours
        self.snapshot_id, self.git = snapshot_id, git
        self.seed = int(mcfg.raw["seed"])
        self.g = mcfg.raw["gbm"]
        self.runs: list[dict] = []
        self.artifacts: dict[str, str] = {}
        self.timings: dict = {}
        self.diag: dict = {"fit_status": {}, "fallbacks": {}, "local": {}, "blend": {}, "tuning": {}, "tuning_control": {}}

    # -- helpers
    def cols(self, *drop_groups: str) -> list[int]:
        return self.fm.schema.without_groups(*drop_groups)

    def make(self, target: str, cols: list[int], params: dict, backend: str = "lightgbm") -> GBM:
        names = [self.fm.schema.names[i] for i in cols]
        return GBM(backend, target, params, names, ["league_code"] if backend == "lightgbm" else [], self.seed, self.g["threads"], self.g["class_weight"])

    def record(self, family: str, league: str, target: str, model: GBM, rounds: int, n_train: int, inner: dict | None = None) -> None:
        key = f"{self.fold.eval_season}__{family}__{target}__{league}"
        text = model.model_text()
        self.artifacts[f"{key}.txt"] = text
        self.runs.append({
            "model_family": family, "backend": model.backend, "league": league, "target": target, "fold": self.fold.eval_season,
            "snapshot_id": self.snapshot_id, "feature_schema_sha256": self.fm.schema.sha256, "features": len(model.names),
            "params": model.params, "rounds": int(rounds), "best_iteration": int(model.best_iteration), "seed": self.seed, "git_sha": self.git,
            "train_end_exclusive_utc": self.fold.fit_cutoff_utc.isoformat(), "training_rows": int(n_train), "inner_validation": inner,
            "data_license_ids": LICENSED_PROVIDERS, "artifact_file": f"models/{key}.txt",
            "artifact_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()})  # fmt: skip

    def fit_fixed(self, target: str, cols: list[int], params: dict, rounds: int, X, y, backend: str = "lightgbm") -> GBM:
        return self.make(target, cols, params, backend).fit(X[:, cols], y, rounds)

    def inner_masks(self, train: list[EvalRow]) -> tuple[np.ndarray, np.ndarray, str]:
        """(inner fit, inner validation, kind). Normally the last training season is the validation block. With one training season
        only (the first fold) there is no earlier season to fit on, so the training rows are cut in time instead: the earliest share
        fits, the rest validates, and fit rows whose result was not yet available when validation starts are dropped (purge)."""
        season = np.array([r.season for r in train])
        by_season = season != self.fold.inner_validation_season
        if by_season.any():
            val = ~by_season
            lag = timedelta(hours=self.lag_hours)
            start = min(r.kickoff_utc for r, v in zip(train, val, strict=True) if v)
            fit = by_season & np.array([r.kickoff_utc + lag <= start for r in train])  # purge: an earlier-season match still unplayed / unpublished at validation start
            return fit, val, f"LAST_TRAINING_SEASON (purged {int(by_season.sum() - fit.sum())} fit rows not yet available at validation start)"
        share = float(self.mcfg.raw["inner_split"]["within_season_fit_share"])
        kicks = sorted(r.kickoff_utc for r in train)
        cut = kicks[min(int(share * len(kicks)), len(kicks) - 1)]
        lag = timedelta(hours=self.lag_hours)
        fit = np.array([r.kickoff_utc + lag <= cut for r in train])
        val = np.array([r.kickoff_utc >= cut for r in train])
        return fit, val, "WITHIN_SEASON_CHRONOLOGICAL_PURGED (only one training season)"

    # -- the fold
    def run(self) -> FoldResult:
        fold, fm, mcfg, bcfg = self.fold, self.fm, self.mcfg, self.bcfg
        train, ev = list(fold.train_rows), list(fold.eval_rows)
        n, line = len(ev), bcfg.over_under_line
        Xtr, Xev = fm.take([r.fixture_id for r in train]), fm.take([r.fixture_id for r in ev])
        ytr = outcomes_for(train, line)
        fit_m, val_m, split_kind = self.inner_masks(train)
        self.diag["inner_split"] = {"kind": split_kind, "fit_rows": int(fit_m.sum()), "validation_rows": int(val_m.sum())}
        if not fit_m.any() or not val_m.any():
            raise SystemExit(f"fold {fold.eval_season} has no room for an inner validation split (fail closed)")
        if max(r.kickoff_utc for r, f in zip(train, fit_m, strict=True) if f) >= min(r.kickoff_utc for r, v in zip(train, val_m, strict=True) if v):
            raise SystemExit("inner fit block is not strictly earlier than the inner validation block (fail closed)")
        lg_tr = np.array([r.league_id for r in train])
        lg_ev = np.array([r.league_id for r in ev])

        # Sprint 3 candidates and references on exactly these folds
        t0 = time.perf_counter()
        s3, _ = sb.run_fold(self.scfg, bcfg, fold)
        self.timings["sprint3_candidates"] = {"fit_s": time.perf_counter() - t0}
        res = {"1x2": dict(s3.probs_1x2), "over_under": dict(s3.probs_over), "btts": dict(s3.probs_btts)}
        self.diag["sprint3_fallbacks"] = s3.diagnostics["fallbacks"]

        cols_all, cols_A = self.cols(), self.cols("league_identity", "league_priors")
        cols_local = self.cols("league_identity")
        d_rounds = int(self.g["rounds_default"])
        defaults = full_params(mcfg, None)
        tuned: dict[str, dict] = {}
        final_rounds: dict[str, int] = {}
        inner_models: dict[str, GBM] = {}
        B: dict[str, np.ndarray] = {}

        for target in TARGETS:
            y = ytr[target]
            # A, B default, XGBoost cross-check: fixed parameters, fixed rounds
            for name, cols, backend, params in (
                ("A_gbm", cols_A, "lightgbm", defaults),
                ("B_gbm_league_default", cols_all, "lightgbm", defaults),
                ("B_xgb_league", cols_all, "xgboost", {k: v for k, v in mcfg.raw["xgb_cross_check"].items() if k != "rounds"}),
            ):
                t0 = time.perf_counter()
                rounds = int(mcfg.raw["xgb_cross_check"]["rounds"]) if backend == "xgboost" else d_rounds
                try:
                    m = self.fit_fixed(target, cols, params, rounds, Xtr, y, backend)
                    put(res, name, target, m.predict_proba(Xev[:, cols]))
                    self.record(name, "GLOBAL", target, m, rounds, len(train))
                    self.diag["fit_status"][f"{name}:{target}"] = {"status": "TRAINED", "rounds": rounds}
                except NotTrainedML as e:
                    if name != "B_xgb_league":
                        raise SystemExit(f"{name} did not train in fold {fold.eval_season} ({target}): {e} (fail closed)") from e
                    put(res, name, target, get(res, "B_gbm_league_default", target))
                    self.diag["fallbacks"][f"{name}:{target}"] = f"B_gbm_league_default ({e})"
                self.timings[f"{name}:{target}"] = {"fit_s": time.perf_counter() - t0}
            # B tuned on the inner validation season only
            t0 = time.perf_counter()
            tn = tune(mcfg, target, Xtr[fit_m][:, cols_all], y[fit_m], Xtr[val_m][:, cols_all], y[val_m], [fm.schema.names[i] for i in cols_all],
                      ["league_code"], self.seed)  # fmt: skip
            self.timings[f"tuning:{target}"] = {"fit_s": time.perf_counter() - t0, "wallclock_seconds": tn.pop("wallclock_seconds_excluded_from_hashes")}
            tuned[target] = full_params(mcfg, tn["best_params"])
            final_rounds[target] = max(10, round(tn["best_iteration"] * len(train) / int(fit_m.sum())))
            tn["final_rounds"] = final_rounds[target]
            self.diag["tuning"][target] = tn
            inner = self.make(target, cols_all, tuned[target]).fit(Xtr[fit_m][:, cols_all], y[fit_m], tn["best_iteration"])
            inner_models[target] = inner
            p_inner = inner.predict_proba(Xtr[val_m][:, cols_all])
            inner_info = {"inner_fit_rows": int(fit_m.sum()), "inner_validation_rows": int(val_m.sum()), "validation_log_loss": log_loss(target, p_inner, y[val_m])}
            mdl = self.fit_fixed(target, cols_all, tuned[target], final_rounds[target], Xtr, y)
            B[target] = mdl.predict_proba(Xev[:, cols_all])
            put(res, "B_gbm_league", target, B[target])
            self.record("B_gbm_league", "GLOBAL", target, mdl, final_rounds[target], len(train), inner_info)
            self.diag["fit_status"][f"B_gbm_league:{target}"] = {"status": "TRAINED", "rounds": final_rounds[target], "gain_by_feature": mdl.gain_by_feature()}
            # control: the league-BLIND features with the same tuner and budget, so "league-aware" is not confused with "tuned"
            t0 = time.perf_counter()
            tna = tune(mcfg, target, Xtr[fit_m][:, cols_A], y[fit_m], Xtr[val_m][:, cols_A], y[val_m], [fm.schema.names[i] for i in cols_A], [], self.seed)
            self.timings[f"tuning_control:{target}"] = {"fit_s": time.perf_counter() - t0, "wallclock_seconds": tna.pop("wallclock_seconds_excluded_from_hashes")}
            pa = full_params(mcfg, tna["best_params"])
            ra = max(10, round(tna["best_iteration"] * len(train) / int(fit_m.sum())))
            tna["final_rounds"] = ra
            self.diag["tuning_control"][target] = tna
            ma = self.fit_fixed(target, cols_A, pa, ra, Xtr, y)
            put(res, "A_gbm_tuned", target, ma.predict_proba(Xev[:, cols_A]))
            self.record("A_gbm_tuned", "GLOBAL", target, ma, ra, len(train), {"validation_log_loss": tna["best_validation_log_loss"]})
            # ablations: same tuned parameters and rounds, no re-tuning (the variant must not get extra search)
            for name, group in ABLATIONS.items():
                cols = self.cols(group)
                am = self.fit_fixed(target, cols, tuned[target], final_rounds[target], Xtr, y)
                put(res, name, target, am.predict_proba(Xev[:, cols]))
                self.record(name, "GLOBAL", target, am, final_rounds[target], len(train))

        # local models and the shrinkage blend
        eval_comps = sorted(set(lg_ev))
        local_p = {t: B[t].copy() for t in TARGETS}
        weights = {t: np.zeros(n) for t in TARGETS}
        for c in eval_comps:
            idx = np.where(lg_ev == c)[0]
            tr_c = lg_tr == c
            info: dict = {"training_matches": int(tr_c.sum())}
            ok, why = local_eligibility(mcfg, int(tr_c.sum()), {t: ytr[t][tr_c] for t in TARGETS})
            info["eligibility"] = why
            if not ok:
                info["status"] = "LOCAL_INELIGIBLE"
                self.diag["fallbacks"][f"C_gbm_local:{c}"] = why
                self.diag["local"][c] = info
                continue
            info["status"] = "TRAINED"
            info["targets"] = {}
            for target in TARGETS:
                y = ytr[target]
                loc = mcfg.raw["local"]
                params = {**tuned[target], "num_leaves": min(int(tuned[target]["num_leaves"]), loc["max_leaves"]),
                          "min_data_in_leaf": max(int(tuned[target]["min_data_in_leaf"]), loc["min_data_in_leaf"])}  # fmt: skip
                fit_c, val_c = tr_c & fit_m, tr_c & val_m
                tinfo: dict = {"inner_fit_rows": int(fit_c.sum()), "inner_validation_rows": int(val_c.sum())}
                rounds = int(loc["fixed_rounds"])
                p_loc_val = None
                try:
                    if fit_c.sum() >= loc["min_inner_fit_matches"] and val_c.sum() >= mcfg.raw["blend"]["min_validation_matches"]:
                        im = self.make(target, cols_local, params).fit(Xtr[fit_c][:, cols_local], y[fit_c], self.mcfg.raw["tuning"]["max_rounds"],
                                                                       valid=(Xtr[val_c][:, cols_local], y[val_c]), early_stopping=self.mcfg.raw["tuning"]["early_stopping_rounds"])  # fmt: skip
                        p_loc_val = im.predict_proba(Xtr[val_c][:, cols_local])
                        rounds = max(10, round(im.best_iteration * tr_c.sum() / fit_c.sum()))
                        tinfo["rounds_source"] = "inner early stopping"
                    else:
                        tinfo["rounds_source"] = "fixed_rounds (inner blocks too small for early stopping)"
                    lm = self.make(target, cols_local, params).fit(Xtr[tr_c][:, cols_local], y[tr_c], rounds)
                    local_p[target][idx] = lm.predict_proba(Xev[idx][:, cols_local])
                    self.record("C_gbm_local", c, target, lm, rounds, int(tr_c.sum()), tinfo)
                except NotTrainedML as e:
                    tinfo["status"] = f"NOT_TRAINED: {e}"
                    self.diag["fallbacks"][f"C_gbm_local:{c}:{target}"] = str(e)
                    info["targets"][target] = tinfo
                    continue
                tinfo["status"] = "TRAINED"
                tinfo["rounds"] = rounds
                if p_loc_val is not None:
                    p_glob_val = inner_models[target].predict_proba(Xtr[val_c][:, cols_all])
                    b = blend_weight(mcfg, target, p_glob_val, p_loc_val, y[val_c])
                else:
                    b = {"weight": 0.0, "reason": "no inner local validation predictions: weight 0"}
                tinfo["blend"] = b
                weights[target][idx] = float(b["weight"])
                info["targets"][target] = tinfo
            self.diag["local"][c] = info
        for target in TARGETS:
            put(res, "C_gbm_local", target, local_p[target])
            put(res, "D_gbm_blend", target, mix(B[target], local_p[target], weights[target]))
        self.diag["blend"] = {t: {c: float(weights[t][np.where(lg_ev == c)[0][0]]) for c in eval_comps} for t in TARGETS}

        # S3 finding 4: the team ridge of the Dixon-Coles reference tuned on the inner validation season only
        t0 = time.perf_counter()
        grid = mcfg.raw["ridge_tuning"]["l2_team_grid"]
        fit_rows = [r for r, f in zip(train, fit_m, strict=True) if f]
        val_rows = [r for r, v in zip(train, val_m, strict=True) if v]
        yv = np.array([r.outcome for r in val_rows])
        scores = {}
        for l2 in grid:
            mod, d = sb._fit_dc(self.scfg.dc_spec(l2_team=float(l2)), f"A_ridge_{l2}", fit_rows, None)
            scores[str(l2)] = None if mod is None else round(sb._logloss_1x2(mod.predict(val_rows).one_x_two(), yv), 6)
        okay = {k: v for k, v in scores.items() if v is not None}
        if not okay:
            raise SystemExit(f"no ridge value trained in fold {fold.eval_season} (fail closed)")
        best_l2 = float(min(okay, key=lambda k: (okay[k], float(k))))
        mod, d = sb._fit_dc(self.scfg.dc_spec(l2_team=best_l2), "A_dc_ridge_tuned", train, None)
        if mod is None:
            raise SystemExit(f"A_dc_ridge_tuned did not train in fold {fold.eval_season}: {d} (fail closed)")
        pred = mod.predict(ev)
        p1 = np.clip(pred.one_x_two(), 0, 1)
        res["1x2"]["A_dc_ridge_tuned"] = p1 / p1.sum(axis=1, keepdims=True)
        res["over_under"]["A_dc_ridge_tuned_totals"] = np.clip(pred.over(line), 0, 1)
        res["btts"]["A_dc_ridge_tuned_goals"] = np.clip(pred.btts(), 0, 1)
        self.diag["ridge_tuning"] = {"validation_log_loss_by_l2_team": scores, "chosen_l2_team": best_l2, "default_l2_team": self.scfg.raw["dc"]["l2_team"]}
        self.timings["A_dc_ridge_tuned"] = {"fit_s": time.perf_counter() - t0}

        # permutation importance of the tuned 1X2 model on the inner validation season (train / validation data only)
        self.diag["permutation_importance"] = self.permutation_importance(inner_models["1x2"], Xtr[val_m], ytr["1x2"][val_m], cols_all)
        self.diag["bridge_note"] = "the cross-league bridge enters the GBMs through elo_diff (UEFA-learned country offsets, S3 TimeAwareElo)"
        return FoldResult(fold, res["1x2"], res["over_under"], res["btts"], self.diag, self.timings)

    def permutation_importance(self, model: GBM, Xval, yval, cols: list[int]) -> dict:
        rng = np.random.default_rng(int(self.mcfg.raw["ablation"]["seed"]) + int(self.fold.eval_season[:4]))
        reps = int(self.mcfg.raw["ablation"]["permutation_repeats"])
        base = log_loss("1x2", model.predict_proba(Xval[:, cols]), yval)
        out = {}
        for group in sorted(set(self.fm.schema.groups.values())):
            gcols = [c for c in self.fm.schema.columns_of(group) if c in cols]
            deltas = []
            for _ in range(reps):
                Xp = Xval.copy()
                perm = rng.permutation(len(Xp))
                Xp[:, gcols] = Xval[perm][:, gcols]
                deltas.append(log_loss("1x2", model.predict_proba(Xp[:, cols]), yval) - base)
            out[group] = {"mean_log_loss_increase": float(np.mean(deltas)), "features": len(gcols)}
        return {"basis": "inner-validation season of this fold (train-side data only); an association with predictive skill, not a cause",
                "baseline_validation_log_loss": base, "repeats": reps, "by_group": out}


# ------------------------------------------------------------------------------------------------ analysis
HYPOTHESES = [
    # id, competition, target, variant (B), base (ablated or A), text. SUPPORTED if the variant is better with the whole 95% interval < 0.
    ("H1_premier_league_rest_fixture_congestion", "ENG1", "1x2", "B_gbm_league", "B_no_rest", "Premier League: rest and fixture-congestion features improve 1X2"),
    ("H2_serie_a_league_priors_draws", "ITA1", "1x2", "B_gbm_league", "B_no_league_priors", "Serie A: league-season priors (draw / low-scoring rates) improve 1X2"),
    ("H3_la_liga_elo_disparity", "ESP1", "1x2", "B_gbm_league", "B_no_elo", "La Liga: the Elo strength gap improves 1X2"),
    ("H4_bundesliga_goal_density", "GER1", "over_under", "B_gbm_league", "B_no_league_priors", "Bundesliga: league goal-density priors improve over/under 2.5"),
    ("H5_ligue_1_league_identity", "FRA1", "1x2", "B_gbm_league", "A_gbm_tuned", "Ligue 1: league identity and priors (B) improve 1X2 against the equally tuned league-blind GBM"),
    ("H6_super_lig_league_level_home_advantage", "TUR1", "1x2", "B_gbm_league", "B_no_league_priors", "Süper Lig: league-level home advantage / priors improve 1X2"),
    ("H6b_super_lig_venue_form", "TUR1", "1x2", "B_gbm_league", "B_no_home_away", "Süper Lig: home / away venue form improves 1X2"),
    ("H8_champions_league_cross_league", "UEFA_UCL", "1x2", "B_gbm_league", "A_gbm_tuned", "Champions League: league identity plus the bridge-based Elo (B) improve 1X2 against the equally tuned league-blind GBM"),
    ("H9_europa_league_cross_league", "UEFA_UEL", "1x2", "B_gbm_league", "A_gbm_tuned", "Europa League: league identity plus the bridge-based Elo (B) improve 1X2 against the equally tuned league-blind GBM"),
    ("H10_conference_league_cross_league", "UEFA_UECL", "1x2", "B_gbm_league", "A_gbm_tuned", "Conference League: league identity plus the bridge-based Elo (B) improve 1X2 against the equally tuned league-blind GBM"),
]


def _model_key(target: str, name: str) -> str:
    return name if target == "1x2" else name + SUFFIX[target]


def _loss_rows(ev: dict, target: str, name: str, sel: np.ndarray) -> np.ndarray:
    p, y = ev["probs"][target][_model_key(target, name)][sel], ev["y"][target][sel]
    return bm.rows_log_loss_1x2(p, y) if target == "1x2" else bm.binary_log_loss_rows(p, y)


def hypotheses_report(ev: dict, bcfg) -> dict:
    rows: list[EvalRow] = ev["rows"]
    leagues = np.array([r.league_id for r in rows])
    blocks = np.array([bm.iso_week_block(r.kickoff_utc) for r in rows])
    out = []
    for hid, comp, target, variant, base, text in HYPOTHESES:
        sel = leagues == comp
        if not sel.any():
            out.append({"id": hid, "competition": comp, "target": target, "hypothesis": text, "verdict": "NO_EVAL_ROWS"})
            continue
        pd_ = bm.paired_block_bootstrap(_loss_rows(ev, target, variant, sel), _loss_rows(ev, target, base, sel), blocks[sel], bcfg.bootstrap_samples,
                                        bcfg.bootstrap_seed, bcfg.bootstrap_alpha)  # fmt: skip
        verdict = "SUPPORTED" if pd_.upper < 0 else ("NOT_SUPPORTED" if pd_.lower > 0 else "INCONCLUSIVE")
        out.append({"id": hid, "competition": comp, "target": target, "hypothesis": text, "variant": variant, "base": base,
                    "delta_log_loss_variant_minus_base": round(pd_.delta, 6), "ci95": [round(pd_.lower, 6), round(pd_.upper, 6)],
                    "n": pd_.n, "blocks": pd_.n_blocks, "verdict": verdict,
                    "rule": "SUPPORTED: the whole 95% block interval is below 0 on dev out-of-sample rows; NOT_SUPPORTED: above 0; otherwise INCONCLUSIVE"})  # fmt: skip
    out.append({"id": "H7_super_lig_squad_continuity", "competition": "TUR1", "hypothesis": "Süper Lig: squad continuity matters", "verdict": "NOT_TESTABLE",
                "reason": "the licensed universe has no squad, transfer or lineup data"})  # fmt: skip
    out.append({"id": "H11_market_odds", "competition": "ALL", "hypothesis": "beating a bookmaker baseline", "verdict": "NOT_TESTABLE",
                "reason": "no licensed timestamped odds exist in this universe: no odds-aware model and no market baseline were built"})  # fmt: skip
    return {"definition": "league hypotheses tested by feature-group ablation on dev out-of-sample rows; a feature that does not help stays in the "
                          "report as NOT_SUPPORTED / INCONCLUSIVE and no importance is read as causation", "hypotheses": out}  # fmt: skip


def error_analysis(ev: dict, results: list[FoldResult], feats: FeatureMatrix, bcfg) -> dict:
    """Per league x season x target diagnostics of the final GBM candidates (and the best Sprint 3 candidate)."""
    rows: list[EvalRow] = ev["rows"]
    leagues = np.array([r.league_id for r in rows])
    seasons = np.array([r.season for r in rows])
    gh = np.array([r.home_goals for r in rows])
    ga = np.array([r.away_goals for r in rows])
    y1 = ev["y"]["1x2"]
    pos = {f: i for i, f in enumerate(feats.fixture_ids)}
    new_team = np.array([feats.X[pos[r.fixture_id], feats.schema.index("either_new_team")] == 1.0 for r in rows])
    ord_in_season = np.zeros(len(rows))
    for lg in set(leagues):
        for s in set(seasons):
            sel = np.where((leagues == lg) & (seasons == s))[0]
            if len(sel):
                ks = np.array([rows[i].kickoff_utc.timestamp() for i in sel])
                ord_in_season[sel] = np.argsort(np.argsort(ks)) / max(len(sel) - 1, 1)
    models = ("A_gbm", "A_gbm_tuned", "B_gbm_league", "D_gbm_blend", "E_elo_bridge")
    seg = {"early_season_first_third": ord_in_season <= 1 / 3, "late_season_last_third": ord_in_season >= 2 / 3, "either_club_new_or_promoted_like": new_team,
           "scoreline_0_0": (gh == 0) & (ga == 0), "scoreline_1_1": (gh == 1) & (ga == 1), "scoreline_4_plus_goals": (gh + ga) >= 4,
           "one_goal_margin": np.abs(gh - ga) == 1}  # fmt: skip
    out: dict = {"models": list(models), "groups": {}, "segments": {}, "confidence_bins": {}, "binary_targets": {},
                 "missing_inputs": "xG, odds and lineups do not exist in the licensed universe: no segment by missing xG / odds is possible (reported, not imputed)",
                 "uefa_stages": "NOT_AVAILABLE: the source does not mark group / knockout stages in the scores used here; analysed per competition only"}  # fmt: skip

    def one(sel: np.ndarray) -> dict:
        d: dict = {"n": int(sel.sum()), "label_rates_home_draw_away": [round(float((y1[sel] == k).mean()), 4) for k in range(3)]}
        for m in models:
            p = ev["probs"]["1x2"][m][sel]
            y = y1[sel]
            pred = p.argmax(axis=1)
            conf = np.zeros((3, 3), int)
            for a, b in zip(y, pred, strict=True):
                conf[a, b] += 1
            fav_home = p[:, 0] >= p[:, 2]
            fav_p = np.where(fav_home, p[:, 0], p[:, 2])
            fav_won = np.where(fav_home, y == 0, y == 2)
            d[m] = {"log_loss": round(float(bm.rows_log_loss_1x2(p, y).mean()), 5), "accuracy": round(float((pred == y).mean()), 4),
                    "confusion_rows_actual_cols_predicted_HDA": conf.tolist(),
                    "favorite_bias_mean_p_minus_win_rate": round(float(fav_p.mean() - fav_won.mean()), 4),
                    "away_p_minus_observed_away_rate": round(float(p[:, 2].mean() - (y == 2).mean()), 4),
                    "draw_p_minus_observed_draw_rate": round(float(p[:, 1].mean() - (y == 1).mean()), 4)}  # fmt: skip
        return d

    for lg in sorted(set(leagues)):
        for s in sorted(set(seasons)):
            sel = (leagues == lg) & (seasons == s)
            if sel.sum() >= 30:
                out["groups"][f"{lg}|{s}"] = one(sel)
    for name, mask in seg.items():
        out["segments"][name] = one(mask) if mask.sum() >= 30 else {"n": int(mask.sum()), "status": "TOO_FEW_ROWS"}
    for m in ("B_gbm_league", "D_gbm_blend"):
        p = ev["probs"]["1x2"][m]
        conf_max = p.max(axis=1)
        bins = [0.0, 0.40, 0.50, 0.60, 0.70, 1.01]
        tab = []
        for lo, hi in zip(bins[:-1], bins[1:], strict=True):
            sel = (conf_max >= lo) & (conf_max < hi)
            if sel.any():
                tab.append({"confidence": f"[{lo:.2f},{min(hi, 1.0):.2f})", "n": int(sel.sum()), "mean_confidence": round(float(conf_max[sel].mean()), 4),
                            "accuracy": round(float((p[sel].argmax(axis=1) == y1[sel]).mean()), 4)})  # fmt: skip
        out["confidence_bins"][m] = tab
    for target, naive in (("over_under", ("always_over", "always_under", "train_over_rate")), ("btts", ("always_yes", "always_no", "train_btts_rate"))):
        y = ev["y"][target]
        tab = {"label_rate": round(float(y.mean()), 4), "majority_class_accuracy": round(float(max(y.mean(), 1 - y.mean())), 4)}
        for m in (*models[:4], "A_dc_ridge_tuned", *naive):
            key = m if m in naive else _model_key(target, m)
            if key in ev["probs"][target]:
                p = ev["probs"][target][key]
                tab[m] = {"log_loss": round(float(bm.binary_log_loss_rows(np.clip(p, 1e-6, 1 - 1e-6), y).mean()), 5), "accuracy": round(float(((p >= 0.5) == (y == 1)).mean()), 4)}
        out["binary_targets"][target] = tab
    return out


def decisions_report(mcfg: MLConfig, folds: list[Fold], metrics: dict, deltas: dict, deltas_ctrl: dict, deltas_s3: dict, results: list[FoldResult],
                     ev: dict, bcfg) -> dict:
    """Per competition and target: does any league-aware candidate beat the frozen global GBM `A_gbm` AND its equally tuned league-blind control
    `A_gbm_tuned` (and, for over/under and BTTS, the train-rate baseline) on dev out-of-sample rows? 1X2 is the primary target and sets `decision`.
    The verdict is DEV-only evidence: calibration, the frozen holdout and approval are Sprint 5."""
    loc, dec = mcfg.raw["local"], mcfg.raw["decision"]
    last = folds[-1]
    comps = sorted({r.league_id for f in folds for r in f.eval_rows})
    cands = ("B_gbm_league_default", "B_gbm_league", "C_gbm_local", "D_gbm_blend", "A_dc_ridge_tuned")  # B_xgb_league is a cross-check only (OS-dependent)
    naive = {"over_under": "train_over_rate", "btts": "train_btts_rate"}
    leagues = np.array([r.league_id for r in ev["rows"]])
    blocks = np.array([bm.iso_week_block(r.kickoff_utc) for r in ev["rows"]])
    ineligible_folds: dict[str, int] = {}
    blended_folds: dict[tuple[str, str], int] = {}
    for r in results:
        for c, info in r.diagnostics["local"].items():
            if info["status"] == "LOCAL_INELIGIBLE":
                ineligible_folds[c] = ineligible_folds.get(c, 0) + 1
        for t in TARGETS:
            for c, w in r.diagnostics["blend"][t].items():
                if w > 0:
                    blended_folds[(c, t)] = blended_folds.get((c, t), 0) + 1
    out = {}
    for c in comps:
        n_train = sum(1 for r in last.train_rows if r.league_id == c)
        tier = ("LOCAL_OFF_UNDER_500" if n_train < loc["min_train_matches"] else "LOCAL_EVIDENCE_500_TO_1500_PROOF_REQUIRED" if n_train < loc["proof_required_below"] else "LOCAL_CANDIDATES_OVER_1500")  # fmt: skip
        g = f"league={c}"
        sel = leagues == c
        by_target = {}
        for target in TARGETS:
            ref = _model_key(target, "A_gbm")
            ref_acc, ref_ece = metrics[target][g][ref]["accuracy"], metrics[target][g][ref]["ece_raw"]
            table, eligible = {}, []
            for cand in cands:
                key = _model_key(target, cand)
                d = deltas[target].get(g, {}).get(key)
                if d is None:
                    continue
                ll, br, m = d["log_loss"], d["brier"], metrics[target][g][key]
                reasons = []
                if cand in ("C_gbm_local", "D_gbm_blend") and tier == "LOCAL_OFF_UNDER_500":
                    reasons.append("local models are production-ineligible under the minimum training matches")
                if cand == "C_gbm_local" and ineligible_folds.get(c, 0):
                    reasons.append("at least one fold fell back to the global model (LOCAL_INELIGIBLE)")
                if cand == "D_gbm_blend" and not blended_folds.get((c, target), 0):
                    reasons.append("the blend never put weight on the local model: identical to B_gbm_league")
                if not ll["upper"] < 0:
                    reasons.append("log-loss paired interval against A_gbm is not entirely below 0")
                if not br["delta_vs_reference"] < 0:
                    reasons.append("Brier is not better")
                if m["ece_raw"] - ref_ece > dec["max_ece_worsening"]:
                    reasons.append("raw ECE is worse than the reference by more than the allowed margin")
                if m["accuracy"] > ref_acc and ll["delta_vs_reference"] > 0:
                    reasons.append("accuracy rose while log loss worsened: not an argument for promotion")
                ctrl = deltas_ctrl[target].get(g, {}).get(key)
                if ctrl is not None and not ctrl["log_loss"]["upper"] < 0:
                    reasons.append("not significantly better than the equally tuned league-blind control A_gbm_tuned (the gain over A_gbm is tuning, not league awareness)")
                entry = {"delta_log_loss_vs_A_gbm": ll["delta_vs_reference"], "ci95": [ll["lower"], ll["upper"]], "delta_brier_vs_A_gbm": br["delta_vs_reference"],
                         "accuracy": m["accuracy"], "ece_raw": m["ece_raw"],
                         "delta_log_loss_vs_control_A_gbm_tuned": None if ctrl is None else ctrl["log_loss"]["delta_vs_reference"],
                         "ci95_vs_control": None if ctrl is None else [ctrl["log_loss"]["lower"], ctrl["log_loss"]["upper"]]}  # fmt: skip
                if target == "1x2":
                    s2d = deltas_s3["vs_elo_s2"]["1x2"].get(g, {}).get(key)
                    if s2d is not None and s2d["log_loss"]["lower"] > 0:
                        reasons.append("significantly worse than the existing global Elo (elo_s2)")
                    s3d = deltas_s3["vs_E_elo_bridge"]["1x2"].get(g, {}).get(key)
                    entry |= {"delta_log_loss_vs_elo_s2": None if s2d is None else s2d["log_loss"]["delta_vs_reference"],
                              "ci95_vs_elo_s2": None if s2d is None else [s2d["log_loss"]["lower"], s2d["log_loss"]["upper"]],
                              "delta_log_loss_vs_s3_best_E_elo_bridge": None if s3d is None else s3d["log_loss"]["delta_vs_reference"],
                              "ci95_vs_s3_best": None if s3d is None else [s3d["log_loss"]["lower"], s3d["log_loss"]["upper"]]}  # fmt: skip
                else:
                    pd_ = bm.paired_block_bootstrap(_loss_rows(ev, target, cand, sel), bm.binary_log_loss_rows(ev["probs"][target][naive[target]][sel], ev["y"][target][sel]),
                                                    blocks[sel], bcfg.bootstrap_samples, bcfg.bootstrap_seed, bcfg.bootstrap_alpha)  # fmt: skip
                    entry |= {"delta_log_loss_vs_train_rate_baseline": round(pd_.delta, 6), "ci95_vs_train_rate_baseline": [round(pd_.lower, 6), round(pd_.upper, 6)]}
                    if not pd_.upper < 0:
                        reasons.append("not significantly better than the train-rate baseline (always predicting the base rate)")
                table[cand] = {**entry, "blocked_by": reasons}
                if not reasons:
                    eligible.append((ll["delta_vs_reference"], cand))
            best = min(eligible)[1] if eligible else None
            by_target[target] = {"decision": f"CANDIDATE_{best}" if best else "GLOBAL_A_gbm", "candidates_vs_A_gbm": table,
                                 "rationale": ("a candidate beats A_gbm, its tuned league-blind control" + (" and the train-rate baseline" if target != "1x2" else "")
                                               + " on the whole 95% block interval with Brier, ECE and accuracy not contradicting it") if best else
                                              "no league-aware candidate passed every rule; the global model is kept"}  # fmt: skip
        out[c] = {"training_matches_last_fold": n_train, "volume_tier": tier, "folds_with_local_ineligible_fallback": ineligible_folds.get(c, 0),
                  "decision": by_target["1x2"]["decision"], "decision_primary_target": "1x2",
                  "decision_scope": "dev out-of-sample evidence only; calibration, the frozen holdout and approval are Sprint 5", "by_target": by_target}  # fmt: skip
    n_tests = sum(len(x["candidates_vs_A_gbm"]) for v in out.values() for x in v["by_target"].values())
    n_pass = sum(1 for v in out.values() for x in v["by_target"].values() if x["decision"] != "GLOBAL_A_gbm")
    multiplicity = {"comparisons_evaluated": n_tests, "competition_target_cells_with_a_candidate": n_pass,
                    "warning": "each candidate is tested against several references in many competition x target cells with a one-sided 2.5% interval rule; "
                               "a few isolated passes are expected by chance alone. Sprint 5 must confirm any of them on the frozen holdout before it counts."}  # fmt: skip
    return {"reference": "A_gbm (frozen global GBM, score-only re-implementation; the production GBM cannot run on this universe)", "multiple_comparisons": multiplicity,
            "rule": "eligible only with: paired log-loss interval entirely < 0 against A_gbm AND against the tuned league-blind control A_gbm_tuned (and, for over/under and BTTS, "
                    "the train-rate baseline), Brier better, raw ECE not worse by more than the margin, accuracy not rising while log loss worsens, a tier allowing local models, "
                    "a non-zero local weight for the blend, and (1X2) not significantly worse than the existing Elo", "competitions": out}  # fmt: skip


def model_cards(mcfg: MLConfig, results: list[FoldResult], decisions: dict, schema) -> str:
    g, loc, bl, tn = mcfg.raw["gbm"], mcfg.raw["local"], mcfg.raw["blend"], mcfg.raw["tuning"]
    lines = ["# Sprint 4 model cards (generated by `python -m src.league_models.ml_benchmark`)", "",
             "All models: gradient-boosted trees on the score-only feature contract (`s4_feature_schema.json`, hash "
             f"`{schema.sha256[:16]}`), odds-free, fitted per outer fold on matches available at the fold cutoff, parameters frozen for the eval season, "
             "`BACKTEST_LIMITED`, DEV-only evidence, raw (uncalibrated) probabilities. None is in production.", "",
             "**Odds-aware model card: NOT_AVAILABLE.** The licensed universe has no timestamped odds, so no odds-aware model and no market baseline were built and no "
             "claim about beating the market is made. **xG, lineups, injuries, shots: not in the universe, never imputed.**", ""]  # fmt: skip
    cards = [
        ("A_gbm", "Frozen global GBM baseline: league-blind features (no league identity, no league priors), fixed parameters, fixed rounds.",
         f"LightGBM, {g['rounds_default']} rounds, defaults {json.dumps(g['defaults'], sort_keys=True)}; the production XGBoost/LightGBM cannot run here, so this is a score-only re-implementation."),
        ("A_gbm_tuned", "Control: the league-blind features of A_gbm, tuned like B.", "Same tuner, grid, trials and rounds rule as B_gbm_league; separates the effect of league awareness from the effect of tuning."),
        ("B_gbm_league_default", "Global GBM with league identity (categorical) and league-season priors, fixed parameters.", "Same as A_gbm plus league_identity and league_priors: isolates the league effect."),
        ("B_gbm_league", "Same features as B default; parameters tuned by Optuna on the inner-validation season only.",
         f"TPE, {tn['max_trials']} trials per fold and target on a coarse grid, early stopping {tn['early_stopping_rounds']}; final rounds = best iteration scaled by training / inner-fit size."),
        ("B_xgb_league", "XGBoost with the B features and fixed parameters (backend cross-check, not tuned).", json.dumps(mcfg.raw["xgb_cross_check"], sort_keys=True)),
        ("C_gbm_local", "One GBM per competition on its own history.",
         f"LOCAL_INELIGIBLE under {loc['min_train_matches']} matches or with a class under {loc['min_minority_class']} examples (explicit fallback to B); capacity capped at {loc['max_leaves']} leaves; "
         f"matches between {loc['min_train_matches']} and {loc['proof_required_below']} are evidence only, out-of-sample proof required."),
        ("D_gbm_blend", "(1 - w) * B + w * C per competition and target (probability level, simplex preserved).",
         f"w from inner-validation predictions, grid {bl['weight_grid_step']}, must beat B by {bl['min_improvement']} log loss, shrunk by n/(n+{bl['shrink_k']}); 0 otherwise."),
        ("A_dc_ridge_tuned", "Sprint 3 global Dixon-Coles with the team ridge tuned on the inner-validation season.", f"grid {mcfg.raw['ridge_tuning']['l2_team_grid']}."),
    ]
    for name, what, params in cards:
        statuses = [f"{r.fold.eval_season}: {'; '.join(sorted({v['status'] for k, v in r.diagnostics['fit_status'].items() if k.startswith(name + ':')})) or 'see run records'}" for r in results]
        lines += [f"## {name}", what, "", f"- Parameters: {params}", f"- Per-fold status: {' | '.join(statuses)}",
                  "- Known limits: score-only features; UEFA finals carry a home advantage; clubs new to the history get NaN form and are flagged; permutation importance is "
                  "an association on the inner-validation season, never a cause.", ""]  # fmt: skip
    lines += ["## Decisions (dev evidence only)", "", "| Competition | Training matches (last fold) | Tier | 1X2 | Over/under 2.5 | BTTS |", "|---|---|---|---|---|---|"]
    for c, v in decisions["competitions"].items():
        lines.append(f"| `{c}` | {v['training_matches_last_fold']} | {v['volume_tier']} | {v['by_target']['1x2']['decision']} | {v['by_target']['over_under']['decision']} | {v['by_target']['btts']['decision']} |")
    return "\n".join(lines) + "\n"


def tuning_report(results: list[FoldResult]) -> dict:
    return {"objective": "validation log loss on the inner block (last training season of the fold); the eval season is never shown to the tuner",
            "folds": {r.fold.eval_season: {"B_gbm_league": r.diagnostics["tuning"], "A_gbm_tuned_control": r.diagnostics["tuning_control"]} for r in results}}


def importance_report(results: list[FoldResult]) -> dict:
    out = {"definition": "permutation importance by feature group on the inner-validation season (train-side), and gain importance of the final B model by group; associations, not causes",
           "folds": {}}  # fmt: skip
    for r in results:
        gains = r.diagnostics["fit_status"]["B_gbm_league:1x2"]["gain_by_feature"]
        total = sum(gains.values()) or 1.0
        out["folds"][r.fold.eval_season] = {"permutation": r.diagnostics["permutation_importance"],
                                            "gain_share_by_feature_1x2": {k: round(v / total, 5) for k, v in sorted(gains.items(), key=lambda kv: -kv[1])}}  # fmt: skip
    return out


def prediction_lines(results: list[FoldResult], snapshot_id: str, line: float) -> list[str]:
    lines = []
    for r in results:
        y = outcomes_for(list(r.fold.eval_rows), line)
        for i, row in enumerate(r.fold.eval_rows):
            base = {"fixture_id": row.fixture_id, "league": row.league_id, "season": row.season, "fold_eval_season": r.fold.eval_season, "snapshot_id": snapshot_id,
                    "track": "historical_backtest", "as_of_quality": "BACKTEST_LIMITED", "y_1x2": int(y["1x2"][i]), "y_over": int(y["over_under"][i]),
                    "y_btts": int(y["btts"][i])}  # fmt: skip
            for model, p in r.probs_1x2.items():
                rec = {**base, "model": model, "p_1x2": [round(float(v), 8) for v in p[i]]}
                if model + "_totals" in r.probs_over:
                    rec["p_over"] = round(float(r.probs_over[model + "_totals"][i]), 8)
                if model + "_goals" in r.probs_btts:
                    rec["p_btts"] = round(float(r.probs_btts[model + "_goals"][i]), 8)
                lines.append(json.dumps(rec, sort_keys=True))
    return lines


def strip_artifact_hashes(runs: list[dict]) -> list[dict]:
    """The committed copy keeps everything except the model-file hash (LightGBM text can differ in the last digits between operating
    systems) and the git sha (it names the checkout that produced the run, which differs in CI); the run output keeps both and
    `--verify` re-hashes the model files."""
    return [{k: v for k, v in r.items() if k not in ("artifact_sha256", "git_sha")} for r in runs]


# ------------------------------------------------------------------------------------------------ run
def run(
    root: Path,
    as_of: datetime,
    output_dir: Path | None,
    *,
    dry_run: bool = False,
    eval_start: str | None = None,
    eval_end: str | None = None,
    ml_config_path: Path | None = None,
    bench_config_path: Path | None = None,
    stat_config_path: Path | None = None,
    docs_dir: Path | None = None,
    audit: AuditResult | None = None,
    skip_perturbation: bool = False,
) -> dict:
    """`audit` lets a caller reuse a collected Sprint 1 audit (tests pass a synthetic one). The licence gate runs first."""
    bcfg = load_bench_config(bench_config_path) if bench_config_path else load_bench_config()
    scfg = sb.load_stat_config(stat_config_path) if stat_config_path else sb.load_stat_config()
    mcfg = load_ml_config(ml_config_path) if ml_config_path else load_ml_config()
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
    parity = {"status": "NOT_VERIFIED: committed Sprint 2 protocol manifest not found"}
    for tag in ("s2", "s3"):
        path = root / "docs" / "league_models" / f"{tag}_protocol_manifest.json"
        if path.is_file():
            prev = {f["eval_season"]: f for f in json.loads(path.read_text(encoding="utf-8"))["folds"]}
            same = all(prev.get(f["eval_season"]) == f for f in proto["folds"])
            if not same:
                raise SystemExit(f"the folds differ from the committed Sprint {tag[1]} folds: Sprint 4 must use the same folds (fail closed)")
            parity = {"status": "IDENTICAL_TO_SPRINT_2_AND_3", "compared_folds": [f["eval_season"] for f in proto["folds"]]}
    proto["sprint2_3_fold_parity"] = parity
    proto["ml_config_fingerprint_sha256"] = mcfg.fingerprint
    proto["stat_config_fingerprint_sha256"] = scfg.fingerprint
    stage = bcfg.default_stage
    leaks = {f.eval_season: len(history_leak_check([*f.train_rows, *f.eval_rows], league_cfg.result_lag_hours, stage)) for f in folds}
    snapshot_digest = hashlib.sha256("".join(f.sha256 for f in audit.source_files).encode()).hexdigest()
    snapshot_id = f"snap-{snapshot_digest[:12]}"
    fcfg = FeatureConfig.from_raw(mcfg.raw, mcfg.raw["league_vocabulary"], league_cfg.result_lag_hours, stage, scfg.elo_spec())
    plan = {"dry_run": dry_run, "snapshot_id": snapshot_id, "stage": stage.value, "folds": proto["folds"], "universe_counts": proto["universe_counts"],
            "ml_config_fingerprint_sha256": mcfg.fingerprint, "sprint2_3_fold_parity": parity, "replay_violations_per_fold": leaks}  # fmt: skip
    if dry_run:
        return {"plan": plan}
    if any(leaks.values()):
        raise SystemExit(f"replay violations found: {leaks} (fail closed)")

    # one as-of feature pass over the development rows: a row's features depend only on matches available at its own cutoff
    dev_rows = list(universe.dev)
    fm = build_features(dev_rows, fcfg)
    points = [f.fit_cutoff_utc for f in folds[1::2]] or [folds[0].fit_cutoff_utc]
    perturbation = ({"status": "SKIPPED"} if skip_perturbation else perturbation_audit(dev_rows, fcfg, points))
    if perturbation.get("violations"):
        raise SystemExit(f"feature as-of violations found: {perturbation} (fail closed)")
    sha, dirty = git_sha(root)
    runs = [FoldRun(mcfg, scfg, bcfg, f, fm, snapshot_id, sha, league_cfg.result_lag_hours) for f in folds]
    results = [fr.run() for fr in runs]
    ref_cfg = dataclasses.replace(bcfg, reference_model=mcfg.raw["reference_model"])
    ev = collect_eval(results, ref_cfg)
    metrics, deltas, reliab = score_all(ev, ref_cfg, TARGETS)
    _, d_elo, _ = score_all(ev, dataclasses.replace(bcfg, reference_model=mcfg.raw["s3_best_reference"]), ("1x2",))
    _, d_s3_goal, _ = score_all(ev, dataclasses.replace(bcfg, reference_model="A_dc"), ("over_under", "btts"))
    _, d_elo_s2, _ = score_all(ev, dataclasses.replace(bcfg, reference_model="elo_s2"), ("1x2",))
    deltas_s3 = {"vs_E_elo_bridge": d_elo, "vs_A_dc_totals_and_goals": d_s3_goal, "vs_elo_s2": d_elo_s2}
    _, deltas_ctrl, _ = score_all(ev, dataclasses.replace(bcfg, reference_model="A_gbm_tuned"), TARGETS)
    decisions = decisions_report(mcfg, folds, metrics, deltas, deltas_ctrl, deltas_s3, results, ev, bcfg)
    hyp = hypotheses_report(ev, ref_cfg)
    errors = error_analysis(ev, results, fm, bcfg)
    all_runs = [r for fr in runs for r in fr.runs]
    artifacts = {k: v for fr in runs for k, v in fr.artifacts.items()}
    cards = model_cards(mcfg, results, decisions, fm.schema)
    leakage = {"stage": stage.value, "replay_violations_per_fold": leaks, "feature_perturbation_audit": perturbation,
               "feature_schema_sha256": fm.schema.sha256, "unseen_league_rows_in_universe": fm.unseen_league_rows,
               "train_serve_parity": "one code path builds training and evaluation rows (build_features); the schema hash is identical for every fold and model",
               "tuning_isolation": "the tuner receives only the inner-fit and inner-validation blocks of the training rows; no eval-season row, label or prediction reaches it",
               "holdout": "never read (the universe carries holdout / reserved / guard fixtures by id only)"}  # fmt: skip
    evidence = {
        "for_sprint_5": "frozen inputs and per-competition evidence; nothing here is an approval",
        "oof_predictions": {"file": "fold_predictions.jsonl", "note": "per-fixture out-of-sample probabilities of every model on the Sprint 2 folds; frozen by the run manifest hash"},
        "feature_schema_sha256": fm.schema.sha256, "snapshot_id": snapshot_id, "folds": [f.eval_season for f in folds],
        "competitions": {c: {"decision": v["decision"], "volume_tier": v["volume_tier"],
                            "by_target": {t: {"decision": x["decision"], "blocking_reasons": {k: y["blocked_by"] for k, y in x["candidates_vs_A_gbm"].items()}} for t, x in v["by_target"].items()}}
                         for c, v in decisions["competitions"].items()},
        "still_required_in_sprint_5": ["calibration fitted on a separate chronological split", "one-shot evaluation of the frozen holdout",
                                       "block-bootstrap intervals on that holdout", "approval gate; leagues without proven improvement stay on the global model"]}  # fmt: skip
    files: dict[str, str] = {
        "fold_predictions.jsonl": "\n".join(prediction_lines(results, snapshot_id, bcfg.over_under_line)) + "\n",
        "ml_metrics.json": dumps({"protocol_version": mcfg.raw["protocol_version"], "snapshot_id": snapshot_id, "primary_metric": "log_loss",
                                  "reference_model": mcfg.raw["reference_model"], "no_single_winner": True, "metrics": metrics,
                                  "ridge_tuning": {r.fold.eval_season: r.diagnostics["ridge_tuning"] for r in results},
                                  "blend_weights": {r.fold.eval_season: r.diagnostics["blend"] for r in results},
                                  "local_models": {r.fold.eval_season: r.diagnostics["local"] for r in results},
                                  "fallbacks": {r.fold.eval_season: r.diagnostics["fallbacks"] for r in results}}),  # fmt: skip
        "paired_deltas.json": dumps({"definition": "delta = model - reference on the same fixtures; negative = model better", "reference": mcfg.raw["reference_model"], "deltas": deltas}),
        "paired_vs_s3.json": dumps(deltas_s3),
        "paired_vs_control.json": dumps({"definition": "delta = model - A_gbm_tuned (the league-blind GBM tuned with the same tuner and budget); negative = model better", "deltas": deltas_ctrl}),
        "reliability.json": dumps({"bins": bcfg.calibration_bins, "note": "raw probabilities, nothing calibrated in Sprint 4", "curves": reliab}),
        "error_analysis.json": dumps(errors),
        "hypotheses.json": dumps(hyp),
        "feature_importance.json": dumps(importance_report(results)),
        "tuning_trials.json": dumps(tuning_report(results)),
        "league_decisions.json": dumps(decisions),
        "training_runs.json": dumps({"runs": strip_artifact_hashes(all_runs), "note": "model-file hashes are in the run output and verified by --verify"}),
        "eligibility_evidence.json": dumps(evidence),
        "feature_schema.json": dumps(fm.schema.as_dict()),
        "protocol_manifest.json": dumps(proto),
        "leakage_audit.json": dumps(leakage),
        "model_cards.md": cards,
    }
    for name, text in artifacts.items():
        files[f"models/{name}"] = text
    run_files = {k: v for k, v in files.items() if not k.startswith("models/")}
    hashes = {name: hashlib.sha256(text.encode("utf-8")).hexdigest() for name, text in sorted(files.items())}
    hashes["training_runs_with_artifact_hashes.json"] = hashlib.sha256(dumps({"runs": all_runs}).encode("utf-8")).hexdigest()
    run_manifest = {
        "protocol_version": mcfg.raw["protocol_version"], "snapshot_id": snapshot_id, "data_snapshot_sha256": snapshot_digest,
        "license_manifest_sha256": manifest_hash(manifest), "bench_config_fingerprint_sha256": bcfg.fingerprint,
        "stat_config_fingerprint_sha256": scfg.fingerprint, "ml_config_fingerprint_sha256": mcfg.fingerprint, "config_fingerprint_sha256": mcfg.fingerprint,
        "feature_schema_sha256": fm.schema.sha256, "stage": stage.value, "as_of": as_of.isoformat(), "timezone": "UTC",
        "random_seed": {"models_and_tuner": mcfg.raw["seed"], "bootstrap": bcfg.bootstrap_seed, "permutation": mcfg.raw["ablation"]["seed"]},
        "git_sha": sha, "git_dirty_at_run": dirty, "python": sys.version.split()[0], "numpy": np.__version__,
        "lightgbm": __import__("lightgbm").__version__, "xgboost": __import__("xgboost").__version__, "optuna": __import__("optuna").__version__,
        "dependency_lock_sha256": sha256_file(root / "requirements.lock") if (root / "requirements.lock").is_file() else None,
        "device": "CPU, single thread (deterministic); no GPU used", "matches_scored": len(ev["rows"]), "folds": [f.eval_season for f in folds],
        "output_hashes": hashes, "reproducibility_note": "timings.json is excluded from the hashes (wall-clock); tuning stops by max_trials, never by the clock",
    }  # fmt: skip
    wall_stops = [f"{r.fold.eval_season}:{t}" for r in results for t, tn in r.diagnostics["tuning"].items() if tn["stopped_by"] != "max_trials"]
    run_manifest["tuning_stopped_by_wallclock"] = wall_stops
    files["run_manifest.json"] = dumps(run_manifest)
    summary = {"plan": plan, "run_manifest": run_manifest, "decisions": {c: v["decision"] for c, v in decisions["competitions"].items()}}
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "models").mkdir(exist_ok=True)
        for name, text in files.items():
            (output_dir / name).write_text(text, encoding="utf-8", newline="\n")
        (output_dir / "training_runs_with_artifact_hashes.json").write_text(dumps({"runs": all_runs}), encoding="utf-8", newline="\n")
        (output_dir / "timings.json").write_text(dumps({r.fold.eval_season: r.timings for r in results}), encoding="utf-8", newline="\n")
        for name, expected in hashes.items():
            if sha256_file(output_dir / name) != expected:
                raise RuntimeError(f"hash mismatch after writing {name}")
        summary["registry_entry"] = register_run(output_dir, run_manifest)
    if docs_dir is not None:
        docs_dir.mkdir(parents=True, exist_ok=True)
        for name in (*(k for k in run_files if k != "fold_predictions.jsonl"), "run_manifest.json"):
            (docs_dir / f"s4_{name}").write_text(files[name], encoding="utf-8", newline="\n")
    return summary


def compare_to_committed(out_dir: Path, docs_dir: Path, atol: float) -> dict:
    exact_bad, numeric_bad = [], {}
    for name in EXACT_FILES:
        if sha256_file(out_dir / name) != sha256_file(docs_dir / f"s4_{name}"):
            exact_bad.append(name)
    for name in NUMERIC_FILES:
        bad, tr = [], []
        _walk(json.loads((docs_dir / f"s4_{name}").read_text(encoding="utf-8")), json.loads((out_dir / name).read_text(encoding="utf-8")), "", atol, bad, tr)
        bad = [b for b in bad if not any(m in b["path"] for m in OS_DEPENDENT_MODELS)]
        if bad:
            numeric_bad[name] = bad[:25]
    return {"atol": atol, "exact_files_differing": exact_bad, "numeric_differences": numeric_bad,
            "not_compared": {"fold_predictions.jsonl": "not committed (large)", "models/*.txt": "model files are git-ignored artifacts; their hashes are verified by --verify",
                             **{m: "XGBoost CPU results differ between operating systems (reproducible on one machine only); every LightGBM and statistical result is compared"
                                for m in OS_DEPENDENT_MODELS}},
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
    out = run(root, as_of.astimezone(UTC), (root / a.output_dir) if a.output_dir else None, dry_run=a.dry_run, eval_start=a.eval_start,
              eval_end=a.eval_end, docs_dir=(root / a.docs_dir) if a.docs_dir else None)  # fmt: skip
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
