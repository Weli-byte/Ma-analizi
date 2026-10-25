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
# What is worth a message (ADR 0077, from the first day of real alerts): every critical alert; of the warnings only the ones somebody can act on. A single schedule-provider failure is
# routine (TLS timeouts happen; three in a row is already the critical `provider_unavailable`), `slow_inference` is structural and `artifact_store_missing` is information in the cloud.
WARNING_ALLOW = ("scheduler_stale", "no_shadow_run", "provider_not_configured", "model_missing", "fallback_rate", "outcome_lag", "drift_critical", "performance_regression")


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


def should_notify(severity: str, key: str) -> bool:
    if severity == "critical":
        return True
    return severity == "warning" and key.startswith(WARNING_ALLOW)


def watchdog(stamp_path: Path, max_hours: float, state_path: Path, now, label: str) -> str:
    """A dead-man switch for a job that cannot report its own death: `stamp_path` is a JSON file with a `generated_at_utc` (or `generated_at`) timestamp written by that job. When it is older
    than `max_hours` (or missing, or unreadable) one message per day says so. Returns FRESH or STALE_<result of the send>."""
    from datetime import datetime

    try:
        data = json.loads(stamp_path.read_text(encoding="utf-8")) if stamp_path.is_file() else {}
        stamp = datetime.fromisoformat(data.get("generated_at_utc") or data["generated_at"])
        age = (now - stamp).total_seconds() / 3600.0
    except (OSError, ValueError, KeyError):
        age = float("inf")
    if age <= max_hours:
        return "FRESH"
    when = "hiç" if age == float("inf") else f"{age:.1f} saattir"
    text = f"{label} {when} güncellenmedi (sınır {max_hours:g} saat). Bulut işi durmuş olabilir: GitHub Actions sayfasına bak."
    return "STALE_" + notify_once(state_path, f"watchdog:{label}:{now.date().isoformat()}", text)


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
    ap.add_argument("--watchdog", default=None, help="JSON file with a generated_at(_utc) stamp; a message when it is older than --max-hours")
    ap.add_argument("--max-hours", type=float, default=6.0)
    ap.add_argument("--label", default="Gölge işi")
    ap.add_argument("--state", default="notified_watchdog.json")
    a = ap.parse_args(argv)
    if a.watchdog:
        from datetime import UTC, datetime

        res = watchdog(Path(a.watchdog), a.max_hours, Path(a.state), datetime.now(UTC), a.label)
        print(res)
        return 0
    result = send(a.text)
    print(result)
    return 0 if result == "SENT" else 1


if __name__ == "__main__":
    raise SystemExit(main())
