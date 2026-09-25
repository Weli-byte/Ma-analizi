"""Feature engine. `builder` is intentionally NOT imported here so that
`python -m src.features.builder` runs without runpy import warnings."""

from .compute import FeatureResult, compute_features
from .history import MatchHistory, MatchRecord
from .leakage_audit import audit_leakage
from .registry import FEATURE_VERSION, REGISTRY, registry_hash, spec_for

__all__ = [
    "FEATURE_VERSION",
    "REGISTRY",
    "FeatureResult",
    "MatchHistory",
    "MatchRecord",
    "audit_leakage",
    "compute_features",
    "registry_hash",
    "spec_for",
]
