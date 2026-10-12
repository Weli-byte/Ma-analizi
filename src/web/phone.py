"""Phone sign-in through Twilio Verify (ADR 0054). Needs TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_VERIFY_SID
(a Twilio account with a Verify service; paid per SMS). NOT verified live until the owner provides them: without them the
login screen says the method is not enabled. The raw number is never stored: the user id is a hash and the display name is
masked. A per-number and per-client throttle limits code requests (SMS abuse costs real money)."""

import base64
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from .session import User

BASE = "https://verify.twilio.com/v2/Services"


def configured() -> bool:
    return all(os.environ.get(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_VERIFY_SID"))


def normalize(raw: str) -> str | None:
    """E.164 or None. Turkish national numbers (05xx..., 5xx...) get +90."""
    s = re.sub(r"[\s().-]", "", raw or "")
    if s.startswith("00"):
        s = "+" + s[2:]
    if s.startswith("0") and len(s) == 11:
        s = "+90" + s[1:]
    elif re.fullmatch(r"5\d{9}", s):
        s = "+90" + s
    return s if re.fullmatch(r"\+[1-9]\d{7,14}", s) else None


def user_for(e164: str) -> User:
    return User(
        "phone", "Telefon ····" + e164[-4:], None, "phone:" + hashlib.sha256(e164.encode()).hexdigest()[:16]
    )


class Throttle:
    """At most `limit` events per key in `window` seconds."""

    def __init__(self, limit: int, window: float, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, window, clock
        self.events: dict[str, list[float]] = {}

    def allow(self, key: str) -> bool:
        now = self.clock()
        recent = [t for t in self.events.get(key, []) if now - t < self.window]
        if len(recent) >= self.limit:
            self.events[key] = recent
            return False
        self.events[key] = [*recent, now]
        return True


def _post(path: str, data: dict) -> dict | None:
    sid, tok, svc = (os.environ[k] for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_VERIFY_SID"))
    req = urllib.request.Request(
        f"{BASE}/{svc}/{path}",
        data=urllib.parse.urlencode(data).encode(),
        method="POST",
        headers={"Authorization": "Basic " + base64.b64encode(f"{sid}:{tok}".encode()).decode()},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310 - fixed https host
            return json.loads(r.read().decode())
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None  # never include the credentials or the number in an error


def send_code(e164: str) -> bool:
    if not configured():
        return False
    d = _post("Verifications", {"To": e164, "Channel": "sms"})
    return bool(d) and d.get("status") == "pending"


def check_code(e164: str, code: str) -> bool:
    if not configured() or not re.fullmatch(r"\d{4,10}", code or ""):
        return False
    d = _post("VerificationCheck", {"To": e164, "Code": code})
    return bool(d) and d.get("status") == "approved"
