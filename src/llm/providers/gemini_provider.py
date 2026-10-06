"""REAL Gemini adapter: official `google-genai` SDK, `models.generate_content` with
`response_json_schema` structured output. The key travels via the SDK (header), never a URL.
Output tokens = total - prompt when the API reports a total (includes any thinking tokens, which
are billed as output); otherwise they are left `None`."""

import time
from datetime import UTC, datetime

import httpx

from src.llm.contract import response_json_schema

from .base import ErrorKind, LLMResponse, ProviderError, classify_status, scrub, sha256_text, with_retries


def _translate(e: Exception, api_key: str) -> ProviderError:
    from google.genai import errors

    if isinstance(e, httpx.TimeoutException):
        return ProviderError(ErrorKind.TIMEOUT, scrub(str(e), api_key))
    if isinstance(e, httpx.TransportError):
        return ProviderError(ErrorKind.NETWORK, scrub(str(e), api_key))
    if isinstance(e, errors.APIError):
        return ProviderError(
            classify_status(e.code), scrub(str(e.message or e.status or ""), api_key), status_code=e.code
        )
    return ProviderError(ErrorKind.UNKNOWN, f"{type(e).__name__}: {scrub(str(e), api_key)}")


class GeminiProvider:
    name = "gemini"

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
        # imported lazily: google-genai emits a DeprecationWarning at import time
        from google import genai
        from google.genai import types

        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=int(timeout_s * 1000), retry_options=None),
        )
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_json_schema=response_json_schema(),
            max_output_tokens=max_output_tokens,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

        def once() -> LLMResponse:
            t0 = time.monotonic()
            try:
                resp = client.models.generate_content(model=model, contents=user, config=config)
            except Exception as e:  # noqa: BLE001 - translated into the taxonomy, never swallowed
                raise _translate(e, api_key) from None
            latency_ms = (time.monotonic() - t0) * 1000
            request_id = getattr(resp, "response_id", None)
            if resp.prompt_feedback is not None and getattr(resp.prompt_feedback, "block_reason", None):
                raise ProviderError(
                    ErrorKind.CONTENT_POLICY, f"prompt blocked: {resp.prompt_feedback.block_reason}",
                    request_id=request_id,
                )  # fmt: skip
            cand = resp.candidates[0] if resp.candidates else None
            finish = str(getattr(cand, "finish_reason", "") or "")
            text = resp.text if cand is not None else None
            if not text:
                blocked = "SAFETY" in finish or "BLOCK" in finish
                kind = ErrorKind.CONTENT_POLICY if blocked else ErrorKind.SCHEMA
                detail = f"empty output, finish_reason={finish or 'unknown'}"
                raise ProviderError(kind, detail, request_id=request_id)
            if "MAX_TOKENS" in finish:
                raise ProviderError(ErrorKind.SCHEMA, "output truncated (MAX_TOKENS)", request_id=request_id)
            u = resp.usage_metadata
            in_tok = getattr(u, "prompt_token_count", None)
            total = getattr(u, "total_token_count", None)
            out_tok = total - in_tok if total is not None and in_tok is not None else None
            return LLMResponse(
                provider=self.name,
                model=resp.model_version or model,
                text=text,
                created_at_utc=datetime.now(UTC),
                latency_ms=latency_ms,
                request_id=request_id,
                input_tokens=in_tok,
                output_tokens=out_tok,
                total_tokens=total,
                raw_response_hash=sha256_text(resp.model_dump_json()),
            )

        result, retries = with_retries(once, retry_limit=retry_limit)
        return LLMResponse(**{**result.__dict__, "retry_count": retries})
