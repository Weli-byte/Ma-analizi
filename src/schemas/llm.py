"""S8: per-call metadata for an LLM 1X2 forecast. Deliberately SEPARATE from `PredictionRecord`
(the probability itself still becomes an ordinary `PredictionRecord`, model_id=f"llm_{provider}",
so it flows through the same ledger/metrics as every other model) -- this record carries the
volatile call-level facts (latency, tokens, cost, retries) that must never be part of a
prediction's content hash.
"""

from typing import Literal, Self

from pydantic import Field, model_validator

from .common import ExperimentType, ImmutableModel, UtcDatetime


class LLMCallRecord(ImmutableModel):
    fixture_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)  # "openai" | "anthropic" | "gemini"
    model: str = Field(min_length=1)  # provider's model string, e.g. "gpt-4o"
    prompt_version: str = Field(min_length=1)
    prompt_hash: str = Field(min_length=1)  # sha256 of the exact prompt text sent
    snapshot_hash: str = Field(min_length=1)  # sha256 of the structured snapshot (pre-prompt)
    information_cutoff: UtcDatetime
    generated_at: UtcDatetime  # when the response was actually received
    track: ExperimentType  # HISTORICAL_BACKTEST (replay) vs PROSPECTIVE (forward-only, real time)
    status: Literal["ok", "malformed_json_exhausted", "provider_error", "post_kickoff_rejected"]
    retries: int = Field(ge=0, default=0)
    latency_ms: float | None = Field(default=None, ge=0.0)  # None when no call was made
    prompt_tokens: int | None = Field(default=None, ge=0)  # None = provider did not report it
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    # ESTIMATE from configs/pricing.yaml (`pricing_version`); None when unknown -- never a fake 0
    cost_usd: float | None = Field(default=None, ge=0.0)
    pricing_version: str | None = None
    request_id: str | None = None  # provider-side request id when the API returned one
    error_kind: str | None = None  # ErrorKind value for provider_error / malformed outputs
    prompt_id: str | None = None
    system_prompt_hash: str | None = None
    schema_version: str | None = None
    raw_response_sha256: str | None = Field(default=None, min_length=1)  # None when no response exists
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)  # model's own self-report
    short_reasoning: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.generated_at < self.information_cutoff:
            raise ValueError("generated_at must not precede information_cutoff")
        return self
