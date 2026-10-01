"""S8: LLM 1X2 probability benchmark (OpenAI/Anthropic/Google under one interface).

Scope boundary (CLAUDE.md non-negotiable rules): the snapshot this package serializes is built
from already leakage-safe `EvalRow.features` (produced by `src.features`, which already enforces
`information_cutoff`) -- this package adds NO new feature computation and performs NO leakage
check of its own; `build_snapshot` only ever drops fields (outcome, goals), never adds history.
"""

from .parse import MalformedLLMOutput, parse_llm_output
from .prompt import PROMPT_VERSION, build_prompt
from .providers import (
    COST_PER_1K_TOKENS_USD,
    AnthropicProvider,
    GoogleProvider,
    LLMResponse,
    OpenAIProvider,
    Provider,
    ProviderError,
)
from .runner import ProviderNotConfigured, resolve_provider, run_llm_benchmark
from .snapshot import build_snapshot

__all__ = [
    "COST_PER_1K_TOKENS_USD",
    "PROMPT_VERSION",
    "AnthropicProvider",
    "GoogleProvider",
    "LLMResponse",
    "MalformedLLMOutput",
    "OpenAIProvider",
    "Provider",
    "ProviderError",
    "ProviderNotConfigured",
    "build_prompt",
    "build_snapshot",
    "parse_llm_output",
    "resolve_provider",
    "run_llm_benchmark",
]
