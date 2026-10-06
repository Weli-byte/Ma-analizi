"""Coverage report for an LLM benchmark (ADR 0026): per provider, model, league, season and stage,
how many requests produced a usable prediction and how many FAILED. Failed requests are never
dropped -- every attempted (fixture, provider) pair is counted, so a benchmark cannot silently
look better than it was."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from src.schemas import LLMCallRecord


@dataclass(frozen=True)
class CallContext:
    league_id: str
    season: str
    kickoff_utc: datetime


def stage_label(ctx: CallContext, call: LLMCallRecord) -> str:
    """Hours between the information cutoff and kickoff, e.g. `T-0h` (historical replay at
    kickoff) or `T-192h` (a prospective forecast eight days ahead)."""
    hours = round((ctx.kickoff_utc - call.information_cutoff).total_seconds() / 3600)
    return f"T-{hours}h"


def _bucket() -> dict:
    return {"requests": 0, "success": 0, "failed": 0, "statuses": defaultdict(int)}


def _finish(b: dict) -> dict:
    n = b["requests"]
    return {
        "requests": n,
        "success": b["success"],
        "failed": b["failed"],
        "coverage": round(b["success"] / n, 4) if n else 0.0,
        "failure_rate": round(b["failed"] / n, 4) if n else 0.0,
        "statuses": dict(sorted(b["statuses"].items())),
    }


def build_coverage(pairs: list[tuple[CallContext, LLMCallRecord]]) -> dict:
    dims = ("provider", "model", "league", "season", "stage")
    buckets: dict[str, dict[str, dict]] = {d: defaultdict(_bucket) for d in dims}
    by_provider_model: dict[str, dict] = defaultdict(_bucket)
    for ctx, call in pairs:
        keys = {
            "provider": call.provider,
            "model": f"{call.provider}:{call.model}",
            "league": ctx.league_id,
            "season": ctx.season,
            "stage": stage_label(ctx, call),
        }
        ok = call.status == "ok"
        for dim, key in keys.items():
            b = buckets[dim][key]
            b["requests"] += 1
            b["success" if ok else "failed"] += 1
            b["statuses"][call.status] += 1
        pm = by_provider_model[f"{call.provider}:{call.model}"]
        pm["requests"] += 1
        pm["success" if ok else "failed"] += 1
        pm["statuses"][call.status] += 1
    return {
        "total_requests": len(pairs),
        **{f"by_{d}": {k: _finish(v) for k, v in sorted(buckets[d].items())} for d in dims},
    }


def render_coverage_md(report: dict) -> str:
    lines = ["# LLM benchmark coverage", "", f"Total requests attempted: {report['total_requests']}", ""]
    for dim in ("provider", "model", "league", "season", "stage"):
        lines += [f"## by {dim}", "", "| key | requests | success | failed | coverage | failure rate |",
                  "|---|---|---|---|---|---|"]  # fmt: skip
        for key, b in report[f"by_{dim}"].items():
            lines.append(
                f"| {key} | {b['requests']} | {b['success']} | {b['failed']} | "
                f"{b['coverage']:.1%} | {b['failure_rate']:.1%} |"
            )
        lines.append("")
    lines.append("Failed requests are counted, never dropped; no value is substituted for them.")
    return "\n".join(lines) + "\n"
