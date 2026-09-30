"""S0-S7 hardening Phase 8 — full vs reduced GBM feature set, compared under walk-forward
(audit finding M-09).

    python scripts/gbm_feature_reduction_comparison.py [--root DIR] [--mode research]

`form_points_3`/`form_points_5`/`form_points_10` are nested rolling windows of the same signal
and, by construction, correlated (`scripts/gbm_feature_audit.py` shows the real correlation
matrix). This script does NOT remove them — it fits XGBoost on the FULL feature set and on a
REDUCED set (form_points_3/form_points_10 excluded, form_points_5 kept) across every
`src.evaluation.split.walk_forward_folds` fold and reports both, per fold, side by side.
Removal is a decision for a human to make from this evidence, never automatic.
"""

import argparse
import sys
from pathlib import Path

import numpy as np

from src.config import config_dir_for, load_config
from src.data.dataset import resolve_dataset
from src.evaluation.context import EvalMode, make_context
from src.evaluation.dataset import load_rows
from src.evaluation.metrics import log_loss
from src.evaluation.split import walk_forward_folds
from src.features.artifact import load_features
from src.models.gbm import XGBModel

ROOT = Path(__file__).resolve().parents[1]
REDUCED_EXCLUDED = frozenset(
    {"home_form_points_3", "away_form_points_3", "home_form_points_10", "away_form_points_10"}
)


def run_comparison(root: Path, mode: str = "research") -> str:
    cdir = config_dir_for(root)
    data_cfg = load_config("data", cdir)
    model_cfg = load_config("model", cdir)
    eval_cfg = load_config("evaluation", cdir)
    ref = resolve_dataset(root / data_cfg.processed_dir)
    feats = load_features(root, ref, model_cfg.feature_version)
    folds = walk_forward_folds(eval_cfg)
    ctx = make_context(EvalMode.VALIDATION, eval_cfg)

    gbm_cfg = model_cfg.gbm
    n_trials = getattr(gbm_cfg.n_optuna_trials, mode, gbm_cfg.n_optuna_trials.research)
    rows_out = []
    for fold in folds:
        train_rows = load_rows(ref, ctx, list(fold.train_seasons), feats)
        test_rows = load_rows(ref, ctx, [fold.test_season], feats)
        y_test = np.array([r.outcome for r in test_rows])

        full = XGBModel(seed=gbm_cfg.seed, n_optuna_trials=n_trials,
                         validation_fraction=gbm_cfg.validation_fraction,
                         early_stopping_rounds=gbm_cfg.early_stopping_rounds).fit(train_rows)  # fmt: skip
        reduced = XGBModel(seed=gbm_cfg.seed, n_optuna_trials=n_trials,
                            validation_fraction=gbm_cfg.validation_fraction,
                            early_stopping_rounds=gbm_cfg.early_stopping_rounds,
                            excluded_features=REDUCED_EXCLUDED).fit(train_rows)  # fmt: skip

        ll_full = log_loss(full.predict_proba(test_rows), y_test)
        ll_reduced = log_loss(reduced.predict_proba(test_rows), y_test)
        rows_out.append((fold.index, fold.test_season, len(test_rows), ll_full, ll_reduced))

    lines = [
        "# GBM full vs reduced feature set (walk-forward), M-09",
        "",
        f"Reduced set excludes: {sorted(REDUCED_EXCLUDED)}",
        "",
        "| fold | test_season | n | log_loss (full) | log_loss (reduced) | delta |",
        "|---|---|---|---|---|---|",
    ]
    for idx, season, n, ll_full, ll_reduced in rows_out:
        lines.append(f"| {idx} | {season} | {n} | {ll_full:.6f} | {ll_reduced:.6f} | "
                      f"{ll_reduced - ll_full:+.6f} |")  # fmt: skip
    lines += [
        "",
        "No feature is removed by this script or its output. A consistently negative delta "
        "across folds is evidence FOR considering removal in a future, separately-reviewed "
        "change (with its own ADR if adopted) -- a single run is not.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--mode", default="research", choices=["development", "research", "strict"])
    a = p.parse_args(argv)
    try:
        print(run_comparison(Path(a.root), a.mode))
    except (RuntimeError, ValueError, KeyError) as e:
        print(f"FEATURE REDUCTION COMPARISON FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
