"""Model / feature / data version naming standard (see docs/versioning.md)."""

import hashlib
import json
import re
from typing import Any

MODEL_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")  # e.g. elo, dixon_coles, llm_claude
MODEL_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")  # semver, e.g. 1.0.0
FEATURE_VERSION_RE = re.compile(r"^fv\d+$")  # e.g. fv1
DATA_VERSION_RE = re.compile(r"^dv\d+$")  # e.g. dv1


def check(pattern: re.Pattern[str], value: str, label: str) -> str:
    if not pattern.match(value):
        raise ValueError(f"invalid {label} {value!r}; expected /{pattern.pattern}/")
    return value


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def config_hash(config: dict[str, Any]) -> str:
    """Stable sha256 of a config dict (key order independent)."""
    return hashlib.sha256(canonical_json(config).encode()).hexdigest()
