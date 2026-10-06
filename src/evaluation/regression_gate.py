"""S0-S7 hardening Phase 42 (audit finding M-19): compose the existing piecemeal checks
(probability validity, feature leakage, feature-set disappearance) into one reusable go/no-go
gate, instead of a contributor having to remember to run each one separately.

Explicitly NOT a "new metric must be better than the old one" threshold -- per CLAUDE.md,
scientific/methodological changes are allowed to trade metrics (a worse log loss from a more
honest model is not a regression). This gate only catches STRUCTURAL breakage: invalid
probabilities, leakage, or features silently vanishing. It does not judge model quality.
"""

from dataclasses import dataclass, field

import numpy as np

from src.evaluation import metrics
from src.features.history import MatchRecord
from src.features.leakage_audit import FeatureFn, Violation, audit_leakage


@dataclass(frozen=True)
class RegressionGateResult:
    probability_errors: tuple[str, ...] = ()
    leakage_violations: tuple[Violation, ...] = ()
    missing_features: tuple[str, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        return not (self.probability_errors or self.leakage_violations or self.missing_features)


def run_regression_gate(
    *,
    probs: np.ndarray | None = None,
    outcomes: np.ndarray | None = None,
    leakage_matches: list[MatchRecord] | None = None,
    leakage_samples: int = 50,
    leakage_seed: int = 0,
    leakage_feature_fn: FeatureFn | None = None,
    current_feature_names: list[str] | None = None,
    expected_feature_names: list[str] | None = None,
) -> RegressionGateResult:
    """Each check is independently optional (pass `None` to skip it) so this can run against
    whatever artifacts a given call site already has -- a full walk-forward run can pass all
    three, a lighter CI check can pass just the ones it built.
    """
    prob_errors: list[str] = []
    if probs is not None:
        try:
            metrics.validate(probs, outcomes)
        except ValueError as e:
            prob_errors.append(str(e))

    violations: tuple[Violation, ...] = ()
    if leakage_matches is not None:
        violations = tuple(
            audit_leakage(
                leakage_matches,
                n_samples=leakage_samples,
                seed=leakage_seed,
                feature_fn=leakage_feature_fn,
            )
        )

    missing: tuple[str, ...] = ()
    if expected_feature_names is not None:
        current = set(current_feature_names or [])
        missing = tuple(sorted(set(expected_feature_names) - current))

    return RegressionGateResult(
        probability_errors=tuple(prob_errors),
        leakage_violations=violations,
        missing_features=missing,
    )
