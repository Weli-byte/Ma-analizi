"""Real-API reality check (ADR 0024):  python -m src.llm.live_smoke [--capture] [--root DIR]

Makes exactly ONE real call per ENABLED provider (tiny request, hard budget from
`configs/provider.yaml`, checked BEFORE any call). A provider whose key env var is empty is
reported NOT_CONFIGURED -- never PASS. There is no mock path. `LIVE_AI_READY = TRUE` only when
every enabled provider returned a real, schema-valid response.

`--capture` stores each real response (text + metadata, no secrets) under
tests/fixtures/real_provider_captures/ as a `REAL_PROVIDER_CAPTURE` for offline parser tests.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from src.cli_utils import configure_output, load_dotenv
from src.config import config_dir_for, load_config

from .budget import BudgetExceeded, estimate_input_tokens, preflight
from .contract import MalformedLLMOutput, parse_forecast
from .pricing import load_price_table
from .prompt import SYSTEM_PROMPT, build_user_prompt, prompt_meta
from .providers import PROVIDERS, ProviderError

ROOT = Path(__file__).resolve().parents[2]
CAPTURE_DIR = ROOT / "tests" / "fixtures" / "real_provider_captures"

# A real, tiny forecasting request in the production contract. Smoke only: its output is never
# stored as a prediction or fed to any benchmark.
SMOKE_SNAPSHOT = {
    "purpose": "live API smoke check, not a benchmark fixture",
    "home_team": "Arsenal",
    "away_team": "Chelsea",
    "information_cutoff": "2025-01-01T00:00:00+00:00",
    "permitted_historical_features": {"home_form_points_5": 10.0, "away_form_points_5": 6.0},
}


def run_smoke(root: Path, capture: bool) -> tuple[list[dict], bool]:
    cfg = load_config("provider", config_dir_for(root))
    prices = load_price_table()
    user = build_user_prompt(SMOKE_SNAPSHOT)
    in_tok = estimate_input_tokens(SYSTEM_PROMPT, user)

    enabled = [(n, e) for n, e in cfg.providers.items() if e.enabled]
    rows: list[dict] = []
    runnable = []
    for name, entry in enabled:
        if not cfg.api_key(name):
            rows.append({"provider": name, "model": entry.model, "status": "NOT_CONFIGURED",
                         "detail": f"${entry.api_key_env} is empty"})  # fmt: skip
        else:
            runnable.append((name, entry))

    plan = [(n, e.model, in_tok) for n, e in runnable]
    exposure = preflight(plan, cfg.budget, prices)  # raises BudgetExceeded -> no call at all
    print(
        f"PRE-FLIGHT OK: {exposure.request_count} request(s), worst-case "
        f"{exposure.token_budget} tokens, ${exposure.max_cost_usd:.6f} (pricing {prices.version})"
    )

    for name, entry in runnable:
        row = {"provider": name, "model": entry.model}
        try:
            resp = PROVIDERS[name].complete(
                SYSTEM_PROMPT, user, model=entry.model, api_key=cfg.api_key(name),
                timeout_s=cfg.budget.request_timeout_seconds,
                max_output_tokens=cfg.budget.max_output_tokens, retry_limit=cfg.budget.retry_limit,
            )  # fmt: skip
        except ProviderError as e:
            rows.append({**row, "status": "FAIL", "detail": str(e), "retries": e.retry_count})
            continue
        try:
            out = parse_forecast(resp.text)
            schema_ok, detail = True, ""
        except MalformedLLMOutput as e:
            out, schema_ok, detail = None, False, str(e)
        cost = prices.estimate(name, entry.model, resp.input_tokens, resp.output_tokens)
        rows.append({
            **row, "status": "PASS" if schema_ok else "FAIL", "detail": detail,
            "reported_model": resp.model, "request_id": resp.request_id,
            "latency_ms": round(resp.latency_ms), "input_tokens": resp.input_tokens,
            "output_tokens": resp.output_tokens, "estimated_cost_usd": cost,
            "schema_valid": schema_ok, "retries": resp.retry_count,
            "p_home": out.home_probability if out else None,
        })  # fmt: skip
        if capture:
            CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
            meta = prompt_meta(user)
            (CAPTURE_DIR / f"{name}.json").write_text(
                json.dumps({
                    "label": "REAL_PROVIDER_CAPTURE",
                    "captured_at_utc": datetime.now(UTC).isoformat(),
                    "provider": name, "requested_model": entry.model, "reported_model": resp.model,
                    "request_id": resp.request_id, "raw_response_hash": resp.raw_response_hash,
                    "input_tokens": resp.input_tokens, "output_tokens": resp.output_tokens,
                    "total_tokens": resp.total_tokens, "prompt_meta": meta.__dict__,
                    "text": resp.text,
                }, indent=2, sort_keys=True),
                encoding="utf-8",
            )  # fmt: skip
    ready = bool(enabled) and all(r["status"] == "PASS" for r in rows)
    return rows, ready


def main(argv: list[str] | None = None) -> int:
    configure_output()
    load_dotenv()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--capture", action="store_true", help="store real responses as test fixtures")
    a = p.parse_args(argv)
    try:
        rows, ready = run_smoke(Path(a.root), a.capture)
    except BudgetExceeded as e:
        print(f"BUDGET EXCEEDED - NO API CALL MADE: {e}", file=sys.stderr)
        return 3
    for r in rows:
        print(json.dumps(r, sort_keys=True))
    print(f"LIVE_AI_READY = {str(ready).upper()}")
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
