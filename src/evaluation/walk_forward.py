"""S7 — walk-forward backtest engine: one reproducible experiment loop for every model.

    python -m src.evaluation.walk_forward [--root DIR] [--mode development|research|strict]

Reuses the S3 split contract (`split.walk_forward_folds`, expanding or rolling per
`evaluation.split_strategy`): for each fold, train on `fold.train_seasons`, predict
`fold.test_season` (never a final-test season — ADR 0004), append to ONE immutable,
content-hashed prediction ledger, and write one `ExperimentRecord` per (fold, model) with its
train/test date range, cutoff, model_version, feature_version, git SHA and a `config_hash`
computed from its own config (fold info included) — the S7 "config hashing" requirement.

A fold's test-season prediction is a walk-forward DIAGNOSTIC, never a model-selection signal:
this loop declares no overall winner, same discipline as `run_baselines` (docs/baselines.md).
The technical lock stays with `EvaluationContext`: every fold lives entirely inside
train+validation seasons, so final-test seasons are structurally unreachable here, not just
policy-excluded.

Reproducibility: same config + same commit + same data content -> byte-identical
`predictions.jsonl` / report hashes (`tests/test_walk_forward.py`), because every model is
refit from scratch each fold (fresh instances via `build_models`) and every model class already
carries its own determinism guarantee (S4/S5/S6 tests).
"""

import argparse
import hashlib
import json
import shutil
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.cli_utils import configure_output
from src.config import config_dir_for, load_config
from src.data.dataset import resolve_dataset
from src.features.artifact import load_features
from src.models import build_models
from src.provenance import collect
from src.runmode import RunMode
from src.schemas import ExperimentRecord, PredictionLedger, PredictionRecord, transition
from src.schemas.common import PredictionStatus as PS
from src.schemas.lifecycle import dump_records
from src.versioning import canonical_json

from .context import EvalMode, make_context
from .dataset import load_rows
from .runner import EvalSettings, evaluate
from .split import Fold, build_split_manifest, walk_forward_folds

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class FoldResult:
    fold: Fold
    train_range: tuple[str, str]
    test_range: tuple[str, str]
    metrics: dict[str, dict]  # model_id -> metrics


@dataclass(frozen=True)
class WalkForwardOutput:
    out_dir: Path
    fold_results: list[FoldResult]
    hashes: dict[str, str]


def _fold_predictions(rep, meta: dict, cutoff_offset_hours: float) -> list[PredictionRecord]:
    ledger = PredictionLedger()
    versions = {r.model_id: r.model_version for r in rep.results}
    for model_id, probs in rep.probs.items():
        for row, p in zip(rep.common_rows, probs, strict=True):
            cutoff = row.kickoff_utc - timedelta(hours=cutoff_offset_hours)
            rec = PredictionRecord(
                fixture_id=row.fixture_id,
                model_id=model_id,
                model_version=versions[model_id],
                feature_version=meta["feature_version"],
                data_version=meta["data_version"],
                kickoff_utc=row.kickoff_utc,
                information_cutoff=cutoff,
                generated_at=cutoff,
                p_home=float(p[0]),
                p_draw=float(p[1]),
                p_away=float(p[2]),
            )
            for status in (PS.DRAFT, PS.PUBLISHED, PS.LOCKED, PS.EVALUATED):
                rec = rec if status == PS.DRAFT else transition(rec, status)
                ledger.append(rec)
    return ledger.latest_records()


def run_walk_forward(root: Path = ROOT, mode: RunMode | str = RunMode.RESEARCH) -> WalkForwardOutput:
    mode = RunMode(mode)
    cdir = config_dir_for(root)
    data_cfg = load_config("data", cdir)
    model_cfg = load_config("model", cdir)
    eval_cfg = load_config("evaluation", cdir)
    feat_cfg = load_config("features", cdir)
    prov = collect(mode, root)

    ref = resolve_dataset(root / data_cfg.processed_dir)
    feats = load_features(root, ref, model_cfg.feature_version)  # stale artifacts fail loudly
    split = build_split_manifest(eval_cfg, ref)
    folds = walk_forward_folds(eval_cfg)
    if not folds:
        raise ValueError("walk-forward config produces zero folds; check min_train_seasons")

    ctx = make_context(EvalMode.VALIDATION, eval_cfg)  # blocks final-test only; folds live entirely
    settings = EvalSettings(  # inside train+validation seasons regardless of which fold they fall in
        run_mode=mode,
        metrics=list(eval_cfg.metrics),
        bins=eval_cfg.calibration_bins,
        bootstrap_samples=0,  # walk-forward is a per-fold diagnostic loop, not the CI-bearing
        bootstrap_seed=eval_cfg.bootstrap_seed,  # benchmark run_baselines already is
        max_fallback_rate=getattr(eval_cfg.max_fallback_rate, mode.value),
    )

    tag = f"{ref.data_version}_{model_cfg.feature_version}_{split['split_id']}_walkforward"
    out_dir = root / "artifacts" / "walk_forward" / tag
    tmp = out_dir.with_name(f".tmp-{uuid.uuid4().hex[:8]}")
    (tmp / "experiments").mkdir(parents=True)

    all_records: list[PredictionRecord] = []
    fold_results: list[FoldResult] = []
    fold_reports: list[dict] = []
    try:
        for fold in folds:
            train_rows = load_rows(ref, ctx, list(fold.train_seasons), feats)
            test_rows = load_rows(ref, ctx, [fold.test_season], feats)
            models = build_models(  # fresh instances every fold: no state carries across folds
                model_cfg.models, model_cfg.elo, model_cfg.poisson, model_cfg.gbm
            )
            rep = evaluate(models, train_rows, test_rows, settings)

            meta = {"data_version": ref.data_version, "feature_version": model_cfg.feature_version}
            all_records += _fold_predictions(rep, meta, feat_cfg.cutoff_offset_hours)

            train_start = min(r.kickoff_utc for r in train_rows).isoformat()
            train_end = max(r.kickoff_utc for r in train_rows).isoformat()
            test_start = min(r.kickoff_utc for r in test_rows).isoformat()
            test_end = max(r.kickoff_utc for r in test_rows).isoformat()

            metrics_by_model: dict[str, dict] = {}
            for r in rep.results:
                metrics_by_model[r.model_id] = r.metrics
                exp = ExperimentRecord(
                    experiment_id=f"{tag}_fold{fold.index}_{r.model_id}",
                    run_mode=mode,
                    git_sha=prov.git_sha,
                    git_dirty=prov.git_dirty,
                    dirty_files=prov.dirty_files,
                    python_version=prov.python_version,
                    platform=prov.platform,
                    dependency_lock_hash=prov.dependency_lock_hash,
                    data_version=ref.data_version,
                    feature_version=model_cfg.feature_version,
                    config={
                        "model": model_cfg.model_dump(),
                        "evaluation": eval_cfg.model_dump(),
                        "features": feat_cfg.model_dump(),
                        "fold": {
                            "index": fold.index,
                            "train_seasons": list(fold.train_seasons),
                            "test_season": fold.test_season,
                            "train_start_utc": train_start,
                            "train_end_utc": train_end,
                            "test_start_utc": test_start,
                            "test_end_utc": test_end,
                            "cutoff_utc": train_end,  # information cutoff between train and test
                        },
                    },
                    model_name=r.model_id,
                    model_version=r.model_version,
                    seed=model_cfg.seed,
                    split_id=split["split_id"],
                    train_rows=len(train_rows),
                    validation_rows=len(test_rows),
                    final_test_rows=0,
                    metrics=r.metrics,
                    created_at_utc=datetime.now(UTC),
                )
                (tmp / "experiments" / f"fold{fold.index}_{r.model_id}.json").write_text(
                    exp.model_dump_json(indent=2), encoding="utf-8"
                )
            fold_results.append(
                FoldResult(fold, (train_start, train_end), (test_start, test_end), metrics_by_model)
            )
            fold_reports.append(
                {
                    "index": fold.index,
                    "train_seasons": list(fold.train_seasons),
                    "test_season": fold.test_season,
                    "train_range": [train_start, train_end],
                    "test_range": [test_start, test_end],
                    "n_train_rows": len(train_rows),
                    "n_test_rows": len(test_rows),
                    "n_common_rows": rep.n_common_rows,
                    "metrics": metrics_by_model,
                }
            )

        pred_text = dump_records(all_records)
        (tmp / "predictions.jsonl").write_text(pred_text + "\n", encoding="utf-8")
        report = {
            "split_id": split["split_id"],
            "data_version": ref.data_version,
            "feature_version": model_cfg.feature_version,
            "strategy": eval_cfg.split_strategy,
            "n_folds": len(folds),
            "folds": fold_reports,
            "notes": [
                "Every fold trains and predicts strictly inside train+validation seasons; final-test "
                "seasons are never read here (ADR 0004) and this run never unlocks a FINAL context.",
                "A fold's test-season prediction is a walk-forward diagnostic, not a model-selection "
                "signal: no fold or aggregate here declares an overall winner (docs/baselines.md).",
                "bootstrap_samples=0 for every fold: point metrics per fold, not confidence intervals "
                "(run_baselines is the CI-bearing benchmark).",
            ],
        }
        report_json = json.dumps(report, indent=2, sort_keys=True, default=str)
        (tmp / "report.json").write_text(report_json, encoding="utf-8")
        (tmp / "split_manifest.json").write_text(
            json.dumps(split, indent=2, sort_keys=True), encoding="utf-8"
        )
        hashes = {
            "predictions": hashlib.sha256(pred_text.encode()).hexdigest(),
            "report_json": hashlib.sha256(report_json.encode()).hexdigest(),
            "metrics": hashlib.sha256(
                canonical_json({f"fold{fr['index']}": fr["metrics"] for fr in fold_reports}).encode()
            ).hexdigest(),
        }
        (tmp / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")
        if out_dir.exists():
            shutil.rmtree(out_dir)
        tmp.replace(out_dir)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
    return WalkForwardOutput(out_dir, fold_results, hashes)


def main(argv: list[str] | None = None) -> int:
    configure_output()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--mode", default="research", choices=[m.value for m in RunMode if m != RunMode.FINAL])
    a = p.parse_args(argv)
    try:
        out = run_walk_forward(Path(a.root), a.mode)
    except (RuntimeError, ValueError, PermissionError, KeyError) as e:
        print(f"WALK-FORWARD RUN FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    print(f"{len(out.fold_results)} folds; results written to {out.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
