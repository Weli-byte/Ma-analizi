"""Real LLM benchmark engine (ADR 0026):

    python -m src.llm.benchmark --track historical --providers openai,gemini,groq --limit 20
    python -m src.llm.benchmark --track prospective --league PL --limit 5

Prints the PLAN first (match/request/provider/model counts, worst-case cost and tokens). Real calls
need `ALLOW_REAL_LLM_CALLS=true`; without it the plan is printed, NO API call is made and the
result is `REAL_CALLS_DISABLED_BY_OPERATOR` (benchmark NOT complete -- never replaced by a mock).
The configured budget (`configs/provider.yaml: budget`) is checked for the WHOLE run before the
first call; CLI flags can only LOWER it. A failed request yields no prediction; nothing is
substituted. Missing keys are reported NOT_CONFIGURED; there is no silent provider fallback.

Tracks: `historical` replays the chronological VALIDATION split (final-test seasons are locked by
`EvaluationContext`) and carries a memorization-risk caveat; `prospective` forecasts real upcoming
fixtures before kickoff (post-kickoff requests are refused before any call).

Outputs under artifacts/llm_runs/benchmark_<track>_<data_version>_<utc>/ : plan.json,
predictions.jsonl, calls.jsonl, responses.jsonl (raw provider text), coverage.{json,md},
evaluation.{json,md} (historical, LLM_REAL model class, raw vs calibrated), summary.md, hashes.json.
"""

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from src.cli_utils import configure_output, load_dotenv
from src.config import LLMBudget, config_dir_for, load_config
from src.data.dataset import resolve_dataset
from src.evaluation.context import EvalMode, make_context
from src.evaluation.dataset import load_rows
from src.features.artifact import load_features
from src.schemas import ExperimentType, PredictionLedger
from src.schemas.lifecycle import dump_records

from .budget import BudgetExceeded, estimate_input_tokens, preflight
from .coverage import CallContext, build_coverage, render_coverage_md
from .evaluation import evaluate_llm_predictions
from .forecast import LEAGUES, load_upcoming_rows
from .pricing import load_price_table
from .prompt import PROMPT_VERSION, SYSTEM_PROMPT, build_user_prompt
from .providers import PROVIDERS
from .runner import ProviderNotConfigured, run_llm_benchmark
from .snapshot import CutoffViolation, build_snapshot

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Plan:
    match_count: int
    request_count: int
    provider_count: int
    model_count: int
    est_max_cost_usd: float
    est_token_budget: int
    providers: list[str]
    not_configured: list[str]
    track: str

    def lines(self) -> list[str]:
        return [
            f"TRACK: {self.track}",
            f"MATCH COUNT: {self.match_count}",
            f"REQUEST COUNT: {self.request_count}",
            f"PROVIDER COUNT: {self.provider_count} ({', '.join(self.providers) or '-'})",
            f"MODEL COUNT: {self.model_count}",
            f"ESTIMATED MAX COST: ${self.est_max_cost_usd:.6f}",
            f"ESTIMATED TOKEN BUDGET: {self.est_token_budget}",
            f"NOT_CONFIGURED: {', '.join(self.not_configured) or '-'}",
        ]


def lowered_budget(base: LLMBudget, max_requests, max_cost, concurrency) -> LLMBudget:
    """CLI flags may only tighten the configured budget, never loosen it."""
    upd = {}
    if max_requests is not None:
        upd["max_requests_per_run"] = min(base.max_requests_per_run, max_requests)
    if max_cost is not None:
        upd["max_estimated_cost_usd"] = min(base.max_estimated_cost_usd, max_cost)
    if concurrency is not None:
        upd["max_concurrency"] = min(base.max_concurrency, concurrency)
    return base.model_copy(update=upd)


def sample_rows(rows: list, limit: int) -> list:
    """Deterministic, evenly spaced chronological sample (not the first N, which would all be
    the start of a season) -- no randomness."""
    if limit >= len(rows):
        return rows
    step = len(rows) / limit
    return [rows[int(i * step)] for i in range(limit)]


def run_benchmark(
    root: Path,
    track: str,
    provider_names: list[str] | None,
    limit: int | None,
    league: str,
    split: str,
    max_requests: int | None,
    max_cost: float | None,
    concurrency: int | None,
    prompt_version: str,
    cutoff_offset_hours: float,
    allow_real_calls: bool,
) -> tuple[Path | None, int]:
    if prompt_version != PROMPT_VERSION:
        raise ValueError(f"prompt_version {prompt_version!r} != production {PROMPT_VERSION!r} (needs ADR)")
    if track not in ("historical", "prospective"):
        raise ValueError("track must be 'historical' or 'prospective'")
    if track == "historical" and split not in ("validation", "train"):
        raise ValueError("split must be 'validation' or 'train' (final-test is locked)")
    cdir = config_dir_for(root)
    cfg = load_config("provider", cdir)
    model_cfg = load_config("model", cdir)
    data_cfg = load_config("data", cdir)
    eval_cfg = load_config("evaluation", cdir)
    prices = load_price_table()
    budget = lowered_budget(cfg.budget, max_requests, max_cost, concurrency)

    enabled = [
        (n, e) for n, e in cfg.providers.items() if e.enabled and (not provider_names or n in provider_names)
    ]
    unknown = set(provider_names or []) - set(cfg.providers)
    if unknown:
        raise ProviderNotConfigured(f"unknown provider(s): {sorted(unknown)}")
    runnable, not_configured = [], []
    for name, entry in enabled:
        if entry.model.upper() == "TBD" or not cfg.api_key(name):
            not_configured.append(name)
        else:
            runnable.append((name, entry))
    if not runnable:
        raise ProviderNotConfigured(f"no runnable provider (NOT_CONFIGURED: {not_configured})")

    n_rows = max(1, (limit if limit is not None else budget.max_requests_per_run // len(runnable)))
    cutoff: datetime | None = None
    rows_for_eval = {}
    if track == "historical":
        if split not in ("validation", "train"):
            raise ValueError("split must be 'validation' or 'train' (final-test is locked)")
        ref = resolve_dataset(root / data_cfg.processed_dir)
        feats = load_features(root, ref, model_cfg.feature_version)
        seasons = list(eval_cfg.validation_seasons if split == "validation" else eval_cfg.train_seasons)
        ctx = make_context(EvalMode.VALIDATION, eval_cfg)
        rows = sample_rows(load_rows(ref, ctx, seasons, feats), n_rows)
        rows_for_eval = {r.fixture_id: r for r in rows}
        data_version, feature_version = ref.data_version, model_cfg.feature_version
        exp_track = ExperimentType.HISTORICAL_BACKTEST
        meta = {r.fixture_id: CallContext(r.league_id, r.season, r.kickoff_utc) for r in rows}
        extra_note = "HISTORICAL replay: the LLM may have memorized these results (memorization risk)."
    elif track == "prospective":
        bundle = load_upcoming_rows(root, league, None, n_rows)
        rows, cutoff = bundle.rows, bundle.now
        data_version, feature_version = bundle.data_version, bundle.feature_version
        exp_track = ExperimentType.PROSPECTIVE
        meta = {r.fixture_id: CallContext(r.league_id, r.season, r.kickoff_utc) for r in rows}
        extra_note = (
            f"PROSPECTIVE: cutoff {cutoff.isoformat()}; unresolved teams skipped: {bundle.skipped_unresolved}"
        )
    else:
        raise ValueError("track must be 'historical' or 'prospective'")

    plan_items = []
    for r in rows:
        c = cutoff or r.kickoff_utc
        tok = estimate_input_tokens(SYSTEM_PROMPT, build_user_prompt(build_snapshot(r, c)))
        plan_items += [(n, e.model, tok) for n, e in runnable]
    exposure = preflight(plan_items, budget, prices)  # BudgetExceeded -> nothing is called
    plan = Plan(
        len(rows), exposure.request_count, len(runnable), len({(n, e.model) for n, e in runnable}),
        exposure.max_cost_usd, exposure.token_budget, [n for n, _ in runnable], not_configured, track,
    )  # fmt: skip
    print("\n".join(plan.lines()))
    if not allow_real_calls:
        print("REAL_CALLS_DISABLED_BY_OPERATOR: set ALLOW_REAL_LLM_CALLS=true. No API call was made; "
              "the benchmark is NOT complete.", file=sys.stderr)  # fmt: skip
        return None, 4

    now = datetime.now(UTC)
    out_dir = root / "artifacts" / "llm_runs" / f"benchmark_{track}_{data_version}_{now:%Y%m%dT%H%M%SZ}"
    out_dir.mkdir(parents=True)
    ledger = PredictionLedger()
    preds, pairs, response_lines = [], [], []
    for name, entry in runnable:
        results = run_llm_benchmark(
            rows, PROVIDERS[name], entry.model, cfg.api_key(name), exp_track, budget, prices,
            cutoff_offset_hours, feature_version, data_version, cutoff,
        )  # fmt: skip
        for res in results:
            pairs.append((meta[res.call.fixture_id], res.call))
            if res.prediction is not None:
                ledger.append(res.prediction)
                preds.append(res.prediction)
            if res.raw_text is not None:
                response_lines.append(json.dumps({
                    "fixture_id": res.call.fixture_id, "provider": name, "model": entry.model,
                    "request_id": res.call.request_id,
                    "raw_response_sha256": res.call.raw_response_sha256, "text": res.raw_text,
                }, sort_keys=True))  # fmt: skip

    calls_text = "\n".join(c.model_dump_json() for _, c in pairs)
    pred_text = dump_records(preds)
    (out_dir / "predictions.jsonl").write_text(pred_text + "\n" if preds else "", encoding="utf-8")
    (out_dir / "calls.jsonl").write_text(calls_text + "\n", encoding="utf-8")
    (out_dir / "responses.jsonl").write_text("\n".join(response_lines) + "\n", encoding="utf-8")
    (out_dir / "plan.json").write_text(
        json.dumps({**plan.__dict__, "note": extra_note}, indent=2), encoding="utf-8"
    )

    coverage = build_coverage(pairs)
    coverage["not_configured"] = not_configured
    (out_dir / "coverage.json").write_text(json.dumps(coverage, indent=2, sort_keys=True), encoding="utf-8")
    (out_dir / "coverage.md").write_text(render_coverage_md(coverage), encoding="utf-8")

    if rows_for_eval:  # post-hoc scoring labels, kept apart from anything sent to a provider
        outcomes = {
            fid: {"outcome": r.outcome, "league_id": r.league_id, "season": r.season,
                  "kickoff_utc": r.kickoff_utc.isoformat()}
            for fid, r in rows_for_eval.items()
        }  # fmt: skip
        (out_dir / "outcomes.json").write_text(
            json.dumps(outcomes, indent=2, sort_keys=True), encoding="utf-8"
        )

    evaluation_md = "(no evaluation: prospective predictions are scored only after results exist)\n"
    if track == "historical" and preds:
        ev = evaluate_llm_predictions(
            preds, rows_for_eval, list(eval_cfg.metrics), eval_cfg.calibration_bins,
            eval_cfg.bootstrap_samples, eval_cfg.bootstrap_seed,
        )  # fmt: skip
        (out_dir / "evaluation.json").write_text(
            json.dumps(ev, indent=2, sort_keys=True, default=str), encoding="utf-8"
        )
        evaluation_md = ev["leaderboard_md"]
        (out_dir / "evaluation.md").write_text(evaluation_md, encoding="utf-8")

    total_cost = sum(c.cost_usd or 0.0 for _, c in pairs)
    unknown_cost = sum(c.cost_usd is None for _, c in pairs)
    summary = (
        f"# LLM benchmark ({track})\n\n{extra_note}\n\n"
        + "\n".join(f"- {line}" for line in plan.lines())
        + f"\n- predictions produced: {len(preds)} of {len(pairs)} requests\n"
        f"- estimated cost: ${total_cost:.6f} ({unknown_cost} call(s) without a cost estimate; "
        f"pricing {prices.version})\n\nSee coverage.md and evaluation.md. No single winner is declared.\n"
    )
    (out_dir / "summary.md").write_text(summary, encoding="utf-8")
    hashes = {
        "predictions": hashlib.sha256(pred_text.encode()).hexdigest(),
        "calls": hashlib.sha256(calls_text.encode()).hexdigest(),
    }
    (out_dir / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")
    print(summary)
    print(render_coverage_md(coverage))
    print(evaluation_md)
    return out_dir, 0


def main(argv: list[str] | None = None) -> int:
    configure_output()
    load_dotenv()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--track", default="historical", choices=["historical", "prospective"])
    p.add_argument("--providers", "--provider", dest="providers", default=None,
                   help="comma list; default: every enabled provider")  # fmt: skip
    p.add_argument("--limit", type=int, default=None, help="matches (rows); default fits the budget")
    p.add_argument("--league", default="PL", choices=sorted(LEAGUES), help="prospective track only")
    p.add_argument("--split", default="validation", help="historical track: validation|train")
    p.add_argument("--max-requests", type=int, default=None, help="can only lower the configured budget")
    p.add_argument("--max-cost", type=float, default=None, help="USD; can only lower the configured budget")
    p.add_argument("--concurrency", type=int, default=None)
    p.add_argument("--prompt-version", default=PROMPT_VERSION)
    p.add_argument("--cutoff-offset-hours", type=float, default=0.0)
    a = p.parse_args(argv)
    allow = os.environ.get("ALLOW_REAL_LLM_CALLS", "").lower() == "true"
    try:
        out_dir, rc = run_benchmark(
            Path(a.root), a.track, a.providers.split(",") if a.providers else None, a.limit, a.league,
            a.split, a.max_requests, a.max_cost, a.concurrency, a.prompt_version,
            a.cutoff_offset_hours, allow,
        )  # fmt: skip
    except (BudgetExceeded, CutoffViolation, ProviderNotConfigured, ValueError, RuntimeError, KeyError) as e:
        print(f"LLM BENCHMARK FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    if out_dir is not None:
        print(f"artifacts: {out_dir}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
