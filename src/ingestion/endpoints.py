"""Provider endpoint resolution. Production always talks to the real https endpoint.

An endpoint can be redirected ONLY to a loopback http address (127.0.0.1 / localhost) through an
environment variable, so the real client code (URL building, request, parsing, processing) can be
exercised over a real HTTP socket against files that hold REAL captured provider responses. Any other
override is refused: TLS verification is never bypassed and traffic can never be silently sent to a
different remote host.
"""

import os

LOOPBACK = ("http://127.0.0.1", "http://localhost")


def endpoint(env_name: str, default: str) -> str:
    value = os.environ.get(env_name)
    if not value:
        return default
    if not value.startswith(LOOPBACK):
        raise ValueError(f"{env_name} may only point to a loopback http address, got {value!r}")
    return value.rstrip("/")
