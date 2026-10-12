"""Sign-in providers (ADR 0052). Only what can be done HONESTLY is on:

- guest: always available.
- google: verifies a Google ID token with Google's own `tokeninfo` endpoint and checks audience, issuer, expiry and
  e-mail verification. Needs `GOOGLE_CLIENT_ID` (free: Google Cloud console -> OAuth client, type Web). NOT verified live
  until the owner creates the client id.
- apple: "Sign in with Apple" needs a paid Apple Developer account and a verified domain: NOT_CONFIGURED.
- phone: SMS one-time codes need an SMS provider (Firebase, Twilio, ...): NOT_CONFIGURED.
A provider that is not configured says so on the login screen; nothing is simulated.
"""

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

    return {"google": bool(google_client_id()), "apple": False, "phone": phone.configured()}


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
