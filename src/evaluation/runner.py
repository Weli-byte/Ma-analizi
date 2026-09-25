"""Common evaluation runner: every model, same chronological fixtures, same metrics."""

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from src.features.availability import build_report, enforce
from src.runmode import RunMode

from .dataset import EvalRow
from .metrics import bootstrap_ci, compute_metrics, validate


class Model(Protocol):
    model_id: str
    model_version: str
    model_class: str
    required_features: tuple[str, ...]
    diagnostics: dict

    def fit(self, train: list[EvalRow]): ...
    def predict_proba(self, rows: list[EvalRow]) -> np.ndarray: ...


@dataclass(frozen=True)
class EvalSettings:
    run_mode: RunMode
    metrics: list[str]
    bins: int
    bootstrap_samples: int
    bootstrap_seed: int
    max_fallback_rate: float


@dataclass
class ModelResult:
    model_id: str
    model_version: str
    model_class: str
    metrics: dict[str, float]
    confidence_intervals: dict[str, dict]
    by_league: dict[str, dict]
    by_season: dict[str, dict]
    unpredictable_rows: int
    availability: dict
    diagnostics: dict = field(default_factory=dict)


@dataclass
class EvalReport:
    n_eval_rows: int
    n_common_rows: int
    train_range: tuple[str, str]
    eval_range: tuple[str, str]
    results: list[ModelResult]
    # not serialized into report.json: needed to emit predictions
    common_rows: list[EvalRow] = field(default_factory=list, repr=False, compare=False)
    probs: dict[str, np.ndarray] = field(default_factory=dict, repr=False, compare=False)


def assert_chronological(train: list[EvalRow], test: list[EvalRow]) -> None:
    """Training data must end strictly before the evaluation window starts (no random splits)."""
    if not train or not test:
        raise ValueError("train and evaluation sets must be non-empty")
    if max(r.kickoff_utc for r in train) >= min(r.kickoff_utc for r in test):
        raise ValueError("train period overlaps evaluation period; splits must be chronological")


def _group(
    rows: list[EvalRow], probs: np.ndarray, y: np.ndarray, key: str, s: EvalSettings
) -> dict[str, dict]:
    out: dict[str, dict] = {}
    labels = np.array([getattr(r, key) for r in rows])
    for lab in sorted(set(labels)):
        m = labels == lab
        out[str(lab)] = {"n": int(m.sum()), **compute_metrics(s.metrics, probs[m], y[m], s.bins)}
    return out


def evaluate(
    models: list[Model], train: list[EvalRow], test: list[EvalRow], settings: EvalSettings
) -> EvalReport:
    assert_chronological(train, test)
    preds: dict[str, np.ndarray] = {}
    avail: dict[str, dict] = {}
    for model in models:
        if model.required_features:  # no silent fallbacks: measure, report, enforce
            rep = build_report(test, list(model.required_features))
            enforce(rep, settings.run_mode, settings.max_fallback_rate, model.model_id)
            avail[model.model_id] = rep.as_dict()
        else:
            avail[model.model_id] = {}
        model.fit(train)
        p = np.asarray(model.predict_proba(test), dtype=float)
        if p.shape != (len(test), 3):
            raise ValueError(f"{model.model_id}: expected {(len(test), 3)}, got {p.shape}")
        finite = np.isfinite(p).all(axis=1)
        if finite.any():
            validate(p[finite])  # invalid probabilities are an error, NaN rows are "abstain"
        preds[model.model_id] = p

    common = np.ones(len(test), dtype=bool)
    for p in preds.values():
        common &= np.isfinite(p).all(axis=1)
    if not common.any():
        raise ValueError("no fixture is predictable by all models")

    idx = np.flatnonzero(common)
    rows = [test[i] for i in idx]
    y = np.array([r.outcome for r in rows])
    results, probs = [], {}
    for model in models:
        p = preds[model.model_id][idx]
        probs[model.model_id] = p
        ci = (
            bootstrap_ci(
                settings.metrics,
                p,
                y,
                settings.bootstrap_samples,
                settings.bootstrap_seed,
                settings.bins,
            )
            if settings.bootstrap_samples > 0
            else {}
        )
        results.append(
            ModelResult(
                model.model_id,
                model.model_version,
                model.model_class,
                {"n": len(y), **compute_metrics(settings.metrics, p, y, settings.bins)},
                ci,
                _group(rows, p, y, "league_id", settings),
                _group(rows, p, y, "season", settings),
                int((~np.isfinite(preds[model.model_id]).all(axis=1)).sum()),
                avail[model.model_id],
                dict(model.diagnostics),
            )
        )
    seasons = lambda rs: (min(r.season for r in rs), max(r.season for r in rs))  # noqa: E731
    return EvalReport(len(test), int(common.sum()), seasons(train), seasons(test), results, rows, probs)
