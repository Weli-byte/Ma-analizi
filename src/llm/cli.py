"""Run the S8 LLM benchmark on the chronological validation split.

    python -m src.llm.cli --provider openai [--root DIR] [--limit N]

Historical track only (this repo has no live/future fixture feed yet -- S13/S14 scope); the
runner itself supports `ExperimentType.PROSPECTIVE` for when that exists (`tests/test_llm.py`).
Disabled by default (`configs/provider.yaml`): nothing is called unless `--provider` names an
entry with `enabled: true` and its API key env var set. No key is ever printed or logged.

Outputs: artifacts/llm_runs/<data_version>_<provider>_<model>/ (gitignored): predictions.jsonl
(immutable PredictionRecords, one per successful call), calls.jsonl (every LLMCallRecord,
including failures), summary.md, hashes.json.
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

from src.cli_utils import configure_output
from src.config import config_dir_for, load_config
from src.data.dataset import resolve_dataset
from src.evaluation.context import EvalMode, make_context
from src.evaluation.dataset import load_rows
from src.features.artifact import load_features
from src.schemas import ExperimentType, PredictionLedger
from src.schemas.lifecycle import dump_records
from src.versioning import canonical_json

from .budget import BudgetExceeded
from .runner import ProviderNotConfigured, resolve_provider, run_llm_benchmark

ROOT = Path(__file__).resolve().parents[2]


def render_summary(provider: str, model: str, results) -> str:
    by_status: dict[str, int] = {}
    total_cost = 0.0
    unknown_cost = 0
    latencies = []
    for r in results:
        by_status[r.call.status] = by_status.get(r.call.status, 0) + 1
        total_cost += r.call.cost_usd or 0.0
        unknown_cost += r.call.cost_usd is None
        if r.call.latency_ms is not None:
            latencies.append(r.call.latency_ms)
    avg_latency = sum(latencies) / len(latencies) if latencies else 0.0
    lines = [
        f"# LLM benchmark — {provider}/{model}",
        "",
        f"- fixtures attempted: {len(results)}",
        f"- status counts: {by_status}",
        f"- total estimated cost: ${total_cost:.6f} (configs/pricing.yaml estimate, not an invoice; "
        f"{unknown_cost} call(s) without a cost estimate)",
        f"- average latency: {avg_latency:.0f} ms",
        "",
        "Track: HISTORICAL_BACKTEST (replay). The LLM's training data may already contain these "
        "matches' real results -- this is a memorization-risk benchmark, NOT evidence of "
        "genuine forward-looking skill. A PROSPECTIVE (forward-only) run requires a live fixture "
        "feed, not yet built (S13/S14).",
    ]
    return "\n".join(lines) + "\n"


def run_llm_cli(root: Path, provider_name: str, limit: int | None) -> Path:
    cdir = config_dir_for(root)
    data_cfg = load_config("data", cdir)
    model_cfg = load_config("model", cdir)
    eval_cfg = load_config("evaluation", cdir)
    provider_cfg = load_config("provider", cdir)

    adapter, model, api_key = resolve_provider(provider_cfg, provider_name)

    ref = resolve_dataset(root / data_cfg.processed_dir)
    feats = load_features(root, ref, model_cfg.feature_version)
    rows = load_rows(ref, make_context(EvalMode.VALIDATION, eval_cfg), eval_cfg.validation_seasons, feats)
    if limit is not None:
        rows = rows[:limit]

    results = run_llm_benchmark(
        rows, adapter, model, api_key, ExperimentType.HISTORICAL_BACKTEST, provider_cfg.budget,
        feature_version=model_cfg.feature_version, data_version=ref.data_version,
    )  # fmt: skip

    tag = f"{ref.data_version}_{provider_name}_{model.replace('/', '_')}"
    out_dir = root / "artifacts" / "llm_runs" / tag
    tmp = out_dir.with_name(f".tmp-{uuid.uuid4().hex[:8]}")
    tmp.mkdir(parents=True)
    try:
        ledger = PredictionLedger()
        for r in results:
            if r.prediction is not None:
                ledger.append(r.prediction)
        pred_text = dump_records([r.prediction for r in results if r.prediction is not None])
        (tmp / "predictions.jsonl").write_text(pred_text + "\n" if pred_text else "", encoding="utf-8")
        calls_text = "\n".join(r.call.model_dump_json() for r in results)
        (tmp / "calls.jsonl").write_text(calls_text + "\n" if calls_text else "", encoding="utf-8")
        summary = render_summary(provider_name, model, results)
        (tmp / "summary.md").write_text(summary, encoding="utf-8")
        hashes = {
            "predictions": hashlib.sha256(pred_text.encode()).hexdigest(),
            "calls": hashlib.sha256(calls_text.encode()).hexdigest(),
            "snapshot_hashes": hashlib.sha256(
                canonical_json(sorted(r.call.snapshot_hash for r in results)).encode()
            ).hexdigest(),
        }
        (tmp / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")
        if out_dir.exists():
            shutil.rmtree(out_dir)
        tmp.replace(out_dir)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
    return out_dir


def main(argv: list[str] | None = None) -> int:
    configure_output()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--provider", required=True, choices=["openai", "anthropic", "gemini"])
    p.add_argument("--limit", type=int, default=None, help="cap rows (cost control)")
    a = p.parse_args(argv)
    if os.environ.get("ALLOW_REAL_LLM_CALLS", "").lower() != "true":
        print("REAL_CALLS_DISABLED_BY_OPERATOR: set ALLOW_REAL_LLM_CALLS=true to spend API budget. "
              "No API call was made; the benchmark is NOT complete.", file=sys.stderr)  # fmt: skip
        return 4
    try:
        out_dir = run_llm_cli(Path(a.root), a.provider, a.limit)
    except (BudgetExceeded, ProviderNotConfigured, RuntimeError, ValueError, KeyError) as e:
        print(f"LLM BENCHMARK FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    print((out_dir / "summary.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
