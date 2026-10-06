"""Leakage test harness.

Pick a random fixture + random cutoff, then scramble/delete EVERYTHING that is not available at
that cutoff (results not yet published, the fixture's own result, its post-match stats and market
data). Features must not change. Any difference proves the feature function peeked at the future.
"""

import random
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from types import MappingProxyType

from src.config import FeaturesConfig

from .compute import DEFAULT_CONFIG, FeatureResult, compute_features
from .history import MatchHistory, MatchRecord

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
        home_goals=(m.home_goals or 0) + rng.randint(1, 4),
        away_goals=max(0, (m.away_goals or 0) + rng.randint(-2, 3)),
        extras=MappingProxyType({k: v + rng.random() * 5 + 1 for k, v in m.extras.items()}),
    )


def _diff(a: FeatureResult, b: FeatureResult) -> list[str]:
    bad = [k for k in a.values if a.values[k] != b.values.get(k)]
    bad += [k for k in a.available_at if a.available_at[k] != b.available_at.get(k)]
    bad += [k for k in a.reasons if a.reasons[k] != b.reasons.get(k)]
    return sorted(set(bad))


def audit_leakage(
    matches: list[MatchRecord],
    n_samples: int = 200,
    seed: int = 0,
    feature_fn: FeatureFn | None = None,
    max_lookback_days: int = 30,
    cfg: FeaturesConfig = DEFAULT_CONFIG,
) -> list[Violation]:
    """Return violations (empty list = clean)."""
    fn: FeatureFn = feature_fn or (lambda fx, h, c: compute_features(fx, h, c, cfg))
    rng = random.Random(seed)
    ordered = sorted(matches, key=lambda m: (m.kickoff_utc, m.fixture_id))
    base_history = MatchHistory(ordered)
    violations: list[Violation] = []
    for _ in range(n_samples):
        fx = rng.choice(ordered)
        cutoff = fx.kickoff_utc - timedelta(seconds=rng.randint(0, max_lookback_days * 86400))
        base = fn(fx, base_history, cutoff)

        perturbed: list[MatchRecord] = []
        for m in ordered:
            unavailable = (
                m.fixture_id == fx.fixture_id
                or m.result_available_at_utc is None
                or m.result_available_at_utc > cutoff
            )
            if not unavailable:
                perturbed.append(m)
                continue
            changed = _scramble(m, rng, allow_drop=m.fixture_id != fx.fixture_id)
            if changed is not None:
                perturbed.append(changed)
        # the fixture being predicted always exists (only its result is scrambled)
        fx_changed = next(m for m in perturbed if m.fixture_id == fx.fixture_id)
        alt = fn(fx_changed, MatchHistory(perturbed), cutoff)
        bad = _diff(base, alt)
        if bad:
            violations.append(Violation(fx.fixture_id, cutoff, bad))
    return violations
