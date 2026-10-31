"""Live provider smoke (ADR 0091): one real, cheap request per provider whose key and terms allow it, recorded with UTC time, HTTP status, latency and size.

No credit-spending call is made: The Odds API `/v4/sports` costs no credit and reports the remaining credits; football-data.org one competition listing; API-Football `/status`; ESPN one
scoreboard; openfootball one raw file. Highlightly is not called from here (the owner's network cannot reach its host; the cloud collector is its smoke). A missing key is NOT_CONFIGURED, an
unreachable host UNREACHABLE, a refused account is reported with the provider's own message (redacted). Keys are sent in headers or the query string the provider requires and are scrubbed
from every recorded text: the output can be committed.

`python -m src.lineage.smoke --out docs/phase3/s13_provider_smoke.json`
"""

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from src.cli_utils import configure_output, load_dotenv
from src.config import load_config
from src.markets.injuries import KEY_ENV as AF_ENV
from src.odds.theoddsapi import KEY_ENV as ODDS_ENV

ODDS_ENVS = (ODDS_ENV, ODDS_ENV + "_2")  # the second key of ADR 0059
FD_ENV = load_config("ingestion").providers["football-data-org"].api_key_env  # the NAME of the variable, from configs/ingestion.yaml

UA = {"User-Agent": "Mozilla/5.0 (football-forecasting smoke, ADR 0091)"}


def _redact(text: str, secrets: list[str]) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


def call(name: str, url: str, headers: dict, secrets: list[str], timeout: float = 30.0) -> dict:
    t0 = time.perf_counter()
    rec = {"provider": name, "at_utc": datetime.now(UTC).isoformat(), "endpoint": _redact(url.split("?")[0], secrets)}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={**UA, **headers}), timeout=timeout) as r:
            body = r.read()
            rec |= {"status": "OK", "http": r.status, "bytes": len(body), "latency_ms": round(1000 * (time.perf_counter() - t0)),
                    "headers": {k: r.headers.get(k) for k in ("x-requests-remaining", "x-requests-used", "X-Requests-Available-Minute") if r.headers.get(k) is not None}}  # fmt: skip
            rec["_body"] = body
    except urllib.error.HTTPError as e:
        rec |= {"status": "HTTP_ERROR", "http": e.code, "latency_ms": round(1000 * (time.perf_counter() - t0)), "message": _redact(e.read()[:200].decode("utf-8", "replace"), secrets)}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        rec |= {"status": "UNREACHABLE", "latency_ms": round(1000 * (time.perf_counter() - t0)), "message": _redact(f"{type(e).__name__}: {getattr(e, 'reason', e)}", secrets)[:200]}
    return rec


def run() -> list[dict]:
    load_dotenv()
    keys = {k: os.environ.get(k, "") for k in (FD_ENV, *ODDS_ENVS, AF_ENV)}
    secrets = [v for v in keys.values() if v]
    out = []
    if keys[FD_ENV]:
        out.append(call("football-data.org", "https://api.football-data.org/v4/competitions/PL", {"X-Auth-Token": keys[FD_ENV]}, secrets))
    else:
        out.append({"provider": "football-data.org", "status": "NOT_CONFIGURED"})
    for k in ODDS_ENVS:
        if keys[k]:
            out.append(call(f"the-odds-api ({k.lower()})", f"https://api.the-odds-api.com/v4/sports/?apiKey={keys[k]}", {}, secrets))
        else:
            out.append({"provider": f"the-odds-api ({k.lower()})", "status": "NOT_CONFIGURED"})
    if keys[AF_ENV]:
        r = call("api-football", "https://v3.football.api-sports.io/status", {"x-apisports-key": keys[AF_ENV]}, secrets)
        body = r.get("_body")
        if body:
            try:
                errs = json.loads(body).get("errors") or {}
                if errs:
                    r |= {"status": "ACCOUNT_REFUSED", "message": _redact(json.dumps(errs)[:200], secrets)}
            except ValueError:
                pass
        out.append(r)
    else:
        out.append({"provider": "api-football", "status": "NOT_CONFIGURED"})
    day = datetime.now(UTC).strftime("%Y%m%d")
    out.append(call("espn (keyless)", f"https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard?dates={day}", {}, secrets))
    out.append(call("openfootball (keyless)", "https://raw.githubusercontent.com/openfootball/football.json/master/2024-25/en.1.json", {}, secrets))
    out.append({"provider": "highlightly", "status": "NOT_CALLED_FROM_THIS_NETWORK", "message": "host unreachable from the owner's network; verified by the cloud statistics collector (ADR 0050)"})
    for r in out:
        r.pop("_body", None)
    return out


def main(argv=None) -> int:
    configure_output()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    res = run()
    text = json.dumps({"schema": "provider-smoke-v1", "results": res}, indent=1, sort_keys=True)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text + "\n", encoding="utf-8", newline="\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
