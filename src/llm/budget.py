"""No-cost-surprise pre-flight (ADR 0024). Runs BEFORE any API call; if the worst-case exposure
exceeds the configured budget it raises, and nothing is called. Worst case assumes every request
is retried `retry_limit` times and every response uses the full `max_output_tokens`."""

from dataclasses import dataclass

from src.config import LLMBudget

from .pricing import PriceTable

CHARS_PER_TOKEN = 3.0  # deliberately pessimistic (real text is ~4); only used for the pre-flight


class BudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class Exposure:
    request_count: int
    max_attempts: int
    token_budget: int
    max_cost_usd: float


def estimate_input_tokens(*texts: str) -> int:
    return int(sum(len(t) for t in texts) / CHARS_PER_TOKEN) + 1


def preflight(
    plan: list[tuple[str, str, int]],  # (provider, model, estimated_input_tokens) per request
    budget: LLMBudget,
    prices: PriceTable,
) -> Exposure:
    if len(plan) > budget.max_requests_per_run:
        raise BudgetExceeded(f"{len(plan)} requests > max_requests_per_run={budget.max_requests_per_run}")
    attempts = 1 + budget.retry_limit
    tokens, cost = 0, 0.0
    for provider, model, in_tok in plan:
        if not prices.has(provider, model):
            raise BudgetExceeded(
                f"no price for {provider}:{model} in configs/pricing.yaml; cannot bound cost"
            )
        tokens += attempts * (in_tok + budget.max_output_tokens)
        est = prices.estimate(provider, model, attempts * in_tok, attempts * budget.max_output_tokens)
        cost += est or 0.0
    if tokens > budget.max_total_tokens:
        raise BudgetExceeded(f"worst-case {tokens} tokens > max_total_tokens={budget.max_total_tokens}")
    if cost > budget.max_estimated_cost_usd:
        raise BudgetExceeded(
            f"worst-case ${cost:.6f} > max_estimated_cost_usd=${budget.max_estimated_cost_usd}"
        )
    return Exposure(len(plan), attempts, tokens, cost)
