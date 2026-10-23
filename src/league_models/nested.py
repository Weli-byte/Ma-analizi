"""Sprint 4c (ADR 0071): nested walk-forward model selection.

Sprint 3 and 4 chose every hyper-parameter on ONE inner season (the last training season). Here a choice is scored on several inner folds: each of the last `k` training seasons is
predicted from the seasons strictly before it (expanding window, with the result-lag embargo), and the candidate with the lowest mean inner log loss of that target wins (ties go to the earlier
grid entry, so the choice is deterministic). The outer eval season of the fold is never seen: `train_rows` is all the selector receives.

A choice is made PER TARGET (1X2, over/under, BTTS): the strength penalty that is best for the result is not necessarily the best for the goal totals. Every candidate and every inner fold is recorded, including
the ones that did not train: nothing is dropped silently."""

from dataclasses import dataclass
from datetime import timedelta

import numpy as np

from src.evaluation.dataset import EvalRow

from . import bench_metrics as bm
from .ml_models import (  # noqa: F401 - class_weights re-exported for callers that want the same weighting
    GBM,
    NotTrainedML,
    class_weights,
)
from .splits import season_start_year
from .stat_models import DCSpec, MatrixPrediction, NotTrained, RidgePoissonDC

TARGETS = ("1x2", "over_under", "btts")


@dataclass(frozen=True)
class InnerFold:
    season: str
    fit: tuple[EvalRow, ...]
    val: tuple[EvalRow, ...]
    kind: str = "SEASON"


def inner_folds(train_rows: list[EvalRow], lag_hours: int, k: int, min_fit: int, within_share: float = 0.7) -> tuple[list[InnerFold], list[dict]]:
    """Up to `k` inner folds from `train_rows` (the last k seasons, oldest first). The second return value lists the dropped seasons and why."""
    by_season: dict[str, list[EvalRow]] = {}
    for r in train_rows:
        by_season.setdefault(r.season, []).append(r)
    ordered = sorted(by_season, key=season_start_year)
    lag = timedelta(hours=lag_hours)
    folds, dropped = [], []
    for s in ordered[-k:]:
        val = sorted(by_season[s], key=lambda r: (r.kickoff_utc, r.fixture_id))
        start = min(r.kickoff_utc for r in val)
        earlier = [r for r in train_rows if season_start_year(r.season) < season_start_year(s) and r.kickoff_utc + lag <= start]
        if len(earlier) < min_fit:
            dropped.append({"season": s, "reason": f"{len(earlier)} fit matches < {min_fit}"})
            continue
        folds.append(InnerFold(s, tuple(sorted(earlier, key=lambda r: (r.kickoff_utc, r.fixture_id))), tuple(val)))
    if not folds and len(ordered) == 1:  # only one training season (the first outer fold): cut it in time instead, earliest share fits, the rest validates, fit rows still unpublished are purged
        rows = sorted(train_rows, key=lambda r: (r.kickoff_utc, r.fixture_id))
        cut = rows[min(int(within_share * len(rows)), len(rows) - 1)].kickoff_utc
        fit = [r for r in rows if r.kickoff_utc + lag <= cut]
        val = [r for r in rows if r.kickoff_utc >= cut]
        if len(fit) >= min_fit and val:
            folds.append(InnerFold(ordered[0], tuple(fit), tuple(val), "WITHIN_SEASON_CHRONOLOGICAL_PURGED"))
    return folds, dropped


def outcomes(rows: list[EvalRow], line: float) -> dict[str, np.ndarray]:
    hg = np.array([r.home_goals for r in rows])
    ag = np.array([r.away_goals for r in rows])
    return {"1x2": np.where(hg > ag, 0, np.where(hg == ag, 1, 2)), "over_under": ((hg + ag) > line).astype(int), "btts": ((hg > 0) & (ag > 0)).astype(int)}


def matrix_losses(pred: MatrixPrediction, rows: list[EvalRow], line: float) -> dict[str, float]:
    y = outcomes(rows, line)
    p1 = np.clip(pred.one_x_two(), 1e-12, 1.0)
    p1 = p1 / p1.sum(axis=1, keepdims=True)
    return {"1x2": float(bm.rows_log_loss_1x2(p1, y["1x2"]).mean()),
            "over_under": float(bm.binary_log_loss_rows(np.clip(pred.over(line), 1e-6, 1 - 1e-6), y["over_under"]).mean()),
            "btts": float(bm.binary_log_loss_rows(np.clip(pred.btts(), 1e-6, 1 - 1e-6), y["btts"]).mean())}  # fmt: skip


def select_dc(grid: list[tuple[str, DCSpec]], folds: list[InnerFold], line: float) -> dict:
    """Pick, for EACH target, the DC spec with the lowest mean inner log loss of that target (ties go to the earlier grid entry). A target whose every candidate failed has no choice.
    Returns {status, chosen: {target: name | None}, chosen_mean_loss: {target: x}, table}."""
    table = []
    for name, spec in grid:
        per_fold, failures = [], []
        for f in folds:
            try:
                mod = RidgePoissonDC(spec, f"inner_{name}").fit(list(f.fit))
                ls = matrix_losses(mod.predict(list(f.val)), list(f.val), line)
                per_fold.append({"season": f.season, "fit_matches": len(f.fit), "val_matches": len(f.val), **{k: round(v, 6) for k, v in ls.items()}})
            except NotTrained as e:
                failures.append({"season": f.season, "reason": str(e)[:120]})
        ok = bool(per_fold) and not failures
        table.append({"candidate": name, "mean_loss": {t: round(float(np.mean([q[t] for q in per_fold])), 6) for t in TARGETS} if ok else None, "inner_folds": per_fold, "failures": failures})
    names = [n for n, _ in grid]
    chosen, mean_loss = {}, {}
    for t in TARGETS:
        cand = [x for x in table if x["mean_loss"] is not None]
        if not cand:
            chosen[t], mean_loss[t] = None, None
            continue
        best = min(cand, key=lambda x: (x["mean_loss"][t], names.index(x["candidate"])))
        chosen[t], mean_loss[t] = best["candidate"], best["mean_loss"][t]
    return {"status": "SELECTED" if any(chosen.values()) else "NO_CANDIDATE_TRAINED", "chosen": chosen, "chosen_mean_loss": mean_loss, "table": table}


def dc_grid(raw_grid: dict, base: DCSpec, **fixed) -> list[tuple[str, DCSpec]]:
    """Cartesian grid over l2_team / l2_country / half_life_days (whichever are given), in a fixed order."""
    import dataclasses
    import itertools

    keys = [k for k in ("l2_team", "l2_country", "half_life_days") if k in raw_grid]
    out = []
    for combo in itertools.product(*(raw_grid[k] for k in keys)):
        kw = dict(zip(keys, combo, strict=True))
        name = "|".join(f"{k}={'none' if v is None else v}" for k, v in kw.items())
        out.append((name, dataclasses.replace(base, **kw, **fixed)))
    return out


# ------------------------------------------------------------------------------------------------ boosted trees
def tune_nested(raw_ml: dict, target: str, sets: list[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]], names: list[str], categorical: list[str], seed: int,
                max_trials: int, n_startup: int) -> dict:
    """Optuna TPE over the Sprint 4 search space; a trial's score is the mean validation log loss over the inner folds in `sets` = [(Xfit, yfit, Xval, yval), ...]. Returns the best
    parameters, the mean early-stopped round count and every trial. Deterministic: seeded sampler, categorical grid, rounded scores, ties by trial number."""
    import optuna

    from .ml_models import log_loss

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    t, g = raw_ml["tuning"], raw_ml["gbm"]
    space = t["space"]
    digits = int(t["objective_rounding_digits"])
    records = []

    def objective(trial) -> float:
        params = {**g["defaults"], **{k: trial.suggest_categorical(k, v) for k, v in space.items()}}
        losses, iters, state = [], [], "COMPLETE"
        for Xf, yf, Xv, yv in sets:
            m = GBM("lightgbm", target, params, names, categorical, seed, g["threads"], g["class_weight"])
            try:
                m.fit(Xf, yf, t["max_rounds"], valid=(Xv, yv), early_stopping=t["early_stopping_rounds"])
                losses.append(log_loss(target, m.predict_proba(Xv), yv))
                iters.append(m.best_iteration)
            except NotTrainedML as e:
                state = f"FAILED: {e}"
                break
        value = round(float(np.mean(losses)), digits) if state == "COMPLETE" and losses else float("inf")
        records.append({"trial": trial.number, "params": {k: params[k] for k in space}, "mean_validation_log_loss": value, "best_iteration": int(round(float(np.mean(iters)))) if iters else 0, "inner_folds": len(sets), "state": state})
        return value if np.isfinite(value) else 1e9

    study = optuna.create_study(direction="minimize", sampler=optuna.samplers.TPESampler(seed=seed, n_startup_trials=n_startup))
    study.optimize(objective, n_trials=max_trials, show_progress_bar=False)
    complete = [r for r in records if r["state"] == "COMPLETE"]
    if not complete:
        raise NotTrainedML(f"{target}: no nested tuning trial trained")
    best = min(complete, key=lambda r: (r["mean_validation_log_loss"], r["trial"]))
    return {"target": target, "best_params": best["params"], "best_trial": best["trial"], "best_iteration": max(10, best["best_iteration"]), "best_mean_validation_log_loss": best["mean_validation_log_loss"],
            "trials_run": len(records), "inner_folds": len(sets), "sampler": f"TPE seed {seed}", "trials": records}  # fmt: skip
