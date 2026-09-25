"""Leakage test harness.

Pick a random fixture + random cutoff, then scramble EVERYTHING that is not available at that
cutoff (results after the cutoff, and the fixture's own result). Features must not change.
Any difference proves the feature function peeked at the future.
"""

import random
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from .compute import FeatureResult, compute_features
from .history import RESULT_LAG, MatchHistory, MatchRecord

FeatureFn = Callable[[MatchRecord, MatchHistory, datetime], FeatureResult]


@dataclass(frozen=True)
class Violation:
    fixture_id: str
    cutoff: datetime
    features: list[str]


def _scramble(m: MatchRecord, rng: random.Random, allow_drop: bool) -> MatchRecord | None:
    if allow_drop and rng.random() < 0.25:
        return None  # record vanishes
    return replace(
        m,
        home_goals=m.home_goals + rng.randint(1, 4),
        away_goals=max(0, m.away_goals + rng.randint(-2, 3)),
        home_xg=None if m.home_xg is None else m.home_xg + rng.random() * 3,
        away_xg=None if m.away_xg is None else m.away_xg + rng.random() * 3,
    )


def _diff(a: FeatureResult, b: FeatureResult) -> list[str]:
    bad = [k for k in a.values if a.values[k] != b.values.get(k)]
    bad += [k for k in a.available_at if a.available_at[k] != b.available_at.get(k)]
    return sorted(set(bad))


def audit_leakage(
    matches: list[MatchRecord],
    n_samples: int = 200,
    seed: int = 0,
    feature_fn: FeatureFn = compute_features,
    max_lookback_days: int = 30,
) -> list[Violation]:
    """Return violations (empty list = clean)."""
    rng = random.Random(seed)
    ordered = sorted(matches, key=lambda m: (m.kickoff_utc, m.fixture_id))
    base_history = MatchHistory(ordered)
    violations: list[Violation] = []
    for _ in range(n_samples):
        fx = rng.choice(ordered)
        cutoff = fx.kickoff_utc - timedelta(seconds=rng.randint(0, max_lookback_days * 86400))
        base = feature_fn(fx, base_history, cutoff)

        perturbed: list[MatchRecord] = []
        for m in ordered:
            future = m.fixture_id == fx.fixture_id or m.kickoff_utc + RESULT_LAG > cutoff
            if not future:
                perturbed.append(m)
                continue
            changed = _scramble(m, rng, allow_drop=m.fixture_id != fx.fixture_id)
            if changed is not None:
                perturbed.append(changed)
        # the fixture being predicted always exists (only its result is scrambled)
        fx_changed = next(m for m in perturbed if m.fixture_id == fx.fixture_id)
        alt = feature_fn(fx_changed, MatchHistory(perturbed), cutoff)
        bad = _diff(base, alt)
        if bad:
            violations.append(Violation(fx.fixture_id, cutoff, bad))
    return violations
