"""S8 runner: snapshot -> pre-call audit -> budget pre-flight -> REAL provider call (bounded
retries) -> (PredictionRecord, LLMCallRecord) per fixture. See ADR 0019 / 0021 / 0024.

Two timestamps matter and must not be confused:
- `PredictionRecord.generated_at` follows the SAME backtest convention every other model in this
  repo uses for HISTORICAL_BACKTEST (`= information_cutoff`); for PROSPECTIVE it is the REAL call
  time, so a post-kickoff prediction cannot even be constructed.
- `LLMCallRecord.generated_at` is the REAL wall-clock time the response was received. For a
  HISTORICAL_BACKTEST this is long after kickoff -- the LLM may already know the result
  (memorization risk). `track` makes this queryable; it is never hidden.

Failure policy (ADR 0024): a failed provider call yields NO prediction (status provider_error /
malformed_json_exhausted / post_kickoff_rejected). Nothing is substituted -- not a random value,
not an older prediction, not another provider.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from src.config import LLMBudget
from src.evaluation.dataset import EvalRow
from src.schemas import ExperimentType, LLMCallRecord, PredictionRecord

from .budget import estimate_input_tokens, preflight
from .contract import MalformedLLMOutput, parse_forecast
from .pricing import PriceTable, load_price_table
from .prompt import SYSTEM_PROMPT, build_user_prompt, prompt_meta, snapshot_hash
from .providers import PROVIDERS, ErrorKind, Provider, ProviderError
from .snapshot import CutoffViolation, audit_snapshot, build_snapshot


class ProviderNotConfigured(RuntimeError):
    pass


def resolve_provider(provider_cfg, name: str) -> tuple[Provider, str, str]:
    """`configs/provider.yaml` -> (adapter, model string, API key). Raises if disabled, if the
    model is still `TBD`, or if the env var it names holds nothing -- a missing key is never
    silently treated as "skip" and never replaced by another provider."""
    if name not in provider_cfg.providers:
        raise ProviderNotConfigured(f"no provider.yaml entry for {name!r}")
    entry = provider_cfg.providers[name]
    if not entry.enabled:
        raise ProviderNotConfigured(f"provider {name!r} is disabled in provider.yaml")
    if entry.model.upper() == "TBD":
        raise ProviderNotConfigured(f"provider {name!r} has no model configured (model: TBD)")
    api_key = provider_cfg.api_key(name)
    if not api_key:
        raise ProviderNotConfigured(
            f"provider {name!r} enabled but ${entry.api_key_env} is unset in the environment"
        )
    return PROVIDERS[name], entry.model, api_key


RUNNER_VERSION = "2.0.0"  # PredictionRecord.model_version must be semver; the provider's own
# model string goes into model_id (slugified) and verbatim on LLMCallRecord.model.


def _slug(model: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in model.lower())


@dataclass(frozen=True)
class LLMBenchmarkResult:
    prediction: PredictionRecord | None  # None iff status != "ok"
    call: LLMCallRecord
    raw_text: str | None = None  # the provider's actual response text (persisted by callers)


def run_one(
    row: EvalRow,
    provider: Provider,
    model: str,
    api_key: str,
    track: ExperimentType,
    budget: LLMBudget | None = None,
    prices: PriceTable | None = None,
    cutoff_offset_hours: float = 0.0,
    feature_version: str = "fv0",
    data_version: str = "dv-000000000000",
    information_cutoff: datetime | None = None,
) -> LLMBenchmarkResult:
    budget = budget or LLMBudget()
    prices = prices or load_price_table()
    cutoff = information_cutoff or row.kickoff_utc - timedelta(hours=cutoff_offset_hours)
    if cutoff > datetime.now(UTC):  # information cannot come from the future
        raise CutoffViolation(f"information_cutoff {cutoff} is in the future")
    snapshot = build_snapshot(row, cutoff)
    audit_snapshot(snapshot, row.kickoff_utc, cutoff)  # raises BEFORE any provider call
    user = build_user_prompt(snapshot)
    meta = prompt_meta(user)

    def record(status: str, **kw) -> LLMCallRecord:
        return LLMCallRecord(
            fixture_id=row.fixture_id,
            provider=provider.name,
            model=model,
            prompt_version=meta.prompt_version,
            prompt_hash=meta.user_prompt_hash,
            snapshot_hash=snapshot_hash(snapshot),
            information_cutoff=cutoff,
            generated_at=datetime.now(UTC),
            track=track,
            status=status,
            prompt_id=meta.prompt_id,
            system_prompt_hash=meta.system_prompt_hash,
            schema_version=meta.schema_version,
            pricing_version=prices.version,
            **kw,
        )

    # Phase 22: a PROSPECTIVE (pre-match) call at/after kickoff is refused BEFORE the provider is
    # contacted -- no money spent, no prediction. (Live forecasting is a separate, explicit mode.)
    if track == ExperimentType.PROSPECTIVE and datetime.now(UTC) >= row.kickoff_utc:
        return LLMBenchmarkResult(None, record("post_kickoff_rejected", retries=0))

    retries = 0
    while True:
        try:
            resp = provider.complete(
                SYSTEM_PROMPT, user, model=model, api_key=api_key,
                timeout_s=budget.request_timeout_seconds,
                max_output_tokens=budget.max_output_tokens,
                retry_limit=budget.retry_limit - retries,
            )  # fmt: skip
        except ProviderError as e:
            return LLMBenchmarkResult(
                None,
                record("provider_error", retries=retries + e.retry_count,
                       error_kind=e.kind.value, request_id=e.request_id),
            )  # fmt: skip
        retries += resp.retry_count
        try:
            out = parse_forecast(resp.text)
            break
        except MalformedLLMOutput:
            if retries >= budget.retry_limit:
                return LLMBenchmarkResult(
                    None, _call(record, resp, model, prices, retries, ErrorKind.SCHEMA), resp.text
                )
            retries += 1

    call = _call(record, resp, model, prices, retries, None, out)
    total = out.home_probability + out.draw_probability + out.away_probability
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
            p_home=out.home_probability / total,
            p_draw=out.draw_probability / total,
            p_away=out.away_probability / total,
        )
    except ValidationError:
        return LLMBenchmarkResult(
            None, call.model_copy(update={"status": "post_kickoff_rejected"}), resp.text
        )
    return LLMBenchmarkResult(prediction, call, resp.text)


def _call(
    record, resp, model: str, prices: PriceTable, retries: int, error: ErrorKind | None, out=None
) -> LLMCallRecord:
    return record(
        "ok" if error is None else "malformed_json_exhausted",
        retries=retries,
        latency_ms=resp.latency_ms,
        prompt_tokens=resp.input_tokens,
        completion_tokens=resp.output_tokens,
        total_tokens=resp.total_tokens,
        cost_usd=prices.estimate(resp.provider, model, resp.input_tokens, resp.output_tokens),
        request_id=resp.request_id,
        raw_response_sha256=resp.raw_response_hash,
        error_kind=error.value if error else None,
        confidence=out.confidence if out else None,
        short_reasoning=out.analysis_summary if out else None,
    )  # fmt: skip


def run_llm_benchmark(
    rows: list[EvalRow],
    provider: Provider,
    model: str,
    api_key: str,
    track: ExperimentType,
    budget: LLMBudget | None = None,
    prices: PriceTable | None = None,
    cutoff_offset_hours: float = 0.0,
    feature_version: str = "fv0",
    data_version: str = "dv-000000000000",
    information_cutoff: datetime | None = None,
) -> list[LLMBenchmarkResult]:
    """Budget pre-flight for the WHOLE run first (raises `BudgetExceeded` before any call)."""
    budget = budget or LLMBudget()
    prices = prices or load_price_table()
    plan = []
    for row in rows:
        cutoff = information_cutoff or row.kickoff_utc - timedelta(hours=cutoff_offset_hours)
        s = build_snapshot(row, cutoff)
        plan.append((provider.name, model, estimate_input_tokens(SYSTEM_PROMPT, build_user_prompt(s))))
    preflight(plan, budget, prices)

    def one(row: EvalRow) -> LLMBenchmarkResult:
        return run_one(
            row, provider, model, api_key, track, budget, prices, cutoff_offset_hours,
            feature_version, data_version, information_cutoff,
        )  # fmt: skip

    if budget.max_concurrency > 1:
        with ThreadPoolExecutor(max_workers=budget.max_concurrency) as pool:
            return list(pool.map(one, rows))
    return [one(r) for r in rows]
