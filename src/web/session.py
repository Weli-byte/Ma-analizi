"""Signed, expiring session cookie (ADR 0052). Stateless: the cookie carries a small JSON payload and an HMAC-SHA256
signature made with `FORECAST_SESSION_SECRET` (a random per-process secret is used when it is not set, so sessions end
when the server restarts). Payload: provider, display name, optional e-mail, expiry. No password is ever stored."""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass

COOKIE = "mt_session"
MAX_AGE = 30 * 24 * 3600
_FALLBACK = secrets.token_bytes(32)


@dataclass(frozen=True)
class User:
    provider: str  # guest | google | apple | phone
    name: str
    email: str | None = None


def _secret() -> bytes:
    s = os.environ.get("FORECAST_SESSION_SECRET")
    return s.encode() if s else _FALLBACK


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign(user: User, now: float | None = None) -> str:
    payload = _b64(
        json.dumps(
            {"p": user.provider, "n": user.name, "e": user.email, "x": int((now or time.time()) + MAX_AGE)}
        ).encode()
    )
    return f"{payload}.{_b64(hmac.new(_secret(), payload.encode(), hashlib.sha256).digest())}"


def verify(token: str | None, now: float | None = None) -> User | None:
    """The user, or None for a missing, malformed, tampered or expired cookie."""
    if not token or "." not in token:
        return None
    payload, sig = token.rsplit(".", 1)
    good = _b64(hmac.new(_secret(), payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, good):
        return None
    try:
        d = json.loads(_unb64(payload))
    except ValueError:
        return None
    if d.get("x", 0) < (now or time.time()):
        return None
    return User(d["p"], d["n"], d.get("e"))
