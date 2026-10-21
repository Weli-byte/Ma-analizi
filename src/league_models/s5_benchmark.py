"""Sprint 5 (ADR 0067): calibration, the one-shot frozen holdout, the approval gate and the router dry run.

    python -m src.league_models.s5_benchmark --as-of 2026-10-20T00:00:00Z --stage dev     --output-dir artifacts/league_models/s5/dev --docs-dir docs/league_models
    python -m src.league_models.s5_benchmark --as-of 2026-10-20T00:00:00Z --stage holdout --rehearsal --output-dir artifacts/league_models/s5/rehearsal
    python -m src.league_models.s5_benchmark --as-of 2026-10-20T00:00:00Z --stage holdout --output-dir artifacts/league_models/s5/final --docs-dir docs/league_models   # ONCE
    python -m src.league_models.s5_benchmark --verify DIR | --compare-committed docs/league_models

Stages
  dev      Sprint 2 / 3 / 4 candidates on the development folds, calibrator selection by inner walk-forward, the dev screen (which candidate, if any, is worth one
           confirmation test per league x target cell) and the PRE-REGISTRATION manifest (policy, configurations, snapshot, selections, holdout listing hash). No
           holdout score is read. This is the stage CI re-runs.
  holdout  re-runs the dev stage, refuses to go on unless it reproduces the committed pre-registration, then opens the frozen holdout ONCE (the access is logged
           and the door closed before any score is returned), refits every model on all development seasons, applies the frozen calibrators, runs the paired
           evaluation and the approval gate, and writes the immutable score report. `--rehearsal` runs the identical code on a pseudo-holdout (the last development
           season) with its own log, so the real holdout is never needed to debug the code.
The production models in src/models are untouched; nothing here serves production traffic (the router can only select an approved artifact for canary / shadow)."""

import argparse
import dataclasses
import hashlib
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from src.config import config_dir_for
from src.data.teams import TeamDirectory

from . import bench_metrics as bm
from . import ml_benchmark as mb
from . import stat_benchmark as sb
from .approval_gate import (
    APPROVED,
    BLOCKED,
    GLOBAL_KEPT,
    REJECTED,
    SHADOW,
    CellEvidence,
    PairedVsReference,
    evaluate_cell,
    holm,
    load_policy,
)
from .benchmark import (
    FoldResult,
    _metrics,
    _rows_loss,
    collect_eval,
    git_sha,
    outcomes_for,
    sha256_file,
    verify_run,
)
from .benchmark import run_fold as s2_run_fold
from .calibrators import fit_calibrator, select_method
from .competitions import ROOT
from .competitions import load_config as load_league_config
from .coverage import collect
from .holdout_lock import HOLDOUT_LOG, holdout_rows, is_consumed, read_log
from .licenses import load_manifest, manifest_hash, training_gate
from .ml_features import FeatureConfig, build_features
from .ml_models import TARGETS, load_ml_config
from .prediction_router import GLOBAL, RegistryEntry
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

LICENSED_PROVIDERS = mb.LICENSED_PROVIDERS
POLICY_FILE = ROOT / "configs" / "league_models" / "approval_policy.yaml"
MODE_OF = {"E_elo_bridge": "LEAGUE_ADAPTED", "B_gbm_league": "LEAGUE_ADAPTED", "B_dc_league": "LEAGUE_ADAPTED", "C_gbm_local": "LOCAL", "C_dc_local": "LOCAL",
           "F_elo_local": "LOCAL", "D_gbm_blend": "BLENDED", "D_blend": "BLENDED"}  # fmt: skip
EXACT_FILES = ("split_manifest.json",)
NUMERIC_FILES = ("calibration_manifest.json", "dev_screen.json", "preregistration.json", "dev_paired.json")
SUFFIX = {"over_under": "_totals", "btts": "_goals"}


def base_name(key: str) -> str:
    for s in SUFFIX.values():
        if key.endswith(s):
            return key[: -len(s)]
    return key


# ------------------------------------------------------------------------------------------------ the stack of candidates on a list of folds
@dataclasses.dataclass
class Stack:
    rows: list
    fold_idx: np.ndarray
    probs: dict            # target -> model key -> (n,) or (n, 3)
    y: dict                # target -> labels
    fold_labels: list[str]
    diags: list[dict]
    artifacts: dict
    runs: list[dict]
    leagues: np.ndarray
    seasons: np.ndarray

    def mask(self, **kw) -> np.ndarray:
        m = np.ones(len(self.rows), bool)
        if "fold_ge" in kw:
            m &= self.fold_idx >= kw["fold_ge"]
        if "league" in kw:
            m &= self.leagues == kw["league"]
        return m


def run_stack(folds: list[Fold], mcfg, scfg, bcfg, fm, snapshot_id: str, git: str | None, lag_hours: int) -> Stack:
    results: list[FoldResult] = []
    diags, artifacts, runs = [], {}, []
    for fold in folds:
        fr = mb.FoldRun(mcfg, scfg, bcfg, fold, fm, snapshot_id, git, lag_hours)
        res = fr.run()
        s2 = s2_run_fold(bcfg, fold)  # the production-family baselines (Poisson, Dixon-Coles) on the same fold
        for name in ("poisson", "dixon_coles"):
            res.probs_1x2.setdefault(name, s2.probs_1x2[name])
            res.probs_over.setdefault(f"{name}_totals", s2.probs_over[f"{name}_totals"])
            res.probs_btts.setdefault(f"{name}_goals", s2.probs_btts[f"{name}_goals"])
        results.append(res)
        diags.append({"sprint3_fallbacks": fr.diag["sprint3_fallbacks"], "local": fr.diag["local"], "blend": fr.diag["blend"]})
        artifacts.update(fr.artifacts)
        runs += fr.runs
    ev = collect_eval(results, bcfg)
    fold_idx = np.concatenate([np.full(len(f.eval_rows), i) for i, f in enumerate(folds)])
    rows = ev["rows"]
    return Stack(rows, fold_idx, ev["probs"], ev["y"], [f.eval_season for f in folds], diags, artifacts, runs,
                 np.array([r.league_id for r in rows]), np.array([r.season for r in rows]))  # fmt: skip


def holdout_fold(dev_rows: list, eval_rows: list, lag_hours: int) -> Fold:
    """The same rule as make_folds: train = development rows available (kick-off + lag) at the earliest kick-off of the evaluated rows."""
    cutoff = min(r.kickoff_utc for r in eval_rows)
    lag = timedelta(hours=lag_hours)
    train = tuple(sorted((r for r in dev_rows if r.kickoff_utc + lag <= cutoff), key=lambda r: (r.kickoff_utc, r.fixture_id)))
    seasons = sorted({r.season for r in train}, key=lambda s: int(s.split("-")[0]))
    counts = {s: sum(1 for r in train if r.season == s) for s in seasons}
    inner = next((s for s in reversed(seasons) if counts[s] >= 300), seasons[-1])  # a handful of early matches of the newest season is not a validation block
    return Fold(sorted({r.season for r in eval_rows})[0], tuple(seasons), inner, cutoff, train, tuple(sorted(eval_rows, key=lambda r: (r.kickoff_utc, r.fixture_id))))


# ------------------------------------------------------------------------------------------------ calibration
def model_keys(policy: dict, target: str) -> list[str]:
    m = policy["models"]
    keys = [*m["references"][target], *m["candidates"][target]]
    if target in m["naive"]:
        keys.append(m["naive"][target])
    return list(dict.fromkeys(keys))


def calibrate_dev(policy: dict, stack: Stack, n_dev_folds: int) -> tuple[dict, dict, dict, int]:
    """Per target and model: choose the method by inner walk-forward over the development folds, fit the final calibrator on all of them, and build the
    walk-forward calibrated probabilities of the evaluated folds (each fitted on earlier folds only)."""
    c = policy["calibration"]
    first = min(c["folds_first_eval_index"], max(1, n_dev_folds - 1))
    kw = {"ridge": c["league_ridge"], "min_isotonic": c["min_isotonic_rows"], "min_rows": c["min_rows"]}
    selection, finals, wf = {}, {}, {}
    for target in TARGETS:
        selection[target], finals[target], wf[target] = {}, {}, {}
        for key in model_keys(policy, target):
            p = stack.probs[target][key]
            fp = [p[stack.fold_idx == k] for k in range(n_dev_folds)]
            fy = [stack.y[target][stack.fold_idx == k] for k in range(n_dev_folds)]
            fl = [list(stack.leagues[stack.fold_idx == k]) for k in range(n_dev_folds)]
            sel = select_method(fp, fy, fl, target, first_eval_fold=first, margin=c["min_gain_over_none"], **kw)
            selection[target][key] = sel
            finals[target][key] = fit_calibrator(sel["chosen"], target, np.concatenate(fp), np.concatenate(fy), [x for f in fl for x in f], **kw)
            out = np.full(p.shape, np.nan)
            for k in range(first, n_dev_folds):
                cal = fit_calibrator(sel["chosen"], target, np.concatenate(fp[:k]), np.concatenate(fy[:k]), [x for f in fl[:k] for x in f], **kw)
                m = stack.fold_idx == k
                out[m] = cal.apply(p[m], stack.leagues[m])
            wf[target][key] = out
    return selection, finals, wf, first


def screen_dev(policy: dict, stack: Stack, wf: dict, first: int) -> dict:
    """For every league x target cell: among the candidates whose calibrated walk-forward log loss is below EVERY reference, the best one is the single
    candidate confirmed on the holdout. No interval is used here (the holdout confirms); a cell with no such candidate stays on the global model."""
    sc = policy["screen"]
    out = {}
    for lg in sorted(set(stack.leagues)):
        for target in TARGETS:
            m = stack.mask(fold_ge=first, league=lg)
            cell = f"{lg}|{target}"
            n = int(m.sum())
            if n < sc["min_dev_rows"]:
                out[cell] = {"selected": None, "reason": f"{n} dev rows < {sc['min_dev_rows']}", "n": n}
                continue
            y = stack.y[target][m]
            ll = {k: float(np.mean(_loss(target, wf[target][k][m], y))) for k in model_keys(policy, target)}
            refs = policy["models"]["references"][target]
            passing = [c for c in policy["models"]["candidates"][target] if all(ll[c] < ll[r] for r in refs)]
            best_any = min(policy["models"]["candidates"][target], key=lambda c: ll[c])
            out[cell] = {"selected": min(passing, key=lambda c: ll[c]) if passing else None, "descriptive_best_candidate": best_any, "n": n,
                         "calibrated_walk_forward_log_loss": {k: round(v, 6) for k, v in ll.items()},
                         "reason": "best candidate below every reference" if passing else "no candidate is below every reference: global model kept"}  # fmt: skip
    return out


def _loss(target: str, p: np.ndarray, y: np.ndarray) -> np.ndarray:
    return bm.rows_log_loss_1x2(p, y) if target == "1x2" else bm.binary_log_loss_rows(np.clip(p, 1e-6, 1 - 1e-6), y)


def apply_final(finals: dict, stack: Stack) -> dict:
    return {t: {k: finals[t][k].apply(stack.probs[t][k], stack.leagues) for k in finals[t]} for t in finals}


# ------------------------------------------------------------------------------------------------ paired evaluation on the holdout
def paired(cal: dict, stack: Stack, target: str, cand: str, ref: str, sel: np.ndarray, pooled_ece: dict, pcfg: dict, bcfg) -> PairedVsReference:
    y = stack.y[target][sel]
    pc, pr = cal[target][cand][sel], cal[target][ref][sel]
    blocks = [bm.iso_week_block(r.kickoff_utc) for r, s in zip(stack.rows, sel, strict=True) if s]
    pd_ = bm.paired_block_bootstrap(_loss(target, pc, y), _loss(target, pr, y), blocks, pcfg["samples"], bcfg.bootstrap_seed, pcfg["alpha"])
    brier = float(np.mean(_rows_loss(target, "brier", pc, y)) - np.mean(_rows_loss(target, "brier", pr, y)))
    acc = float(_metrics(target, pc, y, 10)["accuracy"] - _metrics(target, pr, y, 10)["accuracy"]) if len(y) else 0.0
    p_one = max(1.0 - pd_.share_below_zero, 1.0 / (pcfg["samples"] + 1))
    return PairedVsReference(pd_.delta, pd_.lower, pd_.upper, p_one, brier, acc, pooled_ece[(target, cand)] - pooled_ece[(target, ref)])


def coverage_of(stack: Stack, cand: str, lg: str, sel: np.ndarray) -> float:
    """Share of the cell's fixtures the candidate really predicted (finite probabilities, no explicit local fallback)."""
    name = base_name(cand)
    ok = np.ones(int(sel.sum()), bool)
    idx = np.where(sel)[0]
    for j, i in enumerate(idx):
        fold = stack.diags[stack.fold_idx[i]]
        if name == "C_gbm_local" and fold["local"].get(lg, {}).get("status") != "TRAINED":
            ok[j] = False
        if name in ("C_dc_local", "F_elo_local") and f"{name}:{lg}" in fold["sprint3_fallbacks"]:
            ok[j] = False
    return float(ok.mean()) if len(ok) else 0.0


def latency_probe(mcfg, scfg, bcfg, fold: Fold, fm) -> dict:
    """Representative inference latency per fixture (milliseconds) of each family on the last development fold: models with default parameters are fitted
    first (not timed), then only prediction is timed. Candidate blends cost the sum of their parts."""
    from .ml_models import GBM, full_params
    from .stat_models import TimeAwareElo

    train, ev = list(fold.train_rows), list(fold.eval_rows)
    Xtr, Xev = fm.take([r.fixture_id for r in train]), fm.take([r.fixture_id for r in ev])
    y = outcomes_for(train, bcfg.over_under_line)["1x2"]
    out = {}
    names = list(fm.schema.names)
    gb = GBM("lightgbm", "1x2", full_params(mcfg, None), names, ["league_code"], 1).fit(Xtr, y, int(mcfg.raw["gbm"]["rounds_default"]))
    t0 = time.perf_counter()
    gb.predict_proba(Xev)
    gbm_ms = (time.perf_counter() - t0) * 1000 / len(ev)
    el = TimeAwareElo(scfg.elo_spec(), "probe", mb.sb.UEFA_IDS).fit(train)
    t0 = time.perf_counter()
    el.predict_proba(ev)
    elo_ms = (time.perf_counter() - t0) * 1000 / len(ev)
    mod, _ = sb._fit_dc(scfg.dc_spec(), "probe_dc", train, None)
    t0 = time.perf_counter()
    mod.predict(ev)
    dc_ms = (time.perf_counter() - t0) * 1000 / len(ev)
    out.update({"B_gbm_league": gbm_ms * 3, "C_gbm_local": gbm_ms * 3, "D_gbm_blend": gbm_ms * 6, "E_elo_bridge": elo_ms, "B_dc_league": dc_ms, "D_blend": dc_ms * 2,
                "elo_s2": elo_ms, "A_gbm_tuned": gbm_ms * 3, "A_dc_ridge_tuned": dc_ms, "dixon_coles": dc_ms})  # fmt: skip
    return {"unit": "milliseconds per fixture, three targets for the GBMs", "method": "default-parameter models fitted on the last development fold, prediction timed",
            "ms_per_fixture": {k: round(v, 4) for k, v in out.items()}}  # fmt: skip


# ------------------------------------------------------------------------------------------------ run
def windows(folds: list[Fold], holdout_note: dict) -> dict:
    return {
        "development_folds": [{"eval_season": f.eval_season, "train_seasons": list(f.train_seasons), "inner_validation_season": f.inner_validation_season,
                               "train_rows": len(f.train_rows), "eval_rows": len(f.eval_rows),
                               "eval_utc": [min(r.kickoff_utc for r in f.eval_rows).isoformat(), max(r.kickoff_utc for r in f.eval_rows).isoformat()]} for f in folds],
        "roles": {"TRAIN": "all development seasons before the evaluated season (expanding)", "INNER_VALIDATION": "last training season of each fold (tuning, early stopping, blend weights)",
                  "OOF_CALIBRATION_AND_SELECTION": "the development folds' out-of-fold predictions: the calibrator method is chosen by inner walk-forward (fit on folds before k, score fold k) "
                                                   "and the final calibrator is fitted on all of them; every such prediction was made by a model that had not seen that fixture's label",
                  "FROZEN_HOLDOUT": holdout_note},
        "documented_exception": "for the final refit the development fixtures are training rows while their out-of-fold predictions were used for calibration (cross-fitting); "
                                "a calibrator never sees a prediction made by a model that was trained on that fixture. The frozen holdout is disjoint from every other role."}  # fmt: skip


def run(root: Path, as_of: datetime, output_dir: Path | None, *, stage: str = "dev", rehearsal: bool = False, docs_dir: Path | None = None, audit=None,
        bench_config_path: Path | None = None, stat_config_path: Path | None = None, ml_config_path: Path | None = None, policy_path: Path | None = None,
        holdout_log: Path | None = None, expect_prereg: Path | None = None, dirty_override: bool | None = None, dry_run: bool = False) -> dict:
    if stage not in ("dev", "holdout"):
        raise ValueError("stage must be dev or holdout")
    policy = load_policy(policy_path) if policy_path else load_policy()
    policy_sha = hashlib.sha256((policy_path or POLICY_FILE).read_bytes()).hexdigest()
    bcfg = load_bench_config(bench_config_path) if bench_config_path else load_bench_config()
    scfg = sb.load_stat_config(stat_config_path) if stat_config_path else sb.load_stat_config()
    mcfg = load_ml_config(ml_config_path) if ml_config_path else load_ml_config()
    league_cfg = load_league_config()
    manifest = load_manifest()
    training_gate(manifest, LICENSED_PROVIDERS, "production_ml")  # a revoked or missing licence stops the run before any row is read
    license_status = "APPROVED"
    if audit is None:
        directory = TeamDirectory.load(config_dir_for(root) / "team_aliases.yaml")
        audit = collect(root, league_cfg, directory, manifest, as_of)
    universe = build_universe(bcfg, audit)
    all_folds = make_folds(bcfg, universe, league_cfg.result_lag_hours)
    assert_disjoint(all_folds, universe)
    dev_folds = all_folds[:-1] if rehearsal else all_folds
    if len(dev_folds) < 3:
        raise SystemExit("too few development folds for an inner walk-forward calibration (fail closed)")
    proto = protocol_manifest(bcfg, universe, dev_folds)
    s4 = root / "docs" / "league_models" / "s4_protocol_manifest.json"
    if s4.is_file() and not rehearsal:
        prev = {f["eval_season"]: f for f in json.loads(s4.read_text(encoding="utf-8"))["folds"]}
        if not all(prev.get(f["eval_season"]) == f for f in proto["folds"]):
            raise SystemExit("the development folds differ from the committed Sprint 4 folds (fail closed)")
    stage_cut = bcfg.default_stage
    leaks = {f.eval_season: len(history_leak_check([*f.train_rows, *f.eval_rows], league_cfg.result_lag_hours, stage_cut)) for f in dev_folds}
    if any(leaks.values()):
        raise SystemExit(f"replay violations found: {leaks} (fail closed)")
    snapshot_digest = hashlib.sha256("".join(f.sha256 for f in audit.source_files).encode()).hexdigest()
    snapshot_id = f"snap-{snapshot_digest[:12]}"
    if dry_run:
        return {"plan": {"stage": stage, "rehearsal": rehearsal, "snapshot_id": snapshot_id, "development_folds": [f.eval_season for f in dev_folds],
                         "holdout_listing_sha256": proto["frozen"]["holdout"]["fixture_ids_sha256"], "holdout_fixtures": proto["frozen"]["holdout"]["count"],
                         "holdout_already_consumed": is_consumed(holdout_log or HOLDOUT_LOG), "policy_sha256": policy_sha}}  # fmt: skip
    sha, dirty = git_sha(root)
    if dirty_override is not None:
        dirty = dirty_override
    fcfg = FeatureConfig.from_raw(mcfg.raw, mcfg.raw["league_vocabulary"], league_cfg.result_lag_hours, stage_cut, scfg.elo_spec())
    dev_rows = list(universe.dev)
    holdout_note = ({"kind": "REHEARSAL pseudo-holdout: the last development season; the real frozen holdout is not read", "season": all_folds[-1].eval_season,
                     "fixtures": len(all_folds[-1].eval_rows)} if rehearsal else
                    {"kind": "FROZEN: fixtures listed by id hash only until the single read", "fixtures": proto["frozen"]["holdout"]["count"],
                     "fixture_ids_sha256": proto["frozen"]["holdout"]["fixture_ids_sha256"], "seasons": "2025-26 (Süper Lig: 2024-25)"})  # fmt: skip

    # ---- development stage
    fm = build_features(dev_rows, fcfg)
    dev = run_stack(dev_folds, mcfg, scfg, bcfg, fm, snapshot_id, sha, league_cfg.result_lag_hours)
    selection, finals, wf, first = calibrate_dev(policy, dev, len(dev_folds))
    screen = screen_dev(policy, dev, wf, first)
    cal_manifest = {"policy_version": policy["policy_version"], "first_walk_forward_eval_fold": dev.fold_labels[first], "folds": dev.fold_labels,
                    "selection": selection, "final_calibrators": {t: {k: c.as_dict() for k, c in finals[t].items()} for t in finals},
                    "rule": "a calibrator is used only when it beats 'none' by the declared margin in mean walk-forward log loss; ECE is reported, never used to select",
                    "no_calibrator_ever_saw": "the evaluated fold of its own walk-forward step, or the frozen holdout"}  # fmt: skip
    dev_paired = dev_pairs(policy, dev, wf, first, bcfg)
    selected = {cell: v["selected"] for cell, v in screen.items() if v.get("selected")}
    prereg = {"policy_version": policy["policy_version"], "policy_sha256": policy_sha, "ml_config_fingerprint_sha256": mcfg.fingerprint, "stat_config_fingerprint_sha256": scfg.fingerprint,
              "bench_config_fingerprint_sha256": bcfg.fingerprint, "license_manifest_sha256": manifest_hash(manifest), "snapshot_id": snapshot_id,
              "data_snapshot_sha256": snapshot_digest, "development_folds": dev.fold_labels, "holdout": holdout_note,
              "calibration_choices": {t: {k: v["chosen"] for k, v in selection[t].items()} for t in selection},
              "screen_selection": dict(sorted(selected.items())), "cells_on_global_model": sorted(c for c, v in screen.items() if not v.get("selected")),
              "confirmation_test": "paired block bootstrap (iso-week blocks), one-sided, every reference of the target, Holm across the selected cells",
              "rehearsal": rehearsal}  # fmt: skip
    prereg_text = dumps(prereg)
    prereg_sha = hashlib.sha256(prereg_text.encode("utf-8")).hexdigest()
    files = {"calibration_manifest.json": dumps(cal_manifest), "dev_screen.json": dumps({"definition": screen_definition(policy), "cells": screen}),
             "dev_paired.json": dumps(dev_paired), "preregistration.json": prereg_text,
             "split_manifest.json": dumps({"protocol": proto, "windows": windows(dev_folds, holdout_note), "fixture_roles_disjoint": role_check(dev_folds, universe)})}
    result: dict = {"stage": stage, "prereg_sha256": prereg_sha, "selected_cells": len(selected)}

    if stage == "holdout":
        committed = expect_prereg or (root / "docs" / "league_models" / "s5_preregistration.json")
        if not rehearsal:
            if not committed.is_file():
                raise SystemExit("no committed pre-registration: run --stage dev first and commit it (fail closed)")
            if hashlib.sha256(committed.read_bytes()).hexdigest() != prereg_sha:
                raise SystemExit("the pre-registration does not reproduce (code, configuration, data or selection changed): the holdout stays closed")
        log = holdout_log or (HOLDOUT_LOG if not rehearsal else (output_dir or root) / "rehearsal_holdout_access_log.json")
        if rehearsal:
            h_rows = list(all_folds[-1].eval_rows)
            from .holdout_lock import consume

            consume(log, purpose="s5_rehearsal_pseudo_holdout", prereg_sha256=prereg_sha, fixture_ids_sha256=hashlib.sha256(",".join(sorted(r.fixture_id for r in h_rows)).encode()).hexdigest(),
                    n_fixtures=len(h_rows), git_sha=sha, dirty=None)  # fmt: skip
        else:
            h_rows = holdout_rows(audit, bcfg, universe, log_path=log, prereg_sha256=prereg_sha, git_sha=sha, dirty=dirty)
        result["holdout"] = holdout_stage(policy, mcfg, scfg, bcfg, fcfg, league_cfg, dev_rows, h_rows, dev_folds, dev, finals, screen, selected, prereg_sha, snapshot_id, sha, rehearsal,
                                          fm_dev=fm, license_status=license_status, output_dir=output_dir, files=files)  # fmt: skip
    return finish(root, output_dir, docs_dir, files, result, stage, rehearsal, prereg_sha, sha, dirty, snapshot_id, policy_sha, mcfg, scfg, bcfg)


def role_check(folds: list[Fold], universe) -> dict:
    frozen = set(universe.holdout_ids) | set(universe.reserved_ids) | set(universe.guard_ids)
    seen: set[str] = set()
    for f in folds:
        ev = {r.fixture_id for r in f.eval_rows}
        assert not (ev & frozen) and not ({r.fixture_id for r in f.train_rows} & frozen) and not (ev & seen)
        seen |= ev
    return {"holdout_reserved_guard_in_any_fold": 0, "evaluation_fixtures_overlapping_between_folds": 0, "train_and_eval_overlap_inside_a_fold": 0,
            "evaluated_fixtures": len(seen)}  # fmt: skip


def screen_definition(policy: dict) -> str:
    return ("For every league x target cell with enough dev rows, the candidates whose CALIBRATED walk-forward log loss is below every reference are screened; the best one "
            "is the only candidate confirmed on the holdout. Nothing else is tested for approval, which keeps the family of confirmation tests small. Declared before the holdout.")


def dev_pairs(policy: dict, stack: Stack, wf: dict, first: int, bcfg) -> dict:
    """Descriptive paired deltas of the calibrated development walk-forward predictions (pooled per target, candidate against each reference)."""
    pc = {"samples": 2000, "alpha": 0.05}
    out = {}
    m = stack.mask(fold_ge=first)
    blocks = [bm.iso_week_block(r.kickoff_utc) for r, s in zip(stack.rows, m, strict=True) if s]
    for target in TARGETS:
        y = stack.y[target][m]
        out[target] = {}
        for c in policy["models"]["candidates"][target]:
            out[target][c] = {}
            for r in policy["models"]["references"][target]:
                d = bm.paired_block_bootstrap(_loss(target, wf[target][c][m], y), _loss(target, wf[target][r][m], y), blocks, pc["samples"], bcfg.bootstrap_seed, pc["alpha"])
                out[target][c][r] = {"delta_log_loss": round(d.delta, 6), "ci95": [round(d.lower, 6), round(d.upper, 6)], "n": d.n}
    return {"definition": "calibrated walk-forward development predictions (folds from the first evaluated fold), pooled over leagues; negative = candidate better", "pairs": out}


def holdout_stage(policy, mcfg, scfg, bcfg, fcfg, league_cfg, dev_rows, h_rows, dev_folds, dev: Stack, finals, screen, selected, prereg_sha, snapshot_id, sha, rehearsal, *, fm_dev, license_status, output_dir, files) -> dict:
    lag = league_cfg.result_lag_hours
    groups: dict[str, list] = {}
    for r in h_rows:
        groups.setdefault(r.season, []).append(r)
    all_rows = dev_rows if rehearsal else [*dev_rows, *h_rows]
    fm = fm_dev if rehearsal else build_features(all_rows, fcfg)
    folds = [holdout_fold(dev_rows, rs, lag) for _, rs in sorted(groups.items())]
    hold = run_stack(folds, mcfg, scfg, bcfg, fm, snapshot_id, sha, lag)
    cal = apply_final(finals, hold)
    pcfg = policy["bootstrap"]
    g = policy["holdout_gate"]
    # pooled calibrated ECE per model (calibration regression is judged on the whole target, not on a 300-row cell)
    pooled_ece = {(t, k): float(_metrics(t, cal[t][k], hold.y[t], bcfg.calibration_bins)["ece_raw"]) for t in TARGETS for k in cal[t]}
    raw_metrics = {t: {k: _metrics(t, hold.probs[t][k], hold.y[t], bcfg.calibration_bins) for k in cal[t]} for t in TARGETS}
    cal_metrics = {t: {k: _metrics(t, cal[t][k], hold.y[t], bcfg.calibration_bins) for k in cal[t]} for t in TARGETS}
    latency = latency_probe(mcfg, scfg, bcfg, dev_folds[-1], fm_dev)
    train_n = {lg: sum(1 for r in folds[0].train_rows if r.league_id == lg) for lg in {r.league_id for r in h_rows}}
    cells = {}
    pvals = {}
    for cell, cand in sorted(selected.items()):
        lg, target = cell.split("|")
        sel = hold.mask(league=lg)
        if not sel.any():
            cells[cell] = {"candidate": cand, "status": BLOCKED, "reasons": ["no holdout rows for the cell"]}
            continue
        refs = {r: paired(cal, hold, target, cand, r, sel, pooled_ece, pcfg, bcfg) for r in policy["models"]["references"][target]}
        pvals[cell] = max(s.p_one_sided for s in refs.values())
        cells[cell] = {"candidate": cand, "refs": refs, "n": int(sel.sum())}
    holm_ok = holm(pvals, pcfg["alpha"])
    decisions = {}
    for cell, info in cells.items():
        lg, target = cell.split("|")
        if "refs" not in info:
            decisions[cell] = {"candidate": info["candidate"], "status": info["status"], "reasons": info["reasons"], "checks": []}
            continue
        sel = hold.mask(league=lg)
        cand = info["candidate"]
        ev = CellEvidence(lg, target, cand, info["n"], coverage_of(hold, cand, lg, sel), train_n.get(lg, 0), base_name(cand) in policy["models"]["local_models"], g["require_license_status"] if license_status == "APPROVED" else license_status,
                          True, True, True, latency["ms_per_fixture"].get(base_name(cand), 0.0), info["refs"], holm_ok.get(cell, False))  # fmt: skip
        d = evaluate_cell(policy, ev)
        decisions[cell] = {"candidate": cand, "status": d.status, "reasons": d.reasons, "checks": d.checks, "holm_adjusted_pass": holm_ok.get(cell, False), "p_value_max_over_references": round(pvals[cell], 6),
                           "holdout_rows": info["n"], "paired": {r: dataclasses.asdict(s) for r, s in info["refs"].items()}}  # fmt: skip
    for cell, v in screen.items():
        if cell not in decisions:
            decisions[cell] = {"candidate": None, "status": GLOBAL_KEPT, "reasons": [v["reason"]], "checks": []}
    matrix = results_matrix(policy, hold, cal, pooled_ece, pcfg, bcfg, screen, decisions, sorted({r.league_id for r in h_rows}))
    diagnostics = holdout_diagnostics(policy, hold, cal, matrix, bcfg)
    counts = {s: sum(1 for d in decisions.values() if d["status"] == s) for s in (APPROVED, SHADOW, REJECTED, BLOCKED, GLOBAL_KEPT)}
    report = {"prereg_sha256": prereg_sha, "rehearsal": rehearsal, "fixtures_scored": len(hold.rows), "folds": hold.fold_labels,
              "raw_metrics": raw_metrics, "calibrated_metrics": cal_metrics, "holm_family_size": len(pvals), "alpha": pcfg["alpha"],
              "status_counts": counts, "latency": latency, "label": "REHEARSAL on a pseudo-holdout (last development season)" if rehearsal else "FROZEN HOLDOUT, evaluated once"}  # fmt: skip
    files["holdout_report.json"] = dumps(report)
    files["approval_decisions.json"] = dumps({"policy_version": policy["policy_version"], "status_counts": counts, "decisions": decisions})
    files["results_matrix.json"] = dumps(matrix)
    files["holdout_diagnostics.json"] = dumps(diagnostics)
    registry = build_registry(policy, hold, decisions, snapshot_id, folds, output_dir, rehearsal)
    files["model_registry.json"] = dumps({"entries": [dataclasses.asdict(e) for e in registry["entries"]], "note": registry["note"]})
    files["router_dry_run.json"] = dumps(registry["dry_run"])
    files["monitoring_contract.json"] = dumps(monitoring_contract(policy, decisions))
    files["rollback_plan.md"] = rollback_plan()
    files["model_cards.md"] = cards(policy, decisions, matrix, report)
    return {"status_counts": counts, "holm_family_size": len(pvals), "decisions": {c: d["status"] for c, d in decisions.items()}, "artifacts": registry["artifacts"]}


def results_matrix(policy, hold: Stack, cal, pooled_ece, pcfg, bcfg, screen, decisions, leagues) -> dict:
    """Won / lost / not enough evidence for every league x target cell, against every reference, on the same holdout fixtures (descriptive candidate when the dev
    screen picked none: shown, never approved)."""
    out = {}
    for lg in leagues:
        for target in TARGETS:
            cell = f"{lg}|{target}"
            sel = hold.mask(league=lg)
            cand = (screen.get(cell) or {}).get("selected") or (screen.get(cell) or {}).get("descriptive_best_candidate")
            if cand is None or not sel.any():
                out[cell] = {"label": "NO_DATA", "n": int(sel.sum())}
                continue
            refs = {r: paired(cal, hold, target, cand, r, sel, pooled_ece, pcfg, bcfg) for r in policy["models"]["references"][target]}
            blocks = {bm.iso_week_block(r.kickoff_utc) for r, s in zip(hold.rows, sel, strict=True) if s}
            if all(s.ci_upper < 0 for s in refs.values()):
                label = "WON"
            elif any(s.ci_lower > 0 for s in refs.values()):
                label = "LOST"
            else:
                label = "INSUFFICIENT_EVIDENCE"
            y = hold.y[target][sel]
            naive = policy["models"]["naive"].get(target)
            entry = {"candidate": cand, "selected_by_dev_screen": bool((screen.get(cell) or {}).get("selected")), "label": label, "n": int(sel.sum()), "iso_week_blocks": len(blocks),
                     "gate_status": decisions.get(cell, {}).get("status", GLOBAL_KEPT), "vs_references": {r: {"delta_log_loss": round(s.delta_log_loss, 6), "ci95": [round(s.ci_lower, 6), round(s.ci_upper, 6)],
                                                                                    "delta_brier": round(s.delta_brier, 6), "delta_accuracy": round(s.delta_accuracy, 4)} for r, s in refs.items()}}  # fmt: skip
            if naive:
                ll = float(np.mean(_loss(target, cal[target][naive][sel], y)))
                entry["train_rate_baseline_log_loss"] = round(ll, 6)
                entry["label_rate"] = round(float(y.mean()), 4)
                entry["candidate_log_loss"] = round(float(np.mean(_loss(target, cal[target][cand][sel], y))), 6)
            out[cell] = entry
    return {"definition": "WON: log-loss interval below 0 against every reference; LOST: above 0 against at least one; otherwise INSUFFICIENT_EVIDENCE. 27 cells are shown; a small nominal "
                          "difference is not a win, and an empty cell is NO_DATA, not a success.", "cells": out}  # fmt: skip


def holdout_diagnostics(policy, hold: Stack, cal, matrix, bcfg) -> dict:
    out = {"reliability_and_confusion": {}, "always_over_check": {}, "notes": {
        "always_over_68_8": "NOT_VERIFIABLE: the 138-match report is not in the licensed universe; the base rates of this holdout are listed below",
        "market_odds_baseline": "NOT_AVAILABLE: the licensed universe has no timestamped odds",
        "correct_score": "see exact_score; derived from the Dixon-Coles matrix only"}}  # fmt: skip
    for target in TARGETS:
        y = hold.y[target]
        cands = [c for c in policy["models"]["candidates"][target]]
        entry = {"label_rate": None if target == "1x2" else round(float(y.mean()), 4)}
        for name in (policy["models"]["production_baseline"][target], *cands[:2]):
            p = cal[target][name]
            entry[name] = {"calibration_bins": bm.reliability_bins_1x2(p, y, bcfg.calibration_bins) if target == "1x2" else bm.reliability_bins(p, y, bcfg.calibration_bins)}
            if target == "1x2":
                conf = np.zeros((3, 3), int)
                for a, b in zip(y, p.argmax(axis=1), strict=True):
                    conf[a, b] += 1
                entry[name]["confusion_rows_actual_cols_predicted_HDA"] = conf.tolist()
        out["reliability_and_confusion"][target] = entry
        if target != "1x2":
            rate = float(y.mean())
            out["always_over_check"][target] = {"base_rate_yes_or_over": round(rate, 4), "majority_class_accuracy": round(max(rate, 1 - rate), 4), "fixtures": int(len(y))}
    return out


def build_registry(policy, hold: Stack, decisions, snapshot_id, folds, output_dir, rehearsal) -> dict:
    """Registry of what actually exists: booster files for GBM models (real files, real hashes), a recipe artifact (canonical JSON, real hash) for models that
    are refitted deterministically (Elo, Dixon-Coles). Entries of the router dry run use gate statuses; nothing is registered that was not produced."""
    from .prediction_router import PredictionRouter

    art_dir = (output_dir / "models") if output_dir else None
    entries: list[RegistryEntry] = []
    produced: dict[str, str] = {}
    if art_dir is not None:
        art_dir.mkdir(parents=True, exist_ok=True)
        for name, text in hold.artifacts.items():
            (art_dir / name).write_text(text, encoding="utf-8", newline="\n")
            produced[name] = hashlib.sha256(text.encode("utf-8")).hexdigest()

    def recipe(model: str, target: str) -> tuple[str, str]:
        text = dumps({"model": model, "target": target, "snapshot_id": snapshot_id, "feature_schema": "lm-s4-features-v1", "refit": "deterministic from the configurations fingerprinted in the pre-registration",
                      "train_rows_last_fold": len(folds[-1].train_rows)})  # fmt: skip
        if art_dir is not None:
            (art_dir / "recipes").mkdir(exist_ok=True)
            (art_dir / "recipes" / f"{model}__{target}.json").write_text(text, encoding="utf-8", newline="\n")
        return f"models/recipes/{model}__{target}.json", hashlib.sha256(text.encode("utf-8")).hexdigest()

    for target in TARGETS:
        base = policy["models"]["production_baseline"][target]
        path, sha = recipe(base, target)
        entries.append(RegistryEntry(base, "baseline-" + snapshot_id[5:], GLOBAL, target, "*", "GLOBAL_REFERENCE", path, sha, "recipe"))
    for cell, d in sorted(decisions.items()):
        if d["candidate"] is None:
            continue
        lg, target = cell.split("|")
        cand = d["candidate"]
        name = base_name(cand)
        label = hold.fold_labels[0] if (lg == "TUR1" and len(hold.fold_labels) > 1) else hold.fold_labels[-1]
        fam = {"B_gbm_league": "B_gbm_league", "C_gbm_local": "C_gbm_local", "D_gbm_blend": "B_gbm_league"}.get(name)
        found = None
        if fam:
            scope = lg if fam == "C_gbm_local" else "GLOBAL"
            key = f"{label}__{fam}__{target}__{scope}.txt"
            if key in produced:
                found = (f"models/{key}", produced[key], "booster_file")
        if found is None:
            path, sha = recipe(name, target)
            found = (path, sha, "recipe")
        entries.append(RegistryEntry(name, "v-" + hashlib.sha256((name + target + snapshot_id).encode()).hexdigest()[:10], MODE_OF.get(name, "LEAGUE_ADAPTED"), target, lg, d["status"], found[0], found[1], found[2]))
    router = PredictionRouter(entries, output_dir or Path("."), production_fraction=policy["router"]["production_fraction"])
    dry = {"production_fraction": policy["router"]["production_fraction"], "routes": []}
    for e in entries:
        if e.mode == GLOBAL:
            continue
        for use in ("production", "canary", "shadow"):
            try:
                r = router.route(e.target, e.league, "t-24h", use=use)
                dry["routes"].append(dataclasses.asdict(r))
            except Exception as ex:  # a dry run records an unusable registry, it does not hide it
                dry["routes"].append({"target": e.target, "league": e.league, "use": use, "error": str(ex)})
    dry["alarms"] = router.alarms
    dry["note"] = "dry run only: the router selects, it never predicts; production use always routes to the global model until Sprint 6"
    return {"entries": entries, "dry_run": dry, "artifacts": produced if output_dir else {}, "note": "artifact hashes are of files that exist in the run output"}


def monitoring_contract(policy, decisions) -> dict:
    return {
        "for_sprint_6": "what the live shadow / canary monitor must record and alarm on",
        "per_prediction_record": ["fixture_id", "league", "target", "stage", "model_id", "version", "route_mode", "fallback_reason", "generated_at_utc (real call time)",
                                  "probabilities", "calibrator_id", "feature_schema_sha256", "snapshot_id"],
        "metrics_to_track": ["log loss", "Brier", "ECE", "coverage", "fallback rate", "latency p50/p95", "paired delta against the global reference on the same fixtures"],
        "alarms": ["artifact hash mismatch", "fallback rate above 5%", "paired log-loss interval above 0 for 4 consecutive weeks",
                   "ECE worse than the reference by more than the policy margin", "latency above the policy ceiling"],
        "rollback_trigger": "any alarm of a canary model routes that league and target back to GLOBAL",
        "canary_candidates": sorted(c for c, d in decisions.items() if d["status"] == APPROVED),
        "policy_version": policy["policy_version"],
    }  # fmt: skip


def rollback_plan() -> str:
    return ("# Rollback plan (Sprint 5, for the Sprint 6 rollout)\n\n1. The global model is never removed or modified; every league and target has a GLOBAL route.\n"
            "2. A canary route is a registry entry with status APPROVED_FOR_CANARY. Rolling back = setting that entry's status to REJECTED or removing it: the router then returns GLOBAL "
            "with `NO_APPROVED_MODEL`.\n3. An artifact whose hash does not match its registry entry falls back to GLOBAL with an alarm automatically.\n"
            "4. Production traffic fraction is 0.0 in the policy; nothing here changes what users see.\n5. The frozen holdout cannot be re-read; a new decision needs new data (a new frozen period) and an ADR.\n")


def cards(policy, decisions, matrix, report) -> str:
    lines = [f"# Sprint 5 model cards and decisions ({report['label']})", "", "Calibrated probabilities (method chosen by inner walk-forward, fitted on out-of-fold development predictions). Odds-aware models: NOT_AVAILABLE. "
             "A status is a product decision under the declared policy, not a guarantee.", "", "| Cell | Candidate | Gate status | Matrix label | n | Reasons |", "|---|---|---|---|---|---|"]
    for cell, d in sorted(decisions.items()):
        m = matrix["cells"].get(cell, {})
        lines.append(f"| `{cell}` | {d['candidate'] or '-'} | {d['status']} | {m.get('label', 'NO_DATA')} | {m.get('n', 0)} | {'; '.join(d['reasons'])[:140]} |")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------------------------------------ outputs
def finish(root, output_dir, docs_dir, files, result, stage, rehearsal, prereg_sha, sha, dirty, snapshot_id, policy_sha, mcfg, scfg, bcfg) -> dict:
    hashes = {n: hashlib.sha256(t.encode("utf-8")).hexdigest() for n, t in sorted(files.items())}
    manifest = {"protocol_version": "lm-s5-v1", "stage": stage, "rehearsal": rehearsal, "snapshot_id": snapshot_id, "prereg_sha256": prereg_sha, "policy_sha256": policy_sha,
                "ml_config_fingerprint_sha256": mcfg.fingerprint, "stat_config_fingerprint_sha256": scfg.fingerprint, "bench_config_fingerprint_sha256": bcfg.fingerprint,
                "git_sha": sha, "git_dirty_at_run": dirty, "python": sys.version.split()[0], "numpy": np.__version__, "output_hashes": hashes,
                "holdout_access_log": [{k: e[k] for k in ("event", "status", "purpose", "fixtures", "prereg_sha256")} for e in read_log(HOLDOUT_LOG)] if not rehearsal else "rehearsal log in the run output"}  # fmt: skip
    files["run_manifest.json"] = dumps(manifest)
    result["run_manifest"] = manifest
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        for n, t in files.items():
            (output_dir / n).write_text(t, encoding="utf-8", newline="\n")
        for n, expected in hashes.items():
            if sha256_file(output_dir / n) != expected:
                raise RuntimeError(f"hash mismatch after writing {n}")
    if docs_dir is not None:
        docs_dir.mkdir(parents=True, exist_ok=True)
        for n, t in files.items():
            if n in ("holdout_report.json", "approval_decisions.json", "results_matrix.json", "holdout_diagnostics.json", "model_registry.json", "router_dry_run.json") and stage != "holdout":
                continue
            (docs_dir / f"s5_{n}").write_text(t, encoding="utf-8", newline="\n")
    return result


def compare_to_committed(out_dir: Path, docs_dir: Path, atol: float) -> dict:
    from .benchmark import _walk

    exact_bad, numeric_bad = [], {}
    for n in EXACT_FILES:
        if sha256_file(out_dir / n) != sha256_file(docs_dir / f"s5_{n}"):
            exact_bad.append(n)
    for n in NUMERIC_FILES:
        bad, tr = [], []
        _walk(json.loads((docs_dir / f"s5_{n}").read_text(encoding="utf-8")), json.loads((out_dir / n).read_text(encoding="utf-8")), "", atol, bad, tr)
        bad = [b for b in bad if not any(m in b["path"] for m in mb.OS_DEPENDENT_MODELS) and not b["path"].endswith("/git_sha")]
        if bad:
            numeric_bad[n] = bad[:25]
    return {"atol": atol, "exact_files_differing": exact_bad, "numeric_differences": numeric_bad,
            "not_compared": {"holdout_*": "the frozen holdout is read once and never re-run in CI; its committed report is verified by hash only"}, "ok": not exact_bad and not numeric_bad}  # fmt: skip


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--stage", default="dev", choices=["dev", "holdout"])
    ap.add_argument("--rehearsal", action="store_true")
    ap.add_argument("--output-dir", default=None)
    ap.add_argument("--docs-dir", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify", default=None, metavar="DIR")
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
    out = run(root, as_of.astimezone(UTC), (root / a.output_dir) if a.output_dir else None, stage=a.stage, rehearsal=a.rehearsal,
              docs_dir=(root / a.docs_dir) if a.docs_dir else None, dry_run=a.dry_run)  # fmt: skip
    sys.stdout.write(dumps({k: v for k, v in out.items() if k != "run_manifest"}))
    if a.compare_committed and not a.dry_run:
        if not a.output_dir:
            sys.exit("--compare-committed needs --output-dir")
        report = compare_to_committed(root / a.output_dir, root / a.compare_committed, a.atol)
        sys.stdout.write(dumps({"compare_committed": report}))
        return 0 if report["ok"] else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
