"""REAL Groq adapter: official `groq` SDK, chat.completions with strict `json_schema` structured
output (supported by openai/gpt-oss-20b|120b). Free-tier friendly: low rate limits surface as 429
and are retried with backoff by `with_retries`. SDK-internal retries are disabled."""

import time
from datetime import UTC, datetime

import groq

from src.llm.contract import response_json_schema

from .base import (
    ErrorKind,
    LLMResponse,
    ProviderError,
    classify_status,
    retry_after_from,
    scrub,
    sha256_text,
    with_retries,
)


def _translate(e: Exception, api_key: str) -> ProviderError:
    if isinstance(e, groq.APITimeoutError):
        return ProviderError(ErrorKind.TIMEOUT, scrub(str(e), api_key))
    if isinstance(e, groq.APIConnectionError):
        return ProviderError(ErrorKind.NETWORK, scrub(str(e), api_key))
    if isinstance(e, groq.APIStatusError):
        return ProviderError(
            classify_status(e.status_code),
            scrub(str(e.message), api_key),
            status_code=e.status_code,
            request_id=getattr(e, "request_id", None),
            retry_after_s=retry_after_from(e),
        )
    return ProviderError(ErrorKind.UNKNOWN, f"{type(e).__name__}: {scrub(str(e), api_key)}")


class GroqProvider:
    name = "groq"

    def complete(
        self,
        system: str,
        user: str,
        *,
        model: str,
        api_key: str,
        timeout_s: float,
        max_output_tokens: int,
        retry_limit: int,
    ) -> LLMResponse:
        client = groq.Groq(api_key=api_key, timeout=timeout_s, max_retries=0)

        def once() -> LLMResponse:
            t0 = time.monotonic()
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    max_completion_tokens=max_output_tokens,
                    reasoning_effort="low",
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": "football_forecast",
                            "strict": True,
                            "schema": response_json_schema(),
                        },
                    },
                )
            except Exception as e:  # noqa: BLE001 - translated into the taxonomy, never swallowed
                raise _translate(e, api_key) from None
            latency_ms = (time.monotonic() - t0) * 1000
            request_id = getattr(resp, "_request_id", None) or getattr(resp, "id", None)
            choice = resp.choices[0]
            if choice.finish_reason == "length":
                raise ProviderError(ErrorKind.SCHEMA, "output truncated (length)", request_id=request_id)
            text = choice.message.content
            if not text:
                raise ProviderError(
                    ErrorKind.CONTENT_POLICY,
                    f"empty output, finish_reason={choice.finish_reason}",
                    request_id=request_id,
                )
            u = resp.usage
            return LLMResponse(
                provider=self.name,
                model=resp.model or model,
                text=text,
                created_at_utc=datetime.now(UTC),
                latency_ms=latency_ms,
                request_id=request_id,
                input_tokens=getattr(u, "prompt_tokens", None),
                output_tokens=getattr(u, "completion_tokens", None),
                total_tokens=getattr(u, "total_tokens", None),
                raw_response_hash=sha256_text(resp.model_dump_json()),
            )

        result, retries = with_retries(once, retry_limit=retry_limit)
        return LLMResponse(**{**result.__dict__, "retry_count": retries})
