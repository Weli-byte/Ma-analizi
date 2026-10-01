"""S11: combine base models' walk-forward OOF predictions into an ensemble.

    python -m src.evaluation.run_ensemble [--root DIR] [--mode research]

Requires `python -m src.evaluation.walk_forward` to have already run for the same
data/feature/split version (reads its `predictions.jsonl`, never recomputes OOF predictions
itself). See ADR 0022 for the three-way chronological split (fit-ensemble / fit-calibration /
report) and why final-test seasons can never enter this (walk-forward's own
`EvaluationContext` already blocks them at the source).
"""

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from src.cli_utils import configure_output
from src.config import config_dir_for, load_config
from src.data.dataset import resolve_dataset
from src.features.artifact import load_features
from src.runmode import RunMode
from src.schemas import PredictionRecord

from .calibration import apply_temperature, fit_temperature
from .context import EvalMode, make_context
from .dataset import load_rows
from .ensemble import (
    build_meta_features,
    fit_lightgbm_stacking,
    fit_logistic_stacking,
    fit_validation_weights,
    predict_lightgbm_stacking,
    predict_logistic_stacking,
    simple_mean,
    weighted_average,
)
from .metrics import compute_metrics
from .split import build_split_manifest

ROOT = Path(__file__).resolve().parents[2]
ENSEMBLE_VERSION = "1.0.0"  # bumped whenever the combination LOGIC changes, not per-run
MIN_SPLIT_ROWS = 15  # per third of the 3-way split; below this a fit is noise


class EnsembleError(RuntimeError):
    pass


def _load_walk_forward_predictions(root: Path, tag: str) -> list[PredictionRecord]:
    path = root / "artifacts" / "walk_forward" / tag / "predictions.jsonl"
    if not path.exists():
        raise EnsembleError(
            f"{path} not found; run `python -m src.evaluation.walk_forward` first for this "
            "data/feature/split version"
        )
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    return [PredictionRecord.from_json(line) for line in lines if line.strip()]


def _common_fixtures(predictions: list[PredictionRecord], model_ids: list[str]) -> list[str]:
    by_model: dict[str, set[str]] = defaultdict(set)
    for p in predictions:
        by_model[p.model_id].add(p.fixture_id)
    common = set.intersection(*(by_model[m] for m in model_ids)) if model_ids else set()
    if not common:
        raise EnsembleError(f"no fixture has a prediction from every model in {model_ids}")
    return sorted(common)


def _probs_matrix(
    predictions: list[PredictionRecord], model_id: str, fixture_order: list[str]
) -> np.ndarray:
    by_fixture = {p.fixture_id: p for p in predictions if p.model_id == model_id}
    rows = [(by_fixture[f].p_home, by_fixture[f].p_draw, by_fixture[f].p_away) for f in fixture_order]
    return np.array(rows)


@dataclass(frozen=True)
class EnsembleVariantResult:
    name: str
    meta_version: str
    base_model_ids: list[str]
    fit_rows: int
    calibration_fit_rows: int
    report_rows: int
    raw_metrics: dict[str, float]
    calibrated_metrics: dict[str, float] | None
    skipped: bool = False
    reason: str | None = None
    by_league: dict[str, dict] | None = None
    by_season: dict[str, dict] | None = None


def _split_three(n: int) -> tuple[slice, slice, slice]:
    a = n // 2
    b = a + (n - a) // 2
    return slice(0, a), slice(a, b), slice(b, n)


def _grouped_metrics(probs, y, labels, metric_names, bins) -> dict[str, dict]:
    out = {}
    labels = np.asarray(labels)
    for lab in sorted(set(labels)):
        m = labels == lab
        out[str(lab)] = {"n": int(m.sum()), **compute_metrics(metric_names, probs[m], y[m], bins)}
    return out


def _evaluate_variant(
    name: str,
    base_model_ids: list[str],
    probs_by_model: dict[str, np.ndarray],
    y: np.ndarray,
    metric_names: list[str],
    bins: int,
    fit_fn,
    leagues: np.ndarray | None = None,
    seasons: np.ndarray | None = None,
) -> EnsembleVariantResult:
    """`fit_fn(probs_fit: dict[str, np.ndarray], y_fit) -> combine_fn(probs: dict) -> np.ndarray`
    -- a closure so `simple_mean`/`weighted_average` (no real "fit") and `logistic`/`lightgbm`
    (genuinely fit on the first third) share one evaluation path."""
    n = len(y)
    s_fit, s_calib, s_report = _split_three(n)
    sizes = (s_fit.stop - s_fit.start, s_calib.stop - s_calib.start, s_report.stop - s_report.start)
    if min(sizes) < MIN_SPLIT_ROWS:
        return EnsembleVariantResult(
            name, ENSEMBLE_VERSION, base_model_ids, 0, 0, 0, {}, None,
            skipped=True, reason=f"only {n} common OOF rows, need >= {3 * MIN_SPLIT_ROWS}",
        )  # fmt: skip

    def sl(probs_by_model, s):
        return {m: p[s] for m, p in probs_by_model.items()}

    combine = fit_fn(sl(probs_by_model, s_fit), y[s_fit])

    calib_probs = combine(sl(probs_by_model, s_calib))
    t = fit_temperature(calib_probs, y[s_calib])

    report_probs = combine(sl(probs_by_model, s_report))
    report_y = y[s_report]
    raw = compute_metrics(metric_names, report_probs, report_y, bins)
    calibrated = compute_metrics(metric_names, apply_temperature(report_probs, t), report_y, bins)
    by_league = None
    if leagues is not None:
        by_league = _grouped_metrics(report_probs, report_y, leagues[s_report], metric_names, bins)
    by_season = None
    if seasons is not None:
        by_season = _grouped_metrics(report_probs, report_y, seasons[s_report], metric_names, bins)
    return EnsembleVariantResult(
        name, ENSEMBLE_VERSION, base_model_ids,
        s_fit.stop - s_fit.start, s_calib.stop - s_calib.start, s_report.stop - s_report.start,
        raw, calibrated, by_league=by_league, by_season=by_season,
    )  # fmt: skip


def run_ensemble_variants(
    probs_by_model: dict[str, np.ndarray],
    y: np.ndarray,
    metric_names: list[str],
    bins: int,
    leagues: np.ndarray | None = None,
    seasons: np.ndarray | None = None,
) -> list[EnsembleVariantResult]:
    model_ids = sorted(probs_by_model)

    def mean_fit(probs_fit, y_fit):
        return lambda probs: simple_mean([probs[m] for m in model_ids])

    def weighted_fit(probs_fit, y_fit):
        w = fit_validation_weights([probs_fit[m] for m in model_ids], y_fit)
        return lambda probs: weighted_average([probs[m] for m in model_ids], w)

    def logistic_fit(probs_fit, y_fit):
        X = build_meta_features([probs_fit[m] for m in model_ids])
        model = fit_logistic_stacking(X, y_fit)
        return lambda probs: predict_logistic_stacking(
            model, build_meta_features([probs[m] for m in model_ids])
        )

    def lgbm_fit(probs_fit, y_fit):
        X = build_meta_features([probs_fit[m] for m in model_ids])
        booster = fit_lightgbm_stacking(X, y_fit, num_leaves=7, min_data_in_leaf=max(5, len(y_fit) // 20))
        return lambda probs: predict_lightgbm_stacking(
            booster, build_meta_features([probs[m] for m in model_ids])
        )

    variants = [
        ("simple_mean", mean_fit),
        ("validation_weighted", weighted_fit),
        ("logistic_stacking", logistic_fit),
        ("lightgbm_stacking", lgbm_fit),
    ]
    return [
        _evaluate_variant(name, model_ids, probs_by_model, y, metric_names, bins, fit_fn, leagues, seasons)
        for name, fit_fn in variants
    ]


def render_ensemble_md(variants: list[EnsembleVariantResult], metric_names: list[str]) -> str:
    lines = [
        "## Ensemble (S11, ADR 0022)",
        "",
        "No single winner declared here either -- raw AND calibrated metrics for every variant, "
        "on the SAME held-out report rows.",
        "",
        "| variant | base models | fit | calib-fit | report | " + " | ".join(f"raw {m}" for m in metric_names)
        + " | " + " | ".join(f"cal {m}" for m in metric_names) + " |",
        "|---|---|---|---|---|" + "---|" * (2 * len(metric_names)),
    ]  # fmt: skip
    for v in variants:
        if v.skipped:
            lines.append(f"| {v.name} | - | - | - | - | SKIPPED: {v.reason} |")
            continue
        raw = " | ".join(f"{v.raw_metrics.get(m, float('nan')):.4f}" for m in metric_names)
        cal = " | ".join(f"{v.calibrated_metrics.get(m, float('nan')):.4f}" for m in metric_names)
        bases = ",".join(v.base_model_ids)
        row = f"| {v.name} | {bases} | {v.fit_rows} | {v.calibration_fit_rows} | {v.report_rows} "
        lines.append(row + f"| {raw} | {cal} |")
    return "\n".join(lines) + "\n"


def run_ensemble(root: Path = ROOT, mode: RunMode | str = RunMode.RESEARCH) -> Path:
    mode = RunMode(mode)
    cdir = config_dir_for(root)
    data_cfg = load_config("data", cdir)
    model_cfg = load_config("model", cdir)
    eval_cfg = load_config("evaluation", cdir)

    ref = resolve_dataset(root / data_cfg.processed_dir)
    feats = load_features(root, ref, model_cfg.feature_version)
    split = build_split_manifest(eval_cfg, ref)
    wf_tag = f"{ref.data_version}_{model_cfg.feature_version}_{split['split_id']}_walkforward"
    predictions = _load_walk_forward_predictions(root, wf_tag)

    base_models = model_cfg.walk_forward_models or model_cfg.models
    fixtures = _common_fixtures(predictions, base_models)

    # outcome/league/season lookup: walk-forward spans train+validation seasons only (its own
    # EvaluationContext already blocks final-test at the source -- reloading here inherits that).
    ctx = make_context(EvalMode.VALIDATION, eval_cfg)
    all_rows = load_rows(ref, ctx, list(eval_cfg.train_seasons) + list(eval_cfg.validation_seasons), feats)
    row_by_fixture = {r.fixture_id: r for r in all_rows}
    fixtures = [f for f in fixtures if f in row_by_fixture]  # defensive: predictions.jsonl could
    fixtures.sort(key=lambda f: row_by_fixture[f].kickoff_utc)  # outlive the loaded season set

    probs_by_model = {m: _probs_matrix(predictions, m, fixtures) for m in base_models}
    y = np.array([row_by_fixture[f].outcome for f in fixtures])
    leagues = np.array([row_by_fixture[f].league_id for f in fixtures])
    seasons = np.array([row_by_fixture[f].season for f in fixtures])

    variants = run_ensemble_variants(
        probs_by_model, y, list(eval_cfg.metrics), eval_cfg.calibration_bins, leagues, seasons
    )

    tag = f"{ref.data_version}_{model_cfg.feature_version}_{split['split_id']}_ensemble"
    out_dir = root / "artifacts" / "ensemble" / tag
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "data_version": ref.data_version,
        "feature_version": model_cfg.feature_version,
        "split_id": split["split_id"],
        "base_models": base_models,
        "n_common_oof_fixtures": len(fixtures),
        "variants": [asdict(v) for v in variants],
    }
    report_json = json.dumps(report, indent=2, sort_keys=True, default=str)
    (out_dir / "report.json").write_text(report_json, encoding="utf-8")
    md = render_ensemble_md(variants, list(eval_cfg.metrics))
    (out_dir / "report.md").write_text(md, encoding="utf-8")
    hashes = {"report_json": hashlib.sha256(report_json.encode()).hexdigest()}
    (out_dir / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")
    return out_dir


def main(argv: list[str] | None = None) -> int:
    configure_output()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--mode", default="research", choices=[m.value for m in RunMode if m != RunMode.FINAL])
    a = p.parse_args(argv)
    try:
        out_dir = run_ensemble(Path(a.root), a.mode)
    except (EnsembleError, RuntimeError, ValueError, KeyError) as e:
        print(f"ENSEMBLE RUN FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    print((out_dir / "report.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
