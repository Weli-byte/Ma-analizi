"""Evaluation contexts: the technical lock on final-test data (ADR 0004).

Every data loader takes an EvaluationContext. Training/validation/tuning/calibration/ensemble
contexts can never read final-test seasons. A FINAL context can only be created by
`unlock_final()` (src/evaluation/final.py), which requires FINAL run mode. This guards against
accidents; it is not a security boundary against code that deliberately bypasses it.
"""

from dataclasses import dataclass, field
from enum import StrEnum

from src.config import EvaluationConfig


class EvalMode(StrEnum):
    TRAIN = "train"
    VALIDATION = "validation"
    TUNING = "tuning"  # hyperparameter optimisation
    CALIBRATION = "calibration"
    MODEL_SELECTION = "model_selection"
    ENSEMBLE_FIT = "ensemble_fit"
    FINAL = "final"


class FinalTestAccessError(PermissionError):
    """Attempt to read final-test data outside the dedicated final evaluation path."""


_FINAL_TOKEN = object()  # only src.evaluation.final holds a reference


@dataclass(frozen=True)
class EvaluationContext:
    mode: EvalMode
    cfg: EvaluationConfig
    _token: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.mode == EvalMode.FINAL and self._token is not _FINAL_TOKEN:
            raise FinalTestAccessError("FINAL contexts can only be created by unlock_final() in final mode")

    def allowed_seasons(self) -> list[str]:
        c = self.cfg
        if self.mode == EvalMode.TRAIN:
            return list(c.train_seasons)
        if self.mode == EvalMode.FINAL:
            return [*c.train_seasons, *c.validation_seasons, *c.final_test_seasons]
        return [*c.train_seasons, *c.validation_seasons]

    def check_seasons(self, seasons: list[str]) -> None:
        final = set(self.cfg.final_test_seasons)
        touched = sorted(final & set(seasons))
        if touched and self.mode != EvalMode.FINAL:
            raise FinalTestAccessError(f"{self.mode.value} code may not read final-test seasons {touched}")
        beyond = sorted(set(seasons) - set(self.allowed_seasons()))
        if beyond:
            raise FinalTestAccessError(
                f"{self.mode.value} context may only read {self.allowed_seasons()}; asked for "
                f"{beyond} (seasons outside every split, e.g. current partial data, are never read)"
            )


def make_context(mode: EvalMode | str, cfg: EvaluationConfig) -> EvaluationContext:
    """Public constructor for every mode except FINAL."""
    mode = EvalMode(mode)
    if mode == EvalMode.FINAL:
        raise FinalTestAccessError("use unlock_final() to obtain a FINAL context")
    return EvaluationContext(mode, cfg)
