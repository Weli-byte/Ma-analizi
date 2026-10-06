"""Evaluate REAL LLM predictions with the same metrics/calibration infrastructure as every other
model (S9, ADR 0020), under model class `LLM_REAL` (ADR 0026).

- One result per provider/model (`PredictionRecord.model_id`); calibration is fit per model, on a
  chronological first half, and reported on the disjoint second half. `raw_probs` and calibrated
  probabilities are never mixed: both metric sets are reported side by side.
- Only predictions with complete provenance are evaluated (`audit_provenance`); a prediction whose
  fixture has no known outcome yet is counted as `awaiting_result`, not scored.
- Final-test seasons are never reachable from here: rows come from an `EvaluationContext`
  (the benchmark loads validation/train seasons only).
"""

from collections import defaultdict
from dataclasses import asdict

import numpy as np

from src.evaluation.dataset import EvalRow
from src.evaluation.leaderboard import build_leaderboard, render_leaderboard_md
from src.evaluation.metrics import bootstrap_ci, compute_metrics
from src.evaluation.run_baselines import _calibration_for_model
from src.evaluation.runner import ModelResult
from src.schemas import PredictionRecord

MODEL_CLASS_LLM_REAL = "LLM_REAL"
REQUIRED_PROVENANCE = (
    "fixture_id", "kickoff_utc", "information_cutoff", "generated_at", "model_id", "model_version",
    "feature_version", "data_version", "p_home", "p_draw", "p_away",
)  # fmt: skip


def audit_provenance(pred: PredictionRecord) -> list[str]:
    """Names of provenance fields that are missing/empty (phase 42). Empty list = complete."""
    return [f for f in REQUIRED_PROVENANCE if getattr(pred, f, None) in (None, "")]


def _group(rows, probs, y, key: str, metric_names: list[str], bins: int) -> dict[str, dict]:
    labels = np.array([getattr(r, key) for r in rows])
    return {
        str(lab): {
            "n": int((labels == lab).sum()),
            **compute_metrics(metric_names, probs[labels == lab], y[labels == lab], bins),
        }
        for lab in sorted(set(labels))
    }


def evaluate_llm_predictions(
    predictions: list[PredictionRecord],
    rows_by_fixture: dict[str, EvalRow],
    metric_names: list[str],
    bins: int = 10,
    bootstrap_samples: int = 0,
    bootstrap_seed: int = 0,
) -> dict:
    by_model: dict[str, list[PredictionRecord]] = defaultdict(list)
    rejected = 0
    for p in predictions:
        if audit_provenance(p):
            rejected += 1  # not evaluated without complete provenance
            continue
        by_model[p.model_id].append(p)

    results: list[ModelResult] = []
    calibration: dict[str, dict] = {}
    awaiting: dict[str, int] = {}
    for model_id in sorted(by_model):
        scored = [(p, rows_by_fixture[p.fixture_id]) for p in by_model[model_id]
                  if p.fixture_id in rows_by_fixture]  # fmt: skip
        awaiting[model_id] = len(by_model[model_id]) - len(scored)
        if not scored:
            continue
        scored.sort(key=lambda pr: pr[1].kickoff_utc)  # chronological: calibration halves are ordered
        probs = np.array([[p.p_home, p.p_draw, p.p_away] for p, _ in scored])
        y = np.array([r.outcome for _, r in scored])
        rows = [r for _, r in scored]
        ci = bootstrap_ci(metric_names, probs, y, bootstrap_samples, bootstrap_seed, bins) if (
            bootstrap_samples > 0 and len(y) >= 2) else {}  # fmt: skip

        results.append(
            ModelResult(
                model_id,
                scored[0][0].model_version,
                MODEL_CLASS_LLM_REAL,
                {"n": len(y), **compute_metrics(metric_names, probs, y, bins)},
                ci,
                _group(rows, probs, y, "league_id", metric_names, bins),
                _group(rows, probs, y, "season", metric_names, bins),
                0,
                {},
                {"raw_probs_only": True},
            )  # fmt: skip
        )
        calibration[model_id] = _calibration_for_model(probs, y, metric_names, bins)

    leaderboard = build_leaderboard(results)
    return {
        "model_class": MODEL_CLASS_LLM_REAL,
        "results": [asdict(r) for r in results],
        "calibration": calibration,
        "awaiting_result": awaiting,
        "rejected_incomplete_provenance": rejected,
        "leaderboard": {k: [asdict(r) for r in v] for k, v in leaderboard.items()},
        "leaderboard_md": render_leaderboard_md(leaderboard, metric_names),
    }
