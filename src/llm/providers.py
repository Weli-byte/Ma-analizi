"""Provider adapters (S8): OpenAI, Anthropic, Google under one `Provider` interface.

Each adapter builds its own request body/headers and calls `_post_json` (a thin `urllib.request`
wrapper, not a vendor SDK -- keeps this project's dependency surface at zero new third-party
packages). Tests mock `_post_json` directly (per the sprint's own spec: "provider calls
testlerde mocklanmali") -- no network access and no API key are needed to exercise this module.

API keys are NEVER hardcoded; `ProviderConfig.api_key()` reads them from the environment
(`src/config/__init__.py::ProviderConfig`, already RESERVED for this sprint in S0-S3).
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Protocol

# Static, documented ESTIMATES (USD per 1K tokens) -- not fetched live, not exact; good enough
# for a research-budget order-of-magnitude figure, never presented as a billing-accurate cost.
# (prompt_rate, completion_rate) per provider:model.
COST_PER_1K_TOKENS_USD: dict[str, tuple[float, float]] = {
    "openai:gpt-4o": (0.0025, 0.010),
    "openai:gpt-4o-mini": (0.00015, 0.0006),
    "anthropic:claude-3-5-sonnet-latest": (0.003, 0.015),
    "anthropic:claude-3-5-haiku-latest": (0.0008, 0.004),
    "google:gemini-1.5-pro": (0.00125, 0.005),
    "google:gemini-1.5-flash": (0.000075, 0.0003),
}


class ProviderError(RuntimeError):
    """The provider call itself failed (network/HTTP/auth) -- distinct from a malformed BODY,
    which is `llm.parse.MalformedLLMOutput`."""


@dataclass(frozen=True)
class LLMResponse:
    text: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float


class Provider(Protocol):
    name: str

    def complete(self, prompt: str, model: str, api_key: str) -> LLMResponse: ...


def _scrubbed(url: str) -> str:
    """Strip the query string before a URL ever reaches an exception message -- a provider API
    key must never be reflected into a log/traceback (Google's adapter puts its key in `?key=`)."""
    return urllib.parse.urlparse(url)._replace(query="").geturl()


def _post_json(url: str, headers: dict[str, str], body: dict, timeout: float = 30.0) -> dict:
    """Isolated so tests monkeypatch exactly this, never the real network."""
    req = urllib.request.Request(  # noqa: S310 - fixed https URLs, not user input
        url, data=json.dumps(body).encode(), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return json.loads(resp.read().decode())
    except urllib.error.URLError as e:
        raise ProviderError(f"{_scrubbed(url)}: {e}") from e


def cost_usd(provider: str, model: str, prompt_tokens: int, completion_tokens: int) -> float:
    rates = COST_PER_1K_TOKENS_USD.get(f"{provider}:{model}")
    if rates is None:
        return 0.0  # unknown model: no fabricated cost (explicit 0, not a guess)
    prompt_rate, completion_rate = rates
    return round(prompt_tokens / 1000 * prompt_rate + completion_tokens / 1000 * completion_rate, 6)


class OpenAIProvider:
    name = "openai"

    def complete(self, prompt: str, model: str, api_key: str) -> LLMResponse:
        t0 = time.monotonic()
        data = _post_json(
            "https://api.openai.com/v1/chat/completions",
            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0},
        )
        latency_ms = (time.monotonic() - t0) * 1000
        usage = data.get("usage", {})
        return LLMResponse(
            text=data["choices"][0]["message"]["content"],
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            latency_ms=latency_ms,
        )


class AnthropicProvider:
    name = "anthropic"

    def complete(self, prompt: str, model: str, api_key: str) -> LLMResponse:
        t0 = time.monotonic()
        data = _post_json(
            "https://api.anthropic.com/v1/messages",
            {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            {"model": model, "max_tokens": 1024, "messages": [{"role": "user", "content": prompt}]},
        )
        latency_ms = (time.monotonic() - t0) * 1000
        usage = data.get("usage", {})
        return LLMResponse(
            text=data["content"][0]["text"],
            prompt_tokens=int(usage.get("input_tokens", 0)),
            completion_tokens=int(usage.get("output_tokens", 0)),
            latency_ms=latency_ms,
        )


class GoogleProvider:
    name = "google"

    def complete(self, prompt: str, model: str, api_key: str) -> LLMResponse:
        t0 = time.monotonic()
        data = _post_json(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            {"Content-Type": "application/json", "x-goog-api-key": api_key},
            {"contents": [{"parts": [{"text": prompt}]}]},
        )
        latency_ms = (time.monotonic() - t0) * 1000
        usage = data.get("usageMetadata", {})
        return LLMResponse(
            text=data["candidates"][0]["content"]["parts"][0]["text"],
            prompt_tokens=int(usage.get("promptTokenCount", 0)),
            completion_tokens=int(usage.get("candidatesTokenCount", 0)),
            latency_ms=latency_ms,
        )


PROVIDERS: dict[str, Provider] = {
    "openai": OpenAIProvider(),
    "anthropic": AnthropicProvider(),
    "google": GoogleProvider(),
}
