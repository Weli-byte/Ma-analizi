"""What is configured and what is still missing for the product (ADR 0081): `python -m src.readiness [--github]`.

Prints, per item, SET or NOT_SET (names only, a value is never printed or logged), what the item unlocks and what the owner does next. The local `.env` and the process environment are read;
with `--github` the NAMES of the repository secrets are read through `gh secret list` (values are write-only on GitHub). Nothing is changed and nothing is called over the network except `gh`."""

import argparse
import os
import shutil
import subprocess

from src.cli_utils import configure_output, load_dotenv

# (name, any-of env vars that must ALL be set, unlocks, next step for the owner)
LOCAL = [
    ("football-data.org key", ("FOOTBALL_DATA_ORG_API_KEY",), "schedules and results of the free competitions", "ücretsiz anahtar zaten alınmış olmalı: football-data.org hesabından kopyala"),
    ("The Odds API key", ("THE_ODDS_API_KEY",), "exact-timestamp odds (edge/EV/CLV, value suggestions)", "the-odds-api.com ücretsiz anahtarı"),
    ("API-Football key", ("API_FOOTBALL_KEY",), "injuries and suspensions card", "dashboard.api-football.com: hesap 'suspended' görünüyordu, giriş yapıp nedenine bak"),
    ("Highlightly key", ("HIGHLIGHTLY_API_KEY",), "corners / cards / shots (published only with stats_plan: pro)", "PRO plan satın alınca anahtarı güncelle, sonra bana haber ver"),
    ("Anthropic key", ("ANTHROPIC_API_KEY",), "Claude in the LLM benchmark (paid usage)", "console.anthropic.com: API anahtarı ve kredi (ücretli)"),
    ("Google sign-in", ("GOOGLE_CLIENT_ID",), "Sign in with Google", "Vercel ortam değişkeni olarak kurulu (canlıda doğrulandı); bu bilgisayarda gerekmez"),
    ("Apple sign-in", ("APPLE_CLIENT_ID",), "Sign in with Apple", "Apple Developer hesabı (ücretli) + Services ID"),
    ("Phone sign-in (Twilio Verify)", ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_VERIFY_SID"), "SMS sign-in", "twilio.com hesabı + Verify servisi (mesaj başına ücret)"),
    ("Persistent user database (Turso)", ("FORECAST_TURSO_URL", "FORECAST_TURSO_TOKEN"), "favourites and plans survive a redeploy", "Vercel ortam değişkeni olarak kurulu olmalı; bu bilgisayarda gerekmez"),
    ("Session secret", ("FORECAST_SESSION_SECRET",), "signed login cookies on the deployed site", "Vercel ortam değişkeni; bu bilgisayarda gerekmez"),
    ("Telegram bot", ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"), "owner notifications (local test only; the cloud jobs use repository secrets)", "BotFather + @userinfobot"),
]  # fmt: skip
GITHUB = [
    ("DATA_REPO_DEPLOY_KEY", "cloud jobs write to the private data repository"),
    ("FOOTBALL_DATA_ORG_API_KEY", "cloud schedules/results (shadow loop, markets)"),
    ("THE_ODDS_API_KEY", "cloud exact odds"),
    ("API_FOOTBALL_KEY", "cloud injuries"),
    ("HIGHLIGHTLY_API_KEY", "cloud statistics"),
    ("VERCEL_TOKEN", "automatic redeploy of the public site"),
    ("VERCEL_ORG_ID", "automatic redeploy of the public site"),
    ("VERCEL_PROJECT_ID", "automatic redeploy of the public site"),
    ("TELEGRAM_BOT_TOKEN", "owner notifications"),
    ("TELEGRAM_CHAT_ID", "owner notifications"),
]


def local_status(environ=None) -> list[dict]:
    env = os.environ if environ is None else environ
    out = []
    for name, keys, unlocks, nxt in LOCAL:
        missing = [k for k in keys if not env.get(k)]
        out.append({"item": name, "status": "SET" if not missing else "NOT_SET", "missing": missing, "unlocks": unlocks, "next": "" if not missing else nxt})
    return out


def github_status(names: set[str] | None) -> list[dict]:
    if names is None:
        return [{"item": n, "status": "UNKNOWN (gh is not available or not logged in)", "unlocks": u} for n, u in GITHUB]
    return [{"item": n, "status": "SET" if n in names else "NOT_SET", "unlocks": u} for n, u in GITHUB]


def github_secret_names() -> set[str] | None:
    if not shutil.which("gh"):
        return None
    try:
        r = subprocess.run(["gh", "secret", "list", "--json", "name", "--jq", ".[].name"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return set(r.stdout.split()) if r.returncode == 0 else None


def stats_plan() -> str:
    import re
    from pathlib import Path

    m = re.search(r"^stats_plan:\s*(\w+)", (Path(__file__).resolve().parents[1] / "configs" / "markets.yaml").read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else "unknown"


def render(local: list[dict], github: list[dict] | None, plan: str) -> str:
    lines = ["== Bilgisayar (.env / ortam) =="]
    for r in local:
        mark = "OK " if r["status"] == "SET" else "-- "
        lines.append(f"{mark}{r['item']}: {r['status']}" + (f"  ->  {r['next']}" if r["next"] else "") + f"   [açar: {r['unlocks']}]")
    lines.append(f"-- Highlightly planı (configs/markets.yaml stats_plan): {plan} (pro olunca korner/kart/şut yayınlanır)")
    if github is not None:
        lines.append("== GitHub depo gizli değişkenleri (yalnızca adlar) ==")
        for r in github:
            lines.append(f"{'OK ' if r['status'] == 'SET' else '-- '}{r['item']}: {r['status']}   [açar: {r['unlocks']}]")
    return "\n".join(lines)


def main(argv=None) -> int:
    configure_output()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--github", action="store_true", help="also list which repository secrets exist (names only, needs gh)")
    a = ap.parse_args(argv)
    load_dotenv()
    print(render(local_status(), github_status(github_secret_names()) if a.github else None, stats_plan()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
