"""Shared provider plumbing (ADR 0024): normalized result, error taxonomy, retry with backoff.

No mock/fake provider exists. Every concrete provider calls the REAL vendor API through the
vendor's official SDK. Fields a vendor does not return stay `None` -- never fabricated.
"""

import hashlib
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol


class ErrorKind(StrEnum):
    RATE_LIMIT = "rate_limit"  # 429
    AUTH = "auth"  # 401/403
    INVALID_REQUEST = "invalid_request"  # 400 and other 4xx
    MODEL_UNAVAILABLE = "model_unavailable"  # 404
    TIMEOUT = "timeout"  # 408 / client timeout
    SERVER = "server"  # 5xx
    NETWORK = "network"
    CONTENT_POLICY = "content_policy"  # safety / refusal
    SCHEMA = "schema"  # malformed or invalid output (raised by the runner)
    UNKNOWN = "unknown"


RETRYABLE = frozenset({ErrorKind.RATE_LIMIT, ErrorKind.TIMEOUT, ErrorKind.SERVER, ErrorKind.NETWORK})


def classify_status(code: int | None) -> ErrorKind:
    if code is None:
        return ErrorKind.UNKNOWN
    if code == 429:
        return ErrorKind.RATE_LIMIT
    if code in (401, 403):
        return ErrorKind.AUTH
    if code == 404:
        return ErrorKind.MODEL_UNAVAILABLE
    if code == 408:
        return ErrorKind.TIMEOUT
    if 400 <= code < 500:
        return ErrorKind.INVALID_REQUEST
    if code >= 500:
        return ErrorKind.SERVER
    return ErrorKind.UNKNOWN


_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{6,}|AIza[0-9A-Za-z_\-]{10,}|key=[^&\s]+)")


def scrub(message: str, api_key: str | None = None) -> str:
    """Remove anything key-shaped (and the exact key) before a message can reach logs/records."""
    if api_key:
        message = message.replace(api_key, "[REDACTED]")
    return _SECRET_RE.sub("[REDACTED]", message)[:300]


class ProviderError(RuntimeError):
    def __init__(
        self,
        kind: ErrorKind,
        message: str,
        status_code: int | None = None,
        request_id: str | None = None,
        retry_count: int = 0,
    ):
        super().__init__(f"{kind.value}: {message}")
        self.kind, self.status_code = kind, status_code
        self.request_id, self.retry_count = request_id, retry_count


@dataclass(frozen=True)
class LLMResponse:
    """Normalized REAL provider response. `None` = the provider did not report it."""

    provider: str
    model: str  # as reported by the provider, else the requested id
    text: str
    created_at_utc: datetime  # local wall-clock time the response was received
    latency_ms: float
    request_id: str | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    raw_response_hash: str
    retry_count: int = 0


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Provider(Protocol):
    name: str

    def complete(
        self,
        system: str,
        user: str,
        *,
        model: str,
        api_key: str,
        timeout_s: float,
        max_output_tokens: int,
        retry_limit: int,
    ) -> LLMResponse: ...


def with_retries[T](
    call: Callable[[], T],
    *,
    retry_limit: int,
    sleep: Callable[[float], None] = time.sleep,
    base_delay_s: float = 1.0,
) -> tuple[T, int]:
    """Run `call`; retry ONLY retryable ProviderErrors with exponential backoff + jitter.
    Returns (result, retry_count). Auth/invalid-request/etc. fail immediately."""
    attempt = 0
    while True:
        try:
            return call(), attempt
        except ProviderError as e:
            if e.kind not in RETRYABLE or attempt >= retry_limit:
                e.retry_count = attempt
                raise
            sleep(base_delay_s * (2**attempt) * (0.5 + random.random()))  # noqa: S311 - jitter
            attempt += 1
