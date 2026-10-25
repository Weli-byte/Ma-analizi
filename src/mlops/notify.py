"""Telegram notifications (ADR 0077): a short message when something needs the owner's eyes. The bot token and the chat id are read from the environment
(`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`; repository secrets in the cloud jobs, never in a file). Without them every call is a no-op that says NOT_CONFIGURED.
A failure to send never fails a job and never prints the token (only the exception class). `notify_once` remembers what it already sent in a small JSON state file, so the
same alert or the same status change is announced once."""

import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.telegram.org/bot{token}/sendMessage"
SEVERITIES = ("warning", "critical")


def configured() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))


def send(text: str, timeout: float = 20.0) -> str:
    """SENT, NOT_CONFIGURED or FAILED:<exception class>. TLS verification stays on (the default context)."""
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return "NOT_CONFIGURED"
    body = urllib.parse.urlencode({"chat_id": chat, "text": text[:3800], "disable_web_page_preview": "true"}).encode("utf-8")
    try:
        with urllib.request.urlopen(urllib.request.Request(API.format(token=token), data=body, method="POST"), timeout=timeout) as r:
            return "SENT" if r.status == 200 else f"FAILED:HTTP{r.status}"
    except Exception as e:  # noqa: BLE001 - only the class name is reported: the message of a urllib error can contain the request URL, which contains the token
        return f"FAILED:{type(e).__name__}"


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        return {}


def notify_once(state_path: Path, key: str, text: str) -> str:
    """Send `text` unless `key` was announced before. The key is stored only after a successful send (a failed send is retried by the next run)."""
    state = _load(state_path)
    if key in state.get("sent", []):
        return "ALREADY_SENT"
    result = send(text)
    if result == "SENT":
        state.setdefault("sent", []).append(key)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return result


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Send one Telegram message (needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in the environment).")
    ap.add_argument("text", nargs="?", default="Test: Welliddia bildirimleri çalışıyor.")
    a = ap.parse_args(argv)
    result = send(a.text)
    print(result)
    return 0 if result == "SENT" else 1


if __name__ == "__main__":
    raise SystemExit(main())
