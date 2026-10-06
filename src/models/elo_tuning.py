"""S0-S7 hardening Phase 7 — Elo hyperparameter tuning via temporal (walk-forward) validation.

    python -m src.models.elo_tuning [--root DIR] [--mode research]

Never tunes on final-test data: the search runs exclusively over the folds
`src.evaluation.split.walk_forward_folds` derives from train+validation seasons — the exact same
folds `src.evaluation.walk_forward` itself uses, read through a VALIDATION-mode
`EvaluationContext` (final-test seasons are structurally unreachable, same mechanism ADR-0016
already relies on). Baseline (config-default) Elo and tuned Elo are evaluated on IDENTICAL folds
so the comparison is apples-to-apples; final-test performance is never touched here at all
(that number only ever comes from `src.evaluation.final`, once).
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from src.cli_utils import configure_output
from src.config import EloConfig, EvaluationConfig, config_dir_for, load_config
from src.data.dataset import resolve_dataset
from src.evaluation.context import EvalMode, make_context
from src.evaluation.dataset import load_rows
from src.evaluation.metrics import log_loss
from src.evaluation.split import walk_forward_folds
from src.features.artifact import load_features

from .elo import EloModel

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class FoldEvalResult:
    fold_index: int
    train_seasons: tuple[str, ...]
    test_season: str
    log_loss: float
    n: int


@dataclass(frozen=True)
class EloTuningReport:
    baseline_params: dict
    tuned_params: dict
    baseline_folds: list[FoldEvalResult]
    tuned_folds: list[FoldEvalResult]
    baseline_mean_log_loss: float
    tuned_mean_log_loss: float
    n_trials_run: int
    selection_period: tuple[str, str]


def _evaluate_params(params: dict, folds, ref, ctx, feats) -> list[FoldEvalResult]:
    results = []
    for fold in folds:
        train_rows = load_rows(ref, ctx, list(fold.train_seasons), feats)
        test_rows = load_rows(ref, ctx, [fold.test_season], feats)
        model = EloModel(**params)
        model.fit(train_rows)
        p = model.predict_proba(test_rows)
        y = np.array([r.outcome for r in test_rows])
        results.append(
            FoldEvalResult(fold.index, fold.train_seasons, fold.test_season, log_loss(p, y), len(test_rows))
        )
    return results


def tune_elo(eval_cfg: EvaluationConfig, base_elo_cfg: EloConfig, ref, feats) -> EloTuningReport:
    tuning_cfg = base_elo_cfg.tuning
    if tuning_cfg.method != "optuna":
        raise ValueError(f"unsupported tuning method {tuning_cfg.method!r}")  # only one exists today
    if tuning_cfg.objective != "log_loss":
        raise ValueError(f"unsupported tuning objective {tuning_cfg.objective!r}")  # Rule 12: never Accuracy
    folds = walk_forward_folds(eval_cfg)
    if not folds:
        raise ValueError("elo tuning requires at least one walk-forward fold")
    ctx = make_context(EvalMode.VALIDATION, eval_cfg)  # final-test seasons unreachable here

    baseline_params = {
        "initial_rating": base_elo_cfg.initial_rating,
        "k_factor": base_elo_cfg.k_factor,
        "home_advantage": base_elo_cfg.home_advantage,
        "decay_half_life_days": base_elo_cfg.decay_half_life_days,
    }
    baseline_folds = _evaluate_params(baseline_params, folds, ref, ctx, feats)

    n_trials_run = 0
    if tuning_cfg.n_trials > 0:
        import optuna

        optuna.logging.set_verbosity(optuna.logging.WARNING)
        study = optuna.create_study(
            direction="minimize", sampler=optuna.samplers.TPESampler(seed=tuning_cfg.seed)
        )

        def objective(trial):
            k = trial.suggest_float("k_factor", *tuning_cfg.k_factor_range)
            h = trial.suggest_float("home_advantage", *tuning_cfg.home_advantage_range)
            decay = None
            if tuning_cfg.decay_half_life_days_range is not None:
                if trial.suggest_categorical("use_decay", [True, False]):
                    decay = trial.suggest_float(
                        "decay_half_life_days", *tuning_cfg.decay_half_life_days_range
                    )
            params = {
                "initial_rating": base_elo_cfg.initial_rating,
                "k_factor": k,
                "home_advantage": h,
                "decay_half_life_days": decay,
            }
            fold_results = _evaluate_params(params, folds, ref, ctx, feats)
            return float(np.mean([r.log_loss for r in fold_results]))

        study.optimize(objective, n_trials=tuning_cfg.n_trials, show_progress_bar=False)
        n_trials_run = len(study.trials)
        best = study.best_params
        tuned_params = {
            "initial_rating": base_elo_cfg.initial_rating,
            "k_factor": best["k_factor"],
            "home_advantage": best["home_advantage"],
            "decay_half_life_days": best.get("decay_half_life_days") if best.get("use_decay") else None,
        }
    else:
        tuned_params = dict(baseline_params)

    tuned_folds = _evaluate_params(tuned_params, folds, ref, ctx, feats)

    return EloTuningReport(
        baseline_params=baseline_params,
        tuned_params=tuned_params,
        baseline_folds=baseline_folds,
        tuned_folds=tuned_folds,
        baseline_mean_log_loss=float(np.mean([r.log_loss for r in baseline_folds])),
        tuned_mean_log_loss=float(np.mean([r.log_loss for r in tuned_folds])),
        n_trials_run=n_trials_run,
        selection_period=(folds[0].train_seasons[0], folds[-1].test_season),
    )


def render_report(rep: EloTuningReport) -> str:
    lines = [
        "# Elo hyperparameter tuning report",
        "",
        f"Selection period (train..test, walk-forward folds only — never final-test): "
        f"{rep.selection_period[0]} .. {rep.selection_period[1]}",
        f"Optuna trials run: {rep.n_trials_run}",
        "",
        f"- Baseline params: {rep.baseline_params}",
        f"- Tuned params: {rep.tuned_params}",
        "",
        "| | mean log loss (walk-forward folds) |",
        "|---|---|",
        f"| Baseline Elo | {rep.baseline_mean_log_loss:.6f} |",
        f"| Tuned Elo | {rep.tuned_mean_log_loss:.6f} |",
        "",
        "## Per-fold (baseline)",
        "",
        "| fold | train_seasons | test_season | n | log_loss |",
        "|---|---|---|---|---|",
    ]
    def _row(r: FoldEvalResult) -> str:
        return f"| {r.fold_index} | {list(r.train_seasons)} | {r.test_season} | {r.n} | {r.log_loss:.6f} |"

    for r in rep.baseline_folds:
        lines.append(_row(r))
    lines += ["", "## Per-fold (tuned)", "",
              "| fold | train_seasons | test_season | n | log_loss |", "|---|---|---|---|---|"]  # fmt: skip
    for r in rep.tuned_folds:
        lines.append(_row(r))
    lines += [
        "",
        "## Notes",
        "- Selected hyperparameters were chosen using ONLY the walk-forward folds above "
        "(train+validation seasons); final-test performance is not, and cannot be, touched here.",
        "- No claim of improvement is made without this identical-fold comparison (per-fold "
        "numbers are reported, not just the mean).",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    configure_output()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--mode", default="research", choices=["development", "research", "strict"])
    a = p.parse_args(argv)
    root = Path(a.root)
    try:
        cdir = config_dir_for(root)
        data_cfg = load_config("data", cdir)
        model_cfg = load_config("model", cdir)
        eval_cfg = load_config("evaluation", cdir)
        if not model_cfg.elo.tuning.enabled:
            print("Elo tuning is disabled (configs/model.yaml: elo.tuning.enabled=false)")
            return 0
        ref = resolve_dataset(root / data_cfg.processed_dir)
        feats = load_features(root, ref, model_cfg.feature_version)
        rep = tune_elo(eval_cfg, model_cfg.elo, ref, feats)
    except (RuntimeError, ValueError, KeyError) as e:
        print(f"ELO TUNING FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    print(render_report(rep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
