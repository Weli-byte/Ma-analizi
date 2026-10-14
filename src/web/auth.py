"""Sign-in providers (ADR 0052). Only what can be done HONESTLY is on:

- guest: always available.
- google: verifies a Google ID token with Google's own `tokeninfo` endpoint and checks audience, issuer, expiry and
  e-mail verification. Needs `GOOGLE_CLIENT_ID` (free: Google Cloud console -> OAuth client, type Web). NOT verified live
  until the owner creates the client id.
- apple: "Sign in with Apple" (ADR 0057): verifies the identity token (RS256) against Apple's published keys with pure
  Python RSA (no extra dependency), checking issuer, audience, expiry and signature. Needs `APPLE_CLIENT_ID` (a Services ID of a
  PAID Apple Developer account, domain and return URL registered there). NOT verified live: the code is tested with a locally
  generated key pair only, and the button stays off until the variable is set.
- phone: SMS one-time codes need an SMS provider (Firebase, Twilio, ...): NOT_CONFIGURED.
A provider that is not configured says so on the login screen; nothing is simulated.
"""

import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from .session import User

TOKENINFO = "https://oauth2.googleapis.com/tokeninfo"
GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")


def google_client_id() -> str | None:
    return os.environ.get("GOOGLE_CLIENT_ID") or None


def provider_status() -> dict[str, bool]:
    from . import phone

    return {"google": bool(google_client_id()), "apple": bool(apple_client_id()), "phone": phone.configured()}


def validate_google_claims(claims: dict, client_id: str, now: float | None = None) -> User | None:
    """Pure check of the documented tokeninfo fields; None when anything is wrong."""
    now = now or time.time()
    if claims.get("aud") != client_id or claims.get("iss") not in GOOGLE_ISSUERS:
        return None
    if int(claims.get("exp", 0)) <= now or str(claims.get("email_verified")).lower() != "true":
        return None
    if not claims.get("sub"):
        return None
    return User(
        "google",
        claims.get("name") or claims.get("email", "Google kullanıcısı").split("@")[0],
        claims.get("email"),
        f"google:{claims['sub']}",
    )


def verify_google(id_token: str) -> User | None:
    cid = google_client_id()
    if not cid:
        return None
    url = f"{TOKENINFO}?{urllib.parse.urlencode({'id_token': id_token})}"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:  # noqa: S310 - fixed https host
            return validate_google_claims(json.loads(r.read().decode()), cid)
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None


# ------------------------------------------------------------------------------------------------ Sign in with Apple
APPLE_KEYS = "https://appleid.apple.com/auth/keys"
APPLE_ISSUER = "https://appleid.apple.com"
_SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")  # DER DigestInfo of SHA-256 (RFC 8017)
_keys_cache: dict = {"at": 0.0, "keys": []}


def apple_client_id() -> str | None:
    return os.environ.get("APPLE_CLIENT_ID") or None


def _b64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def rsa_pkcs1_sha256_verify(n: int, e: int, message: bytes, signature: bytes) -> bool:
    """RSASSA-PKCS1-v1_5 verification with SHA-256 (RFC 8017 section 8.2.2), public-key operation only."""
    k = (n.bit_length() + 7) // 8
    if len(signature) != k:
        return False
    em = pow(int.from_bytes(signature, "big"), e, n).to_bytes(k, "big")
    digest = _SHA256_PREFIX + hashlib.sha256(message).digest()
    expected = b"\x00\x01" + b"\xff" * (k - len(digest) - 3) + b"\x00" + digest
    return k >= len(digest) + 11 and em == expected


def apple_keys(now: float | None = None) -> list[dict]:
    """Apple's published signing keys, cached for an hour; an unreachable host gives the stale copy or nothing."""
    now = now or time.time()
    if _keys_cache["keys"] and now - _keys_cache["at"] < 3600:
        return _keys_cache["keys"]
    try:
        with urllib.request.urlopen(APPLE_KEYS, timeout=10) as r:  # noqa: S310 - fixed https host
            _keys_cache.update(at=now, keys=json.loads(r.read().decode())["keys"])
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError):
        pass
    return _keys_cache["keys"]


def validate_apple_token(token: str, client_id: str, keys: list[dict], now: float | None = None) -> User | None:
    """Pure check of an Apple identity token; None when anything is wrong."""
    now = now or time.time()
    try:
        head_b64, body_b64, sig_b64 = token.split(".")
        head, claims = json.loads(_b64(head_b64)), json.loads(_b64(body_b64))
        if head.get("alg") != "RS256":
            return None
        key = next((k for k in keys if k.get("kid") == head.get("kid") and k.get("kty") == "RSA"), None)
        if key is None:
            return None
        n, e = int.from_bytes(_b64(key["n"]), "big"), int.from_bytes(_b64(key["e"]), "big")
        if not rsa_pkcs1_sha256_verify(n, e, f"{head_b64}.{body_b64}".encode(), _b64(sig_b64)):
            return None
    except (ValueError, KeyError, TypeError):
        return None
    if claims.get("iss") != APPLE_ISSUER or claims.get("aud") != client_id:
        return None
    if int(claims.get("exp", 0)) <= now or not claims.get("sub"):
        return None
    email = claims.get("email")
    name = (email or "Apple kullanıcısı").split("@")[0] if email else "Apple kullanıcısı"
    return User("apple", name, email, f"apple:{claims['sub']}")


def verify_apple(id_token: str) -> User | None:
    cid = apple_client_id()
    if not cid:
        return None
    return validate_apple_token(id_token, cid, apple_keys())
