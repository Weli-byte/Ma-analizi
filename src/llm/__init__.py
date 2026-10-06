"""S8: LLM 1X2 probability benchmark (OpenAI/Anthropic/Gemini under one interface, ADR 0024).

Scope boundary (CLAUDE.md non-negotiable rules): the snapshot this package serializes is built
from already leakage-safe `EvalRow.features` (produced by `src.features`, which already enforces
`information_cutoff`) -- this package adds NO new feature computation; `build_snapshot` only ever
drops fields (outcome, goals), never adds history. Every provider is a REAL API adapter; there is
no mock/fake provider anywhere in the product.
"""

from .contract import ForecastOutput, MalformedLLMOutput, parse_forecast
from .prompt import PROMPT_ID, PROMPT_VERSION, SYSTEM_PROMPT, build_user_prompt
from .providers import (
    PROVIDERS,
    AnthropicProvider,
    GeminiProvider,
    LLMResponse,
    OpenAIProvider,
    Provider,
    ProviderError,
)
from .runner import ProviderNotConfigured, resolve_provider, run_llm_benchmark
from .snapshot import build_snapshot

__all__ = [
    "PROMPT_ID",
    "PROMPT_VERSION",
    "PROVIDERS",
    "SYSTEM_PROMPT",
    "AnthropicProvider",
    "ForecastOutput",
    "GeminiProvider",
    "LLMResponse",
    "MalformedLLMOutput",
    "OpenAIProvider",
    "Provider",
    "ProviderError",
    "ProviderNotConfigured",
    "build_snapshot",
    "build_user_prompt",
    "parse_forecast",
    "resolve_provider",
    "run_llm_benchmark",
]
