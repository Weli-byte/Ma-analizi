"""REAL OpenAI adapter: official `openai` SDK, Responses API (the current API; Assistants is
sunset). Structured output via `text.format = json_schema (strict)`. SDK-internal retries are
disabled (`max_retries=0`) so OUR retry policy is the only one and `retry_count` is truthful."""

import time
from datetime import UTC, datetime

import openai

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
    if isinstance(e, openai.APITimeoutError):
        return ProviderError(ErrorKind.TIMEOUT, scrub(str(e), api_key))
    if isinstance(e, openai.APIConnectionError):
        return ProviderError(ErrorKind.NETWORK, scrub(str(e), api_key))
    if isinstance(e, openai.APIStatusError):
        kind = classify_status(e.status_code)
        body = e.body if isinstance(e.body, dict) else {}
        if "content_filter" in str(body.get("code", "")) or "safety" in str(body.get("code", "")):
            kind = ErrorKind.CONTENT_POLICY
        return ProviderError(
            kind,
            scrub(str(e.message), api_key),
            status_code=e.status_code,
            request_id=e.request_id,
            retry_after_s=retry_after_from(e),
        )
    return ProviderError(ErrorKind.UNKNOWN, f"{type(e).__name__}: {scrub(str(e), api_key)}")


class OpenAIProvider:
    name = "openai"

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
        client = openai.OpenAI(api_key=api_key, timeout=timeout_s, max_retries=0)

        def once() -> LLMResponse:
            t0 = time.monotonic()
            try:
                resp = client.responses.create(
                    model=model,
                    instructions=system,
                    input=user,
                    max_output_tokens=max_output_tokens,
                    store=False,
                    text={
                        "format": {
                            "type": "json_schema",
                            "name": "football_forecast",
                            "schema": response_json_schema(),
                            "strict": True,
                        }
                    },
                )
            except Exception as e:  # noqa: BLE001 - translated into the taxonomy, never swallowed
                raise _translate(e, api_key) from None
            latency_ms = (time.monotonic() - t0) * 1000
            request_id = getattr(resp, "_request_id", None)
            if resp.status == "incomplete":
                reason = getattr(resp.incomplete_details, "reason", None)
                kind = ErrorKind.CONTENT_POLICY if reason == "content_filter" else ErrorKind.SCHEMA
                raise ProviderError(kind, f"response incomplete: {reason}", request_id=request_id)
            text = resp.output_text
            if not text:
                raise ProviderError(
                    ErrorKind.CONTENT_POLICY, "empty output (refusal?)", request_id=request_id
                )
            usage = resp.usage
            return LLMResponse(
                provider=self.name,
                model=resp.model or model,
                text=text,
                created_at_utc=datetime.now(UTC),
                latency_ms=latency_ms,
                request_id=request_id,
                input_tokens=getattr(usage, "input_tokens", None),
                output_tokens=getattr(usage, "output_tokens", None),
                total_tokens=getattr(usage, "total_tokens", None),
                raw_response_hash=sha256_text(resp.model_dump_json()),
            )

        result, retries = with_retries(once, retry_limit=retry_limit)
        return LLMResponse(**{**result.__dict__, "retry_count": retries})
