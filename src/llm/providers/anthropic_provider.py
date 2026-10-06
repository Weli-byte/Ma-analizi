"""REAL Anthropic adapter: official `anthropic` SDK, Messages API with JSON-schema structured
output (`output_config.format`). Not yet verified live: no ANTHROPIC_API_KEY on this machine, so
status stays NOT_CONFIGURED until `python -m src.llm.live_smoke` gets a real response."""

import time
from datetime import UTC, datetime

import anthropic

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
    if isinstance(e, anthropic.APITimeoutError):
        return ProviderError(ErrorKind.TIMEOUT, scrub(str(e), api_key))
    if isinstance(e, anthropic.APIConnectionError):
        return ProviderError(ErrorKind.NETWORK, scrub(str(e), api_key))
    if isinstance(e, anthropic.APIStatusError):
        return ProviderError(
            classify_status(e.status_code), scrub(str(e.message), api_key),
            status_code=e.status_code, request_id=getattr(e, "request_id", None),
            retry_after_s=retry_after_from(e),
        )  # fmt: skip
    return ProviderError(ErrorKind.UNKNOWN, f"{type(e).__name__}: {scrub(str(e), api_key)}")


class AnthropicProvider:
    name = "anthropic"

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
        client = anthropic.Anthropic(api_key=api_key, timeout=timeout_s, max_retries=0)

        def once() -> LLMResponse:
            t0 = time.monotonic()
            try:
                resp = client.messages.create(
                    model=model,
                    system=system,
                    max_tokens=max_output_tokens,
                    messages=[{"role": "user", "content": user}],
                    output_config={"format": {"type": "json_schema", "schema": response_json_schema()}},
                )
            except Exception as e:  # noqa: BLE001 - translated into the taxonomy, never swallowed
                raise _translate(e, api_key) from None
            latency_ms = (time.monotonic() - t0) * 1000
            request_id = getattr(resp, "_request_id", None)
            if resp.stop_reason == "refusal":
                raise ProviderError(ErrorKind.CONTENT_POLICY, "model refused", request_id=request_id)
            if resp.stop_reason == "max_tokens":
                raise ProviderError(ErrorKind.SCHEMA, "output truncated (max_tokens)", request_id=request_id)
            text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
            if not text:
                raise ProviderError(ErrorKind.SCHEMA, "empty text output", request_id=request_id)
            in_tok, out_tok = resp.usage.input_tokens, resp.usage.output_tokens
            return LLMResponse(
                provider=self.name,
                model=resp.model or model,
                text=text,
                created_at_utc=datetime.now(UTC),
                latency_ms=latency_ms,
                request_id=request_id,
                input_tokens=in_tok,
                output_tokens=out_tok,
                total_tokens=in_tok + out_tok if in_tok is not None and out_tok is not None else None,
                raw_response_hash=sha256_text(resp.model_dump_json()),
            )

        result, retries = with_retries(once, retry_limit=retry_limit)
        return LLMResponse(**{**result.__dict__, "retry_count": retries})
