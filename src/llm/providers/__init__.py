"""Provider adapters (S8, ADR 0024). Every entry calls a REAL vendor API via its official SDK."""

from .anthropic_provider import AnthropicProvider
from .base import ErrorKind, LLMResponse, Provider, ProviderError
from .gemini_provider import GeminiProvider
from .groq_provider import GroqProvider
from .openai_provider import OpenAIProvider

PROVIDERS: dict[str, Provider] = {
    "openai": OpenAIProvider(),
    "anthropic": AnthropicProvider(),
    "gemini": GeminiProvider(),
    "groq": GroqProvider(),
}

__all__ = [
    "PROVIDERS",
    "AnthropicProvider",
    "ErrorKind",
    "GeminiProvider",
    "GroqProvider",
    "LLMResponse",
    "OpenAIProvider",
    "Provider",
    "ProviderError",
]
