"""Sprint 2 (ADR 0064): global reference benchmark over the licence-clean universe.

    python -m src.league_models.benchmark --as-of 2026-10-20T00:00:00Z --dry-run
    python -m src.league_models.benchmark --as-of 2026-10-20T00:00:00Z --output-dir artifacts/league_models/benchmark
    python -m src.league_models.benchmark ... --docs-dir docs/league_models   # also writes the committed summaries

What it does: builds the Sprint 1 universe (openfootball scores, licence gate fail-closed), splits it into DEV /
frozen HOLDOUT / RESERVED / GUARD, runs an expanding season-by-season walk-forward over DEV rows, and scores the
production-family models (always-home, league prior, Elo, Poisson, Dixon-Coles) plus the naive totals/BTTS baselines
on exactly the same fixtures. The existing production models are NOT modified: their classes are only instantiated
here with the production hyper-parameters of configs/model.yaml, fitted on the licensed universe.

What it does NOT do: touch the holdout, use odds (none exist in the licensed universe: baseline UNAVAILABLE), claim
xG, or re-score the GBMs / ensemble (their feature pipeline reads the BLOCKED football-data.co.uk source:
NOT_RUN_FEATURES_BLOCKED). Everything is deterministic: the same inputs give byte-identical result files."""

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from src.config import config_dir_for, load_config
from src.data.teams import TeamDirectory
from src.evaluation import metrics as m1x2
from src.evaluation.dataset import EvalRow
from src.models import build_models
from src.snapshot.stages import SnapshotStage

from . import bench_metrics as bm
from .competitions import ROOT
from .competitions import load_config as load_league_config
from .coverage import AuditResult, collect
from .licenses import load_manifest, manifest_hash, training_gate
from .splits import (
    BenchConfig,
    Fold,
    Universe,
    assert_disjoint,
    build_universe,
    dumps,
    history_leak_check,
    load_bench_config,
    make_folds,
    protocol_manifest,
)

LICENSED_PROVIDERS = ["openfootball_domestic", "openfootball_cups"]
OUTCOME_CLASSES = ["HOME", "DRAW", "AWAY"]  # probability order everywhere in this module
RESULT_FILES = (
    "fold_predictions.jsonl",
    "benchmark_metrics.json",
    "paired_deltas.json",
    "reliability.json",
    "protocol_manifest.json",
    "leakage_audit.json",
    "baseline_status.json",
    "claims.json",
    "global_artifact_audit.json",
)

REF_SUFFIX = {"over_under": "_totals", "btts": "_goals"}
TARGETS = ("1x2", "over_under", "btts")


# ------------------------------------------------------------------------------------------------ models
def _normalise(p: np.ndarray) -> np.ndarray:
    """The single normalisation point (see bench_metrics docstring): finite, non-negative, rows summing to 1."""
    p = np.asarray(p, dtype=float)
    if not np.isfinite(p).all() or (p < -1e-12).any():
        raise ValueError("model produced a non-finite or negative probability")
    p = np.clip(p, 0.0, None)
    s = p.sum(axis=-1, keepdims=True) if p.ndim == 2 else None
    return p / s if s is not None else np.clip(p, 0.0, 1.0)


def _rate(rows: list[EvalRow], key) -> tuple[float, dict[str, float]]:
    """(global rate, per-league rate) of a boolean property of the training rows."""
    overall = float(np.mean([key(r) for r in rows]))
    per: dict[str, list[bool]] = defaultdict(list)
    for r in rows:
        per[r.league_id].append(key(r))
    return overall, {k: float(np.mean(v)) for k, v in per.items()}


def over_line(r: EvalRow, line: float) -> bool:
    return (r.home_goals or 0) + (r.away_goals or 0) > line


def btts_yes(r: EvalRow) -> bool:
    return (r.home_goals or 0) > 0 and (r.away_goals or 0) > 0


def goal_target_probs(model, rows: list[EvalRow], line: float) -> tuple[np.ndarray, np.ndarray]:
    """P(total goals > line) and P(both teams score) from the model's own scoreline matrix (one joint distribution
    gives 1X2, totals, BTTS and the exact score, so the targets are mutually consistent)."""
    over, both = np.empty(len(rows)), np.empty(len(rows))
    goals = np.arange(model.max_goals + 1)
    total = goals[:, None] + goals[None, :]
    for i, r in enumerate(rows):
        lam_h, lam_a, _, _ = model.expected_goals(r.home_id, r.away_id)
        mat = model.scoreline_matrix(lam_h, lam_a)
        if not np.isclose(mat.sum(), 1.0, atol=1e-9):
            raise ValueError("scoreline matrix does not sum to 1")
        over[i] = mat[total > line].sum()
        both[i] = mat[1:, 1:].sum()
    return np.clip(over, 0.0, 1.0), np.clip(both, 0.0, 1.0)


@dataclass
class FoldResult:
    fold: Fold
    probs_1x2: dict[str, np.ndarray]
    probs_over: dict[str, np.ndarray]
    probs_btts: dict[str, np.ndarray]
    diagnostics: dict
    timings: dict


def run_fold(cfg: BenchConfig, fold: Fold) -> FoldResult:
    """Fit on the fold's training rows only, predict the fold's eval rows. Fresh model objects per fold (Elo advances
    its state while predicting, so an instance is never reused)."""
    model_cfg = load_config("model")
    models = {
        m.model_id: m
        for m in build_models(
            ["always_home", "historical_prior", "elo", "poisson", "dixon_coles"],
            elo_config=model_cfg.elo,
            poisson_config=model_cfg.poisson,
        )
    }
    train, ev = list(fold.train_rows), list(fold.eval_rows)
    probs_1x2, timings, diag = {}, {}, {}
    for name in cfg.models_1x2:
        t0 = time.perf_counter()
        models[name].fit(train)
        t1 = time.perf_counter()
        probs_1x2[name] = _normalise(models[name].predict_proba(ev))
        timings[name] = {"fit_s": t1 - t0, "predict_s": time.perf_counter() - t1}
        diag[name] = {k: v for k, v in models[name].diagnostics.items() if k != "league_prior"}
    line = cfg.over_under_line
    over_rate, over_by_league = _rate(train, lambda r: over_line(r, line))
    btts_rate, btts_by_league = _rate(train, btts_yes)
    n = len(ev)
    probs_over = {
        "always_over": np.ones(n),
        "always_under": np.zeros(n),
        "train_over_rate": np.array([over_by_league.get(r.league_id, over_rate) for r in ev]),
    }
    probs_btts = {
        "always_yes": np.ones(n),
        "always_no": np.zeros(n),
        "train_btts_rate": np.array([btts_by_league.get(r.league_id, btts_rate) for r in ev]),
    }
    for name in ("poisson", "dixon_coles"):
        over, both = goal_target_probs(models[name], ev, line)
        probs_over[f"{name}_totals"] = over
        probs_btts[f"{name}_goals"] = both
        # unseen-team coverage: such a row is still predicted (league-average strength) but is counted, never hidden
        seen = [models[name].expected_goals(r.home_id, r.away_id)[2:] for r in ev]
        unseen = sum(1 for s_h, s_a in seen if not (s_h and s_a))
        diag.setdefault(name, {})["rows_with_unseen_team"] = unseen
    return FoldResult(
        fold,
        probs_1x2,
        {k: probs_over[k] for k in cfg.models_over_under},
        {k: probs_btts[k] for k in cfg.models_btts},
        diag,
        timings,
    )


# ------------------------------------------------------------------------------------------------ scoring
def outcomes_for(rows: list[EvalRow], line: float) -> dict[str, np.ndarray]:
    return {
        "1x2": np.array([r.outcome for r in rows]),
        "over_under": np.array([int(over_line(r, line)) for r in rows]),
        "btts": np.array([int(btts_yes(r)) for r in rows]),
    }


def reference_for(cfg: BenchConfig, target: str) -> str:
    return cfg.reference_model if target == "1x2" else cfg.reference_model + REF_SUFFIX[target]


def _metrics(target: str, p: np.ndarray, y: np.ndarray, bins: int) -> dict:
    if target == "1x2":
        out = m1x2.compute_metrics(["log_loss", "brier", "rps", "accuracy", "ece_raw"], p, y, bins)
    else:
        out = bm.binary_metrics(p, y, bins)
    return {k: (None if v is None else round(float(v), 6)) for k, v in out.items()}


def _rows_loss(target: str, kind: str, p: np.ndarray, y: np.ndarray) -> np.ndarray:
    if target == "1x2":
        return {"log_loss": bm.rows_log_loss_1x2, "brier": bm.rows_brier_1x2}[kind](p, y)
    return {"log_loss": bm.binary_log_loss_rows, "brier": bm.binary_brier_rows}[kind](p, y)


def collect_eval(results: list[FoldResult], cfg: BenchConfig) -> dict:
    """Concatenate fold results into aligned arrays: rows, outcomes, and probabilities per (target, model)."""
    rows: list[EvalRow] = []
    fold_of: list[str] = []
    for r in results:
        rows += list(r.fold.eval_rows)
        fold_of += [r.fold.eval_season] * len(r.fold.eval_rows)
    probs: dict[str, dict[str, np.ndarray]] = {t: {} for t in TARGETS}
    for t, attr in (("1x2", "probs_1x2"), ("over_under", "probs_over"), ("btts", "probs_btts")):
        for name in getattr(results[0], attr):
            probs[t][name] = np.concatenate([getattr(r, attr)[name] for r in results])
    return {"rows": rows, "fold_of": fold_of, "probs": probs, "y": outcomes_for(rows, cfg.over_under_line)}


def score_all(ev: dict, cfg: BenchConfig, targets: tuple[str, ...] = TARGETS) -> tuple[dict, dict, dict]:
    """(metrics, paired_deltas, reliability). Groups: ALL, per league, per season, per league x season. Block
    bootstrap CIs and paired deltas for ALL and per league (sub-groups below 30 fixtures report point metrics only)."""
    rows: list[EvalRow] = ev["rows"]
    blocks = [bm.iso_week_block(r.kickoff_utc) for r in rows]
    leagues = np.array([r.league_id for r in rows])
    seasons = np.array([r.season for r in rows])
    bins = cfg.calibration_bins
    metrics: dict = {}
    deltas: dict = {}
    reliab: dict = {}
    groups: dict[str, np.ndarray] = {"ALL": np.ones(len(rows), dtype=bool)}
    for lg in sorted(set(leagues)):
        groups[f"league={lg}"] = leagues == lg
    for s in sorted(set(seasons)):
        groups[f"season={s}"] = seasons == s
    for lg in sorted(set(leagues)):
        for s in sorted(set(seasons)):
            sel = (leagues == lg) & (seasons == s)
            if sel.any():
                groups[f"league={lg}|season={s}"] = sel
    for target in targets:
        y_all = ev["y"][target]
        ref = reference_for(cfg, target)
        metrics[target], deltas[target], reliab[target] = {}, {}, {}
        for gname, sel in groups.items():
            n = int(sel.sum())
            y = y_all[sel]
            metrics[target][gname] = {}
            ci_ok = (gname == "ALL" or gname.startswith("league=")) and "|" not in gname and n >= 30
            if target != "1x2":
                metrics[target][gname]["_label_rate"] = round(float(y.mean()), 6)
            for model, p_all in ev["probs"][target].items():
                p = p_all[sel]
                entry = {"n": n, "date_range": _range(rows, sel), **_metrics(target, p, y, bins)}
                if ci_ok:
                    ci = bm.block_bootstrap_mean(
                        _rows_loss(target, "log_loss", p, y), np.array(blocks)[sel], cfg.bootstrap_samples,
                        cfg.bootstrap_seed, cfg.bootstrap_alpha,
                    )  # fmt: skip
                    entry["log_loss_block_ci"] = {
                        "lower": round(ci.lower, 6),
                        "upper": round(ci.upper, 6),
                        "blocks": ci.n_blocks,
                    }
                metrics[target][gname][model] = entry
                if gname == "ALL":
                    reliab[target][model] = (
                        bm.reliability_bins_1x2(p, y, bins)
                        if target == "1x2"
                        else bm.reliability_bins(p, y, bins)
                    )
                if ci_ok and model != ref:
                    d = {}
                    for kind in ("log_loss", "brier"):
                        pd_ = bm.paired_block_bootstrap(
                            _rows_loss(target, kind, p, y), _rows_loss(target, kind, ev["probs"][target][ref][sel], y),
                            np.array(blocks)[sel], cfg.bootstrap_samples, cfg.bootstrap_seed, cfg.bootstrap_alpha,
                        )  # fmt: skip
                        d[kind] = {
                            "delta_vs_reference": round(pd_.delta, 6),
                            "lower": round(pd_.lower, 6),
                            "upper": round(pd_.upper, 6),
                            "share_of_draws_below_zero": round(pd_.share_below_zero, 4),
                            "n": pd_.n,
                            "blocks": pd_.n_blocks,
                        }
                    deltas[target].setdefault(gname, {})[model] = {"reference": ref, **d}
    return metrics, deltas, reliab


def _range(rows: list[EvalRow], sel: np.ndarray) -> list[str]:
    ks = [r.kickoff_utc for r, s in zip(rows, sel, strict=True) if s]
    return [min(ks).isoformat(), max(ks).isoformat()]


# ------------------------------------------------------------------------------------------------ audits
def leakage_audit(
    cfg: BenchConfig, universe: Universe, folds: list[Fold], lag_hours: int, stage: SnapshotStage
) -> dict:
    """Structural checks every run repeats: split overlap/frozen leakage (raises), embargo, replay availability at
    the stage cutoff, date-only kick-offs. A non-empty `replay_violations` list is reported, never hidden."""
    assert_disjoint(folds, universe)
    per_fold = []
    for f in folds:
        bad = history_leak_check([*f.train_rows, *f.eval_rows], lag_hours, stage)
        date_only = sum(1 for r in f.eval_rows if not universe.kickoff_known.get(r.fixture_id, False))
        per_fold.append(
            {
                "eval_season": f.eval_season,
                "stage": stage.value,
                "replay_violations": len(bad),
                "replay_violation_examples": bad[:5],
                "eval_rows_with_date_only_kickoff": date_only,
                "train_rows_after_cutoff": sum(1 for r in f.train_rows if r.kickoff_utc >= f.fit_cutoff_utc),
                "frozen_rows_in_fold": 0,
            }
        )
    return {
        "checks": [
            "no fixture is in both train and eval of a fold",
            "no eval fixture appears in two folds",
            "no holdout / reserved / global-guard fixture appears in any fold",
            "every train fixture kicks off before the fold fit cutoff and is available (kickoff + lag) by then",
            "replay: the previous match of each team is available at the stage cutoff of the next one",
        ],
        "result_availability": "INFERRED (kickoff + lag); every row is BACKTEST_LIMITED (Sprint 1)",
        "stage": stage.value,
        "lag_hours": lag_hours,
        "folds": per_fold,
        "total_replay_violations": sum(x["replay_violations"] for x in per_fold),
    }


def baseline_status(universe: Universe) -> dict:
    return {
        "available": {
            "1x2": ["always_home", "historical_prior(per league)", "elo", "poisson", "dixon_coles"],
            "over_under_2_5": [
                "always_over",
                "always_under",
                "train_over_rate",
                "poisson_totals",
                "dixon_coles_totals",
            ],
            "btts": ["always_yes", "always_no", "train_btts_rate", "poisson_goals", "dixon_coles_goals"],
        },
        "UNAVAILABLE": {
            "market_favourite_1x2": "no odds exist in the licensed universe (openfootball publishes scores only); no odds were invented",
            "odds_baselines_overround": "not applicable: no odds baseline was built, so no overround or bookmaker-aggregation rule is needed",
        },
        "NOT_RUN_FEATURES_BLOCKED": {
            "xgboost_lightgbm_1x2": "the GBM feature pipeline (src/features) reads the football-data.co.uk dataset, BLOCKED for league models (ADR 0063)",
            "global_binary_classifier_over_under": "needs the same blocked feature pipeline; a score-only GBM is a Sprint 4 candidate",
            "production_ensemble": "built from the GBM and LLM out-of-fold predictions of the research benchmark; not re-scorable on this universe",
        },
        "elo_notes": "Elo outputs 1X2 only; it has no goal distribution, so it has no totals/BTTS entry",
        "universe_limits": {
            "score_only": True,
            "neutral_venue_finals": "UEFA finals are scored with a home-advantage term as if played at the home side's ground (declared limitation)",
            "uefa_scores": "90-minute scores as published by openfootball_cups",
        },
        "universe_counts": universe.counts(),
    }


def global_artifact_audit(root: Path) -> dict:
    """Task 1: what the existing global research benchmark actually is, read from its own artifacts (read-only).
    Nothing here is re-scored: that benchmark reads the football-data.co.uk dataset (BLOCKED for league models)."""
    base = root / "artifacts" / "walk_forward"
    runs = sorted(p for p in base.glob("*_walkforward") if p.is_dir()) if base.is_dir() else []
    if not runs:
        return {"status": "NOT_VERIFIED", "reason": "no walk-forward artifact directory found", "runs": []}
    out = []
    for run in runs:
        item: dict = {
            "run": run.name,
            "track": "historical_backtest (information_cutoff = kickoff; not prospective)",
        }
        hashes = (
            json.loads((run / "hashes.json").read_text(encoding="utf-8"))
            if (run / "hashes.json").is_file()
            else {}
        )
        pred = run / "predictions.jsonl"
        if pred.is_file():
            raw_bytes = pred.read_bytes()
            actual = hashlib.sha256(raw_bytes).hexdigest()
            # walk_forward.py hashes the text it is about to write (LF line ends, no trailing newline); on Windows the
            # file on disk has CRLF line ends, so the recorded hash only matches the normalised text.
            normalised = hashlib.sha256(raw_bytes.replace(b"\r\n", b"\n").strip()).hexdigest()
            recorded = hashes.get("predictions")
            item["predictions_sha256_actual_bytes"] = actual
            item["predictions_sha256_normalised_lf"] = normalised
            item["predictions_sha256_recorded"] = recorded
            item["predictions_hash_matches"] = recorded in (actual, normalised)
            item["predictions_hash_basis"] = (
                "raw bytes" if recorded == actual else "LF-normalised text" if recorded == normalised else "NO MATCH"
            )
            lines = pred.read_text(encoding="utf-8").splitlines()
            item["prediction_records"] = len(lines)
            first = json.loads(lines[0]) if lines else {}
            item["probability_order"] = "p_home, p_draw, p_away"
            item["data_version"] = first.get("data_version")
            item["feature_version"] = first.get("feature_version")
            item["models"] = sorted({json.loads(x)["model_id"] for x in lines})
        sm = run / "split_manifest.json"
        if sm.is_file():
            s = json.loads(sm.read_text(encoding="utf-8"))
            item["training_cutoffs"] = s.get("cutoffs")
            item["final_test_seasons"] = s.get("final_test_period", {}).get("seasons")
            item["folds"] = [
                {"test_season": f["test_season"], "train_seasons": f["train_seasons"]}
                for f in s.get("folds", [])
            ]
            item["dataset_content_hash"] = s.get("dataset_content_hash")
        exps = sorted((run / "experiments").glob("*.json"))
        if exps:
            e = json.loads(exps[0].read_text(encoding="utf-8"))
            item["git_sha"] = e.get("git_sha")
            item["git_dirty_at_run"] = e.get("git_dirty")
            item["dependency_lock_hash"] = e.get("dependency_lock_hash")
            item["experiment_files"] = len(exps)
        item["reusable_for_league_programme"] = (
            "NO: scored on the football-data.co.uk dataset (EPL and La Liga only; RESEARCH_ONLY, BLOCKED for league models)"
        )
        item["model_checksum"] = "NOT_RECORDED: models are refitted per fold, no serialized model file exists"
        item["feature_schema_version_field"] = item.get("feature_version")
        out.append(item)
    return {"status": "READ_FROM_ARTIFACTS", "runs": out}


# ------------------------------------------------------------------------------------------------ run
def git_sha(root: Path) -> tuple[str | None, bool | None]:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, check=True
            ).stdout.strip()
        )
        return sha, dirty
    except (OSError, subprocess.CalledProcessError):
        return None, None


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def prediction_lines(
    results: list[FoldResult], cfg: BenchConfig, stage: SnapshotStage, snapshot_id: str
) -> list[str]:
    """One JSON line per (fixture, target, model): the real out-of-sample prediction plus its provenance."""
    lines = []
    for r in results:
        y = outcomes_for(list(r.fold.eval_rows), cfg.over_under_line)
        for i, row in enumerate(r.fold.eval_rows):
            base = {
                "fixture_id": row.fixture_id,
                "league": row.league_id,
                "season": row.season,
                "kickoff_utc": row.kickoff_utc.isoformat(),
                "fold_eval_season": r.fold.eval_season,
                "stage": stage.value,
                "snapshot_id": snapshot_id,
                "track": "historical_backtest",
                "as_of_quality": "BACKTEST_LIMITED",
            }
            for model, p in r.probs_1x2.items():
                lines.append(json.dumps({**base, "target": "1x2", "model": model, "classes": OUTCOME_CLASSES,
                                         "p": [round(float(v), 8) for v in p[i]], "y": int(y["1x2"][i])}, sort_keys=True))  # fmt: skip
            for target, probs in (("over_under", r.probs_over), ("btts", r.probs_btts)):
                for model, p in probs.items():
                    lines.append(json.dumps({**base, "target": target, "model": model, "p": round(float(p[i]), 8),
                                             "y": int(y[target][i])}, sort_keys=True))  # fmt: skip
    return lines


def fit_health(results: list["FoldResult"]) -> dict:
    """Per fold and model: did the fit converge? An unconverged fit is used as-is but never hidden (existing model
    contract, ADR 0013/0014); the report quotes these flags next to the scores."""
    out = {}
    for r in results:
        d = r.diagnostics
        out[r.fold.eval_season] = {
            "elo": {
                "optimizer_success": d["elo"]["optimizer"]["success"],
                "optimizer_message": d["elo"]["optimizer"]["message"],
                "teams_rated": d["elo"]["teams_rated"],
            },
            **{
                name: {
                    "ipf_converged": d[name]["converged"],
                    "iterations_used": d[name]["iterations_used"],
                    "final_delta": d[name]["final_delta"],
                    "max_tail_mass": d[name].get("max_tail_mass"),
                    "rows_with_unseen_team": d[name]["rows_with_unseen_team"],
                    "eval_rows": len(r.fold.eval_rows),
                }
                for name in ("poisson", "dixon_coles")
            },
        }
    return out


def run_id_for(output_hashes: dict[str, str]) -> str:
    return "lmbench-" + hashlib.sha256(json.dumps(output_hashes, sort_keys=True).encode()).hexdigest()[:16]


def register_run(output_dir: Path, run_manifest: dict) -> dict:
    """Append-only registry entry (`registry.jsonl` next to the run directories). The id is derived from the output
    hashes, so an existing id with different hashes can only mean tampering: it is refused, never overwritten."""
    entry = {
        "run_id": run_id_for(run_manifest["output_hashes"]),
        "directory": output_dir.name,
        "protocol_version": run_manifest["protocol_version"],
        "snapshot_id": run_manifest["snapshot_id"],
        "data_snapshot_sha256": run_manifest["data_snapshot_sha256"],
        "config_fingerprint_sha256": run_manifest["config_fingerprint_sha256"],
        "output_hashes": run_manifest["output_hashes"],
        "stage": run_manifest["stage"],
        "as_of": run_manifest["as_of"],
        "git_sha": run_manifest["git_sha"],
    }
    path = output_dir.parent / "registry.jsonl"
    known = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.is_file() else []
    for old in known:
        if old["run_id"] == entry["run_id"]:
            if old["output_hashes"] != entry["output_hashes"]:
                raise RuntimeError(f"registry conflict: {entry['run_id']} already recorded with different hashes")
            return {"run_id": entry["run_id"], "status": "ALREADY_REGISTERED_IDENTICAL"}
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
    return {"run_id": entry["run_id"], "status": "REGISTERED"}


def verify_run(output_dir: Path) -> dict:
    """Re-hash every recorded result file against run_manifest.json (a file existing is not success)."""
    manifest = json.loads((output_dir / "run_manifest.json").read_text(encoding="utf-8"))
    bad = {}
    for name, expected in manifest["output_hashes"].items():
        path = output_dir / name
        actual = sha256_file(path) if path.is_file() else "MISSING"
        if actual != expected:
            bad[name] = actual
    return {"directory": output_dir.name, "files": len(manifest["output_hashes"]), "mismatches": bad, "ok": not bad}


def run(
    root: Path,
    as_of: datetime,
    output_dir: Path | None,
    *,
    stage: SnapshotStage | None = None,
    eval_start: str | None = None,
    eval_end: str | None = None,
    targets: tuple[str, ...] = TARGETS,
    dry_run: bool = False,
    bench_config_path: Path | None = None,
    docs_dir: Path | None = None,
    audit: AuditResult | None = None,
) -> dict:
    """`audit` lets a caller reuse an already collected Sprint 1 audit (the CLI never passes it; tests pass a
    synthetic one). The licence gate always runs first."""
    cfg = load_bench_config(bench_config_path) if bench_config_path else load_bench_config()
    stage = stage or cfg.default_stage
    league_cfg = load_league_config()
    manifest = load_manifest()
    training_gate(manifest, LICENSED_PROVIDERS, "production_ml")  # fail closed before any row is read
    if audit is None:
        directory = TeamDirectory.load(config_dir_for(root) / "team_aliases.yaml")
        audit = collect(root, league_cfg, directory, manifest, as_of)
    universe = build_universe(cfg, audit)
    folds = make_folds(cfg, universe, league_cfg.result_lag_hours)
    if eval_start:
        folds = [f for f in folds if f.eval_season >= eval_start]
    if eval_end:
        folds = [f for f in folds if f.eval_season <= eval_end]
    if not folds:
        raise SystemExit("no fold satisfies the requested window and the protocol minimums (fail closed)")
    snapshot_digest = hashlib.sha256("".join(f.sha256 for f in audit.source_files).encode()).hexdigest()
    snapshot_id = f"snap-{snapshot_digest[:12]}"
    proto = protocol_manifest(cfg, universe, folds)
    leaks = leakage_audit(cfg, universe, folds, league_cfg.result_lag_hours, stage)
    plan = {
        "dry_run": dry_run,
        "snapshot_id": snapshot_id,
        "stage": stage.value,
        "as_of": as_of.isoformat(),
        "targets": list(targets),
        "folds": proto["folds"],
        "universe_counts": proto["universe_counts"],
        "license_manifest_sha256": manifest_hash(manifest),
        "config_fingerprint_sha256": cfg.fingerprint,
    }
    if dry_run:
        return {"plan": plan}
    results = [run_fold(cfg, f) for f in folds]
    ev = collect_eval(results, cfg)
    metrics, deltas, reliab = score_all(ev, cfg, targets)
    sha, dirty = git_sha(root)
    files: dict[str, str] = {
        "fold_predictions.jsonl": "\n".join(prediction_lines(results, cfg, stage, snapshot_id)) + "\n",
        "benchmark_metrics.json": dumps(
            {
                "protocol_version": cfg.protocol_version,
                "snapshot_id": snapshot_id,
                "stage": stage.value,
                "primary_metric": "log_loss",
                "reference_model": cfg.reference_model,
                "no_single_winner": True,
                "metrics": metrics,
                "fit_health": fit_health(results),
                "model_diagnostics": {r.fold.eval_season: r.diagnostics for r in results},
            }
        ),  # fmt: skip
        "paired_deltas.json": dumps(
            {
                "definition": "delta = model - reference on the same fixtures; negative = model better",
                "bootstrap": {
                    "block": "iso_week",
                    "samples": cfg.bootstrap_samples,
                    "seed": cfg.bootstrap_seed,
                    "alpha": cfg.bootstrap_alpha,
                },
                "deltas": deltas,
            }
        ),  # fmt: skip
        "reliability.json": dumps(
            {
                "bins": cfg.calibration_bins,
                "note": "diagnostics on raw probabilities; nothing is calibrated in Sprint 2",
                "curves": reliab,
            }
        ),  # fmt: skip
        "protocol_manifest.json": dumps(proto),
        "leakage_audit.json": dumps(leaks),
        "baseline_status.json": dumps(baseline_status(universe)),
        "global_artifact_audit.json": dumps(global_artifact_audit(root)),
    }
    from .claims import claims_report  # local import: keeps the module graph acyclic

    files["claims.json"] = dumps(claims_report(root, ev, cfg, metrics))
    hashes = {name: hashlib.sha256(text.encode("utf-8")).hexdigest() for name, text in sorted(files.items())}
    run_manifest = {
        "protocol_version": cfg.protocol_version,
        "snapshot_id": snapshot_id,
        "data_snapshot_sha256": snapshot_digest,
        "license_manifest_sha256": manifest_hash(manifest),
        "config_fingerprint_sha256": cfg.fingerprint,
        "model_config_sha256": sha256_file(config_dir_for(root) / "model.yaml"),
        "stage": stage.value,
        "as_of": as_of.isoformat(),
        "timezone": "UTC",
        "random_seed": {
            "bootstrap": cfg.bootstrap_seed,
            "models": "deterministic (no randomness; BFGS / IPF)",
        },
        "git_sha": sha,
        "git_dirty_at_run": dirty,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "numpy": np.__version__,
        "dependency_lock_sha256": sha256_file(root / "requirements.lock")
        if (root / "requirements.lock").is_file()
        else None,
        "matches_scored": len(ev["rows"]),
        "folds": [f.eval_season for f in folds],
        "output_hashes": hashes,
        "reproducibility_note": "timings.json is excluded from the hashes (wall-clock); every other file is deterministic",
    }
    files["run_manifest.json"] = dumps(run_manifest)
    timings = {r.fold.eval_season: r.timings for r in results}
    summary = {"plan": plan, "run_manifest": run_manifest, "headline": headline(metrics, targets)}
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            (output_dir / name).write_text(text, encoding="utf-8", newline="\n")
        (output_dir / "timings.json").write_text(dumps(timings), encoding="utf-8", newline="\n")
        for name, expected in {**hashes}.items():  # never assume success because a file exists
            if sha256_file(output_dir / name) != expected:
                raise RuntimeError(f"hash mismatch after writing {name}")
        summary["registry_entry"] = register_run(output_dir, run_manifest)
    if docs_dir is not None:
        docs_dir.mkdir(parents=True, exist_ok=True)
        for name in ("protocol_manifest.json", "benchmark_metrics.json", "paired_deltas.json", "reliability.json",
                     "leakage_audit.json", "baseline_status.json", "claims.json", "global_artifact_audit.json", "run_manifest.json"):  # fmt: skip
            (docs_dir / f"s2_{name}").write_text(files[name], encoding="utf-8", newline="\n")
    return summary


def headline(metrics: dict, targets: tuple[str, ...]) -> dict:
    out = {}
    for target in targets:
        out[target] = {
            m: {k: v[k] for k in ("n", "log_loss", "brier", "accuracy") if k in v}
            for m, v in metrics[target]["ALL"].items()
            if m != "_label_rate"
        }
    return out


SOLVER_TRACE_KEYS = frozenset(
    {"iterations_used", "n_iter", "final_delta", "optimizer_message", "message", "objective", "status",
     "converged", "ipf_converged", "optimizer_success", "success", "max_tail_mass", "final_params"}
)  # fmt: skip
# discontinuous quantities (a bootstrap draw or one probability crossing a bin edge flips them): wider, still small
KEY_ATOL = {"share_of_draws_below_zero": 0.01, "ece_raw": 0.01}
EXACT_FILES = ("protocol_manifest.json", "leakage_audit.json", "baseline_status.json")
NUMERIC_FILES = ("benchmark_metrics.json", "paired_deltas.json", "reliability.json")
ENVIRONMENT_DEPENDENT = {
    "global_artifact_audit.json": "reads the local walk-forward artifacts (git-ignored, absent in CI)",
    "claims.json": "reads the local forecast artifacts (git-ignored, absent in CI)",
    "fold_predictions.jsonl": "not committed (large); its content is covered by the metrics compared here",
}


def _walk(a, b, path: str, atol: float, out: list[dict], trace: list[dict]) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        if "share_of_draws_below_zero" in a and abs(a.get("delta_vs_reference", 1)) < 1e-6 and abs(b.get("delta_vs_reference", 1)) < 1e-6:
            # a paired delta that is zero to 6 digits (Poisson vs Dixon-Coles totals): the share of draws below zero is
            # decided by float noise, so it carries no information and is skipped
            a, b = ({k: v for k, v in d.items() if k != "share_of_draws_below_zero"} for d in (a, b))
        for k in sorted({*a, *b}):
            if k not in a or k not in b:
                out.append({"path": f"{path}/{k}", "expected": a.get(k, "<absent>"), "got": b.get(k, "<absent>")})
            else:
                _walk(a[k], b[k], f"{path}/{k}", atol, out, trace)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append({"path": path, "expected": f"len {len(a)}", "got": f"len {len(b)}"})
            return
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            _walk(x, y, f"{path}[{i}]", atol, out, trace)
    elif isinstance(a, bool) or isinstance(b, bool) or a is None or b is None or isinstance(a, str) or isinstance(b, str):
        if a != b:
            (trace if path.rsplit("/", 1)[-1] in SOLVER_TRACE_KEYS else out).append({"path": path, "expected": a, "got": b})
    else:
        leaf = path.rsplit("/", 1)[-1].split("[")[0]
        if abs(float(a) - float(b)) > max(atol, KEY_ATOL.get(leaf, 0.0)):
            (trace if leaf in SOLVER_TRACE_KEYS else out).append(
                {"path": path, "expected": a, "got": b, "abs_diff": abs(float(a) - float(b))}
            )


def compare_to_committed(out_dir: Path, docs_dir: Path, atol: float) -> dict:
    """Cross-platform check against the committed s2_* files: structural files must be byte-identical; metric files
    must agree numerically within `atol` (last-bit float differences between operating systems are expected, a changed
    result is not). Solver-trace fields (iteration counts, convergence flags) are reported, never failed. Files that
    depend on git-ignored local artifacts are listed as not compared, with the reason."""
    exact_bad, numeric_bad, trace = [], {}, {}
    for name in EXACT_FILES:
        if sha256_file(out_dir / name) != sha256_file(docs_dir / f"s2_{name}"):
            exact_bad.append(name)
    for name in NUMERIC_FILES:
        bad, tr = [], []
        _walk(json.loads((docs_dir / f"s2_{name}").read_text(encoding="utf-8")),
              json.loads((out_dir / name).read_text(encoding="utf-8")), "", atol, bad, tr)  # fmt: skip
        if bad:
            numeric_bad[name] = bad[:25]
        if tr:
            trace[name] = tr[:25]
    return {
        "atol": atol,
        "exact_files_differing": exact_bad,
        "numeric_differences": numeric_bad,
        "solver_trace_differences_reported_only": trace,
        "not_compared": ENVIRONMENT_DEPENDENT,
        "ok": not exact_bad and not numeric_bad,
    }


def reproduce(root: Path, previous: Path, as_of: datetime, a) -> int:
    """Same inputs must give the same files: re-run into a scratch directory and compare hashes (never assume)."""
    import tempfile

    recorded = json.loads((root / previous / "run_manifest.json").read_text(encoding="utf-8"))["output_hashes"]
    with tempfile.TemporaryDirectory() as tmp:
        fresh = run(
            root, as_of, Path(tmp) / "rerun",
            stage=SnapshotStage(a.stage) if a.stage else None, eval_start=a.eval_start, eval_end=a.eval_end,
            bench_config_path=Path(a.config) if a.config else None,
        )  # fmt: skip
    diff = {k: [recorded.get(k), v] for k, v in fresh["run_manifest"]["output_hashes"].items() if recorded.get(k) != v}
    verdict = {"status": "REPRODUCED" if not diff else "NOT_REPRODUCIBLE", "compared_files": len(recorded), "differences": diff}
    sys.stdout.write(dumps(verdict))
    return 0 if not diff else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--as-of", default=None, help="audit time, ISO UTC (default: now)")
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument(
        "--config", default=None, help="benchmark config path (default configs/league_models/benchmark.yaml)"
    )
    ap.add_argument("--stage", default=None, choices=[s.value for s in SnapshotStage])
    ap.add_argument("--eval-start", default=None, help="first eval season label, e.g. 2021-22")
    ap.add_argument("--eval-end", default=None, help="last eval season label")
    ap.add_argument("--target", default="all", choices=["all", *TARGETS])
    ap.add_argument("--output-dir", default=None)
    ap.add_argument("--docs-dir", default=None, help="also write the committed summaries (s2_*.json) here")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify", default=None, metavar="DIR", help="re-hash a finished run against its manifest")
    ap.add_argument(
        "--reproduce", default=None, metavar="DIR",
        help="re-run into a temporary directory and compare every output hash with DIR (NOT_REPRODUCIBLE on a diff)",
    )  # fmt: skip
    ap.add_argument(
        "--expect-manifest", default=None, metavar="FILE",
        help="after the run, compare every output hash with the output_hashes of a committed run manifest "
        "(exit 1 on a difference); used by the CI job on the archived source snapshot",
    )  # fmt: skip
    ap.add_argument(
        "--compare-committed", default=None, metavar="DOCS_DIR",
        help="after the run, compare the written files with the committed s2_* files in DOCS_DIR (cross-platform "
        "check: exact for structural files, numeric within --atol for metric files)",
    )  # fmt: skip
    ap.add_argument("--atol", type=float, default=1e-3, help="absolute tolerance of --compare-committed")
    a = ap.parse_args(argv)
    if a.verify:
        res = verify_run(Path(a.root) / a.verify)
        sys.stdout.write(dumps(res))
        return 0 if res["ok"] else 1
    as_of = datetime.fromisoformat(a.as_of.replace("Z", "+00:00")) if a.as_of else datetime.now(UTC)
    if as_of.tzinfo is None:
        sys.exit("--as-of must carry a UTC offset or Z")
    root = Path(a.root)
    targets = TARGETS if a.target == "all" else (a.target,)
    if a.reproduce:
        return reproduce(root, Path(a.reproduce), as_of.astimezone(UTC), a)
    out = run(
        root, as_of.astimezone(UTC), (root / a.output_dir) if a.output_dir else None,
        stage=SnapshotStage(a.stage) if a.stage else None, eval_start=a.eval_start, eval_end=a.eval_end,
        targets=targets, dry_run=a.dry_run, bench_config_path=Path(a.config) if a.config else None,
        docs_dir=(root / a.docs_dir) if a.docs_dir else None,
    )  # fmt: skip
    sys.stdout.write(dumps(out))
    if a.compare_committed and not a.dry_run:
        if not a.output_dir:
            sys.exit("--compare-committed needs --output-dir")
        report = compare_to_committed(root / a.output_dir, root / a.compare_committed, a.atol)
        sys.stdout.write(dumps({"compare_committed": report}))
        return 0 if report["ok"] else 1
    if a.expect_manifest and not a.dry_run:
        expected = json.loads(Path(a.expect_manifest).read_text(encoding="utf-8"))["output_hashes"]
        got = out["run_manifest"]["output_hashes"]
        diff = {k: [expected.get(k), got.get(k)] for k in sorted({*expected, *got}) if expected.get(k) != got.get(k)}
        sys.stdout.write(dumps({"expect_manifest": a.expect_manifest, "differences": diff}))
        return 1 if diff else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
