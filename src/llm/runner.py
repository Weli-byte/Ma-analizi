"""S8 runner: snapshot -> prompt -> provider call (retry on malformed JSON) -> (PredictionRecord,
LLMCallRecord) pair per fixture.

Two timestamps matter and must not be confused (this is exactly the gap S10 formally locks down;
S8's job is to report it honestly, not to prevent it):
- `PredictionRecord.generated_at` follows the SAME backtest convention every other model in this
  repo uses (`run_baselines.py::_predictions`): `= information_cutoff`, a synthetic "as of" time,
  so the record validates (`generated_at <= kickoff_utc`) and metrics compare like with like.
- `LLMCallRecord.generated_at` is the REAL wall-clock time the response was received. For a
  HISTORICAL_BACKTEST track this is necessarily long after `kickoff_utc` -- the LLM's training
  data may already contain this match's real result (memorization risk). `track` on every
  record makes this queryable; it is never hidden.
"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from src.evaluation.dataset import EvalRow
from src.schemas import ExperimentType, LLMCallRecord, PredictionRecord

from .parse import MalformedLLMOutput, parse_llm_output
from .prompt import PROMPT_VERSION, build_prompt, prompt_hash, snapshot_hash
from .providers import PROVIDERS, Provider, ProviderError, cost_usd
from .snapshot import build_snapshot


class ProviderNotConfigured(RuntimeError):
    pass


def resolve_provider(provider_cfg, name: str) -> tuple[Provider, str, str]:
    """`configs/provider.yaml` (`ProviderConfig`, RESERVED for S8 since S0-S3) -> (adapter,
    model string, API key). Raises if disabled or the env var it names holds nothing -- a
    missing key is never silently treated as "skip this provider"."""
    if name not in provider_cfg.providers:
        raise ProviderNotConfigured(f"no provider.yaml entry for {name!r}")
    entry = provider_cfg.providers[name]
    if not entry.enabled:
        raise ProviderNotConfigured(f"provider {name!r} is disabled in provider.yaml")
    api_key = provider_cfg.api_key(name)
    if not api_key:
        raise ProviderNotConfigured(
            f"provider {name!r} enabled but ${entry.api_key_env} is unset in the environment"
        )
    return PROVIDERS[name], entry.model, api_key

RUNNER_VERSION = "1.0.0"  # PredictionRecord.model_version must be semver; the provider's own
# model string (gpt-4o, claude-3-5-sonnet-latest, ...) goes into model_id instead (slugified)
# and is kept verbatim on LLMCallRecord.model, which has no such regex constraint.


def _slug(model: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in model.lower())


@dataclass(frozen=True)
class LLMBenchmarkResult:
    prediction: PredictionRecord | None  # None iff status != "ok"
    call: LLMCallRecord


def _response_sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def run_one(
    row: EvalRow,
    provider: Provider,
    model: str,
    api_key: str,
    track: ExperimentType,
    cutoff_offset_hours: float = 0.0,
    max_retries: int = 2,
    feature_version: str = "fv0",
    data_version: str = "dv-000000000000",
) -> LLMBenchmarkResult:
    cutoff = row.kickoff_utc - timedelta(hours=cutoff_offset_hours)
    snapshot = build_snapshot(row, cutoff)
    prompt = build_prompt(snapshot)
    s_hash, p_hash = snapshot_hash(snapshot), prompt_hash(prompt)

    retries = 0
    exhausted = False
    while True:
        try:
            resp = provider.complete(prompt, model, api_key)
        except ProviderError as e:
            call = LLMCallRecord(
                fixture_id=row.fixture_id,
                provider=provider.name,
                model=model,
                prompt_version=PROMPT_VERSION,
                prompt_hash=p_hash,
                snapshot_hash=s_hash,
                information_cutoff=cutoff,
                generated_at=datetime.now(UTC),
                track=track,
                status="provider_error",
                retries=retries,
                latency_ms=0.0,
                prompt_tokens=0,
                completion_tokens=0,
                cost_usd=0.0,
                raw_response_sha256=_response_sha256(str(e)),
            )
            return LLMBenchmarkResult(None, call)
        try:
            parsed = parse_llm_output(resp.text)
            break
        except MalformedLLMOutput:
            if retries >= max_retries:
                exhausted = True
                break
            retries += 1
    if exhausted:
        call = LLMCallRecord(
            fixture_id=row.fixture_id,
            provider=provider.name,
            model=model,
            prompt_version=PROMPT_VERSION,
            prompt_hash=p_hash,
            snapshot_hash=s_hash,
            information_cutoff=cutoff,
            generated_at=datetime.now(UTC),
            track=track,
            status="malformed_json_exhausted",
            retries=retries,
            latency_ms=resp.latency_ms,
            prompt_tokens=resp.prompt_tokens,
            completion_tokens=resp.completion_tokens,
            cost_usd=cost_usd(provider.name, model, resp.prompt_tokens, resp.completion_tokens),
            raw_response_sha256=_response_sha256(resp.text),
        )
        return LLMBenchmarkResult(None, call)

    call = LLMCallRecord(
        fixture_id=row.fixture_id,
        provider=provider.name,
        model=model,
        prompt_version=PROMPT_VERSION,
        prompt_hash=p_hash,
        snapshot_hash=s_hash,
        information_cutoff=cutoff,
        generated_at=datetime.now(UTC),
        track=track,
        status="ok",
        retries=retries,
        latency_ms=resp.latency_ms,
        prompt_tokens=resp.prompt_tokens,
        completion_tokens=resp.completion_tokens,
        cost_usd=cost_usd(provider.name, model, resp.prompt_tokens, resp.completion_tokens),
        raw_response_sha256=_response_sha256(resp.text),
        confidence=parsed["confidence"],
        short_reasoning=parsed["short_reasoning"],
    )
    # S10 (ADR 0021): PROSPECTIVE uses the REAL call time, not the backtest cutoff -- this is
    # the structural post-kickoff guard. If a live call actually happens after kickoff (the
    # match already started), PredictionRecord's own `generated_at <= kickoff_utc` validator
    # rejects it below, refusing to even construct the record, not just refusing to publish it.
    prediction_generated_at = call.generated_at if track == ExperimentType.PROSPECTIVE else cutoff
    try:
        prediction = PredictionRecord(
            fixture_id=row.fixture_id,
            model_id=f"llm_{provider.name}_{_slug(model)}",
            model_version=RUNNER_VERSION,
            feature_version=feature_version,
            data_version=data_version,
            kickoff_utc=row.kickoff_utc,
            information_cutoff=cutoff,
            generated_at=prediction_generated_at,
            p_home=parsed["p_home"],
            p_draw=parsed["p_draw"],
            p_away=parsed["p_away"],
        )
    except ValidationError:
        rejected_call = call.model_copy(update={"status": "post_kickoff_rejected"})
        return LLMBenchmarkResult(None, rejected_call)
    return LLMBenchmarkResult(prediction, call)


def run_llm_benchmark(
    rows: list[EvalRow],
    provider: Provider,
    model: str,
    api_key: str,
    track: ExperimentType,
    cutoff_offset_hours: float = 0.0,
    max_retries: int = 2,
    feature_version: str = "fv0",
    data_version: str = "dv-000000000000",
) -> list[LLMBenchmarkResult]:
    return [
        run_one(
            row, provider, model, api_key, track, cutoff_offset_hours, max_retries,
            feature_version, data_version,
        )  # fmt: skip
        for row in rows
    ]
