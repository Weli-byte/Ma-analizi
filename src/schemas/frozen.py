"""Deep immutability helpers. `frozen=True` alone only blocks attribute assignment; nested
dicts/lists stay mutable. These types freeze recursively (dict -> read-only mapping, list -> tuple)."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Any

from pydantic import AfterValidator, PlainSerializer


def deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({k: deep_freeze(v) for k, v in value.items()})
    if isinstance(value, list | tuple):
        return tuple(deep_freeze(v) for v in value)
    if isinstance(value, set | frozenset):
        return frozenset(deep_freeze(v) for v in value)
    return value


def thaw(value: Any) -> Any:
    """Inverse of deep_freeze into plain JSON-able containers."""
    if isinstance(value, Mapping):
        return {k: thaw(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [thaw(v) for v in value]
    return value


FrozenMap = Annotated[
    Mapping[str, Any],
    AfterValidator(deep_freeze),
    PlainSerializer(thaw, return_type=dict),
]
FrozenFloatMap = Annotated[
    Mapping[str, float | None],
    AfterValidator(deep_freeze),
    PlainSerializer(thaw, return_type=dict),
]
FrozenStrMap = Annotated[
    Mapping[str, str],
    AfterValidator(deep_freeze),
    PlainSerializer(thaw, return_type=dict),
]
