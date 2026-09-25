"""Common evaluation runner: every model, same chronological fixtures, same metrics."""

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from .dataset import EvalRow
from .metrics import all_metrics, validate


class Model(Protocol):
    model_id: str
    model_version: str
    diagnostics: dict

    def fit(self, train: list[EvalRow]): ...
    def predict_proba(self, rows: list[EvalRow]) -> np.ndarray: ...


@dataclass
class ModelResult:
    model_id: str
    model_version: str
    metrics: dict[str, float]
    by_league: dict[str, dict]
    by_season: dict[str, dict]
    unpredictable_rows: int
    diagnostics: dict = field(default_factory=dict)


@dataclass
class EvalReport:
    n_eval_rows: int
    n_common_rows: int
    train_range: tuple[str, str]
    eval_range: tuple[str, str]
    results: list[ModelResult]


def assert_chronological(train: list[EvalRow], test: list[EvalRow]) -> None:
    """Training data must end strictly before the evaluation window starts (no random splits)."""
    if not train or not test:
        raise ValueError("train and evaluation sets must be non-empty")
    if max(r.kickoff_utc for r in train) >= min(r.kickoff_utc for r in test):
        raise ValueError("train period overlaps evaluation period; splits must be chronological")


def _group(rows: list[EvalRow], probs: np.ndarray, y: np.ndarray, key: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    labels = np.array([getattr(r, key) for r in rows])
    for lab in sorted(set(labels)):
        m = labels == lab
        out[str(lab)] = {"n": int(m.sum()), **all_metrics(probs[m], y[m])}
    return out


def evaluate(models: list[Model], train: list[EvalRow], test: list[EvalRow]) -> EvalReport:
    assert_chronological(train, test)
    preds: dict[str, np.ndarray] = {}
    for model in models:
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
    results = []
    for model in models:
        p = preds[model.model_id][idx]
        results.append(
            ModelResult(
                model.model_id,
                model.model_version,
                {"n": len(y), **all_metrics(p, y)},
                _group(rows, p, y, "league_id"),
                _group(rows, p, y, "season"),
                int((~np.isfinite(preds[model.model_id]).all(axis=1)).sum()),
                dict(model.diagnostics),
            )
        )
    seasons = lambda rs: (min(r.season for r in rs), max(r.season for r in rs))  # noqa: E731
    return EvalReport(len(test), int(common.sum()), seasons(train), seasons(test), results)
