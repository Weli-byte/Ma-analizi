"""S8: LLM 1X2 benchmark -- OFFLINE tests only. There is NO mock provider: provider behaviour is
proven by REAL calls (`tests/integration/test_*_live.py`, `python -m src.llm.live_smoke`) and by
`REAL_PROVIDER_CAPTURE` fixtures recorded from them. Here: pure logic, schema, guards, budget."""

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.config import LLMBudget
from src.evaluation.dataset import EvalRow
from src.llm.budget import BudgetExceeded, preflight
from src.llm.contract import SCHEMA_VERSION, MalformedLLMOutput, parse_forecast, response_json_schema
from src.llm.pricing import load_price_table
from src.llm.prompt import PROMPT_VERSION, build_user_prompt, prompt_meta, snapshot_hash
from src.llm.providers import ErrorKind, ProviderError
from src.llm.providers.base import classify_status, scrub, with_retries
from src.llm.runner import ProviderNotConfigured, resolve_provider, run_llm_benchmark, run_one
from src.llm.snapshot import CutoffViolation, audit_snapshot, build_snapshot
from src.schemas import ExperimentType

T0 = datetime(2024, 3, 1, tzinfo=UTC)
PINNED_SYSTEM_PROMPT_HASH = "0b4300e32ad3ea08fa0c9889c2ea08a48ad66e77b98dbc6413aba111e4303c16"
CAPTURES = Path(__file__).parent / "fixtures" / "real_provider_captures"


def row(fixture_id="f1", outcome=0, feats=None, kickoff=T0):
    return EvalRow(
        fixture_id, "EPL", "2023-24", kickoff, "H", "A", outcome,
        features=feats or {"home_form_points_5": 10.0, "away_form_points_5": 4.0},
        odds={"closing:agg_avg": (2.0, 3.4, 3.8)},
    )  # fmt: skip


class MustNotBeCalled:
    """Not a provider stand-in: it only proves a guard fired BEFORE any provider contact."""

    name = "openai"

    def complete(self, *a, **k):
        raise AssertionError("provider was contacted")


# ------------------------------------------------------------------------- snapshot / prompt
def test_snapshot_never_carries_outcome_or_goals():
    r = row(outcome=2)
    snap = build_snapshot(r, T0)
    blob = json.dumps(snap)
    assert "outcome" not in blob and "goal" not in blob.lower()
    assert snap["permitted_historical_features"]["home_form_points_5"] == 10.0


def test_snapshot_omits_none_valued_features():
    r = row(feats={"home_form_points_5": None, "away_form_points_5": 4.0})
    snap = build_snapshot(r, T0)
    assert "home_form_points_5" not in snap["permitted_historical_features"]
    assert snap["permitted_historical_features"]["away_form_points_5"] == 4.0


def test_user_prompt_and_snapshot_hashes_are_deterministic():
    snap = build_snapshot(row(), T0)
    assert build_user_prompt(snap) == build_user_prompt(snap)
    assert snapshot_hash(snap) == snapshot_hash(build_snapshot(row(), T0))


def test_prompt_versioning_fields_and_pinned_system_prompt_hash():
    meta = prompt_meta(build_user_prompt(build_snapshot(row(), T0)))
    assert meta.prompt_id and meta.prompt_version == PROMPT_VERSION
    assert meta.schema_version == SCHEMA_VERSION
    # Editing SYSTEM_PROMPT changes this hash: bump PROMPT_VERSION, write an ADR, update the pin.
    assert meta.system_prompt_hash == PINNED_SYSTEM_PROMPT_HASH


# ------------------------------------------------------------------------------ contract
def load_capture(name: str) -> dict:
    """A REAL_PROVIDER_CAPTURE: recorded from an actual call (`live_smoke --capture`)."""
    cap = json.loads((CAPTURES / f"{name}.json").read_text(encoding="utf-8"))
    assert cap["label"] == "REAL_PROVIDER_CAPTURE"
    return cap


@pytest.mark.parametrize("name", ["openai", "gemini"])
def test_real_captured_responses_satisfy_the_contract(name):
    cap = load_capture(name)
    out = parse_forecast(cap["text"])
    total = out.home_probability + out.draw_probability + out.away_probability
    assert total == pytest.approx(1.0, abs=1e-3)
    assert cap["request_id"] and cap["input_tokens"] and cap["output_tokens"]
    assert cap["prompt_meta"]["schema_version"] == SCHEMA_VERSION


@pytest.mark.parametrize(
    "bad",
    [
        "not json at all",
        json.dumps({"home_probability": 0.5}),  # missing keys
        json.dumps({"home_probability": 1.5, "draw_probability": 0.0, "away_probability": 0.0}),
        json.dumps({"home_probability": 0.9, "draw_probability": 0.9, "away_probability": 0.9}),
        json.dumps({"home_probability": 0.5, "draw_probability": 0.3, "away_probability": 0.2, "confidence": 2.0}),
        json.dumps({"home_probability": 0.5, "draw_probability": 0.3, "away_probability": 0.2, "extra": 1}),
        "[1, 2, 3]",  # valid JSON, not an object
    ],
)  # fmt: skip
def test_contract_rejects_malformed_output(bad):
    with pytest.raises(MalformedLLMOutput):
        parse_forecast(bad)


def test_contract_tolerance_is_documented_and_never_exceeded():
    near = json.dumps({"home_probability": 0.6005, "draw_probability": 0.25, "away_probability": 0.15})
    assert parse_forecast(near).home_probability == 0.6005  # within 1e-3: accepted as returned
    far = json.dumps({"home_probability": 0.62, "draw_probability": 0.25, "away_probability": 0.15})
    with pytest.raises(MalformedLLMOutput):
        parse_forecast(far)


def test_provider_schema_is_strict_mode_compatible():
    schema = response_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])


# ------------------------------------------------------------- provider plumbing (pure logic)
@pytest.mark.parametrize(
    "code,kind",
    [(429, ErrorKind.RATE_LIMIT), (401, ErrorKind.AUTH), (403, ErrorKind.AUTH),
     (400, ErrorKind.INVALID_REQUEST), (404, ErrorKind.MODEL_UNAVAILABLE),
     (408, ErrorKind.TIMEOUT), (500, ErrorKind.SERVER), (503, ErrorKind.SERVER)],
)  # fmt: skip
def test_http_status_classification(code, kind):
    assert classify_status(code) == kind


def test_scrub_removes_key_shaped_strings():
    msg = scrub("bad key sk-abcdefghij1234 and AIzaSyABCDEFGHIJKL and url?key=zzz", "zzz")
    assert "sk-abcdefghij1234" not in msg and "AIza" not in msg and "zzz" not in msg


def test_retry_only_retryable_errors_with_backoff_and_counts():
    delays, state = [], {"n": 0}

    def flaky():
        state["n"] += 1
        if state["n"] < 3:
            raise ProviderError(ErrorKind.RATE_LIMIT, "429")
        return "done"

    result, retries = with_retries(flaky, retry_limit=3, sleep=delays.append)
    assert (result, retries) == ("done", 2)
    assert len(delays) == 2 and delays[1] > 0  # exponential backoff + jitter


def test_auth_error_is_never_retried():
    state = {"n": 0}

    def denied():
        state["n"] += 1
        raise ProviderError(ErrorKind.AUTH, "401", status_code=401)

    with pytest.raises(ProviderError):
        with_retries(denied, retry_limit=5, sleep=lambda s: None)
    assert state["n"] == 1


def test_retry_limit_is_enforced_and_reported():
    def down():
        raise ProviderError(ErrorKind.SERVER, "503")

    with pytest.raises(ProviderError) as e:
        with_retries(down, retry_limit=2, sleep=lambda s: None)
    assert e.value.retry_count == 2


# ------------------------------------------------------------------------ pricing / budget
def test_pricing_comes_from_central_table_and_unknown_is_none():
    prices = load_price_table()
    assert prices.version and prices.has("openai", "gpt-6-luna")
    assert prices.estimate("openai", "gpt-6-luna", 1_000_000, 1_000_000) == pytest.approx(0.60)
    assert prices.estimate("openai", "no-such-model", 10, 10) is None  # never a fabricated 0
    assert prices.estimate("openai", "gpt-6-luna", None, 10) is None  # unreported usage -> None


def test_budget_preflight_fails_before_any_call():
    prices = load_price_table()
    ok = preflight([("openai", "gpt-6-luna", 300)], LLMBudget(), prices)
    assert ok.request_count == 1 and ok.max_cost_usd < 0.01
    with pytest.raises(BudgetExceeded):
        preflight([("openai", "gpt-6-luna", 300)] * 4, LLMBudget(max_requests_per_run=3), prices)
    with pytest.raises(BudgetExceeded):
        preflight([("openai", "gpt-6-luna", 300)], LLMBudget(max_total_tokens=100), prices)
    with pytest.raises(BudgetExceeded):
        preflight([("openai", "gpt-6-luna", 300)], LLMBudget(max_estimated_cost_usd=1e-9), prices)
    with pytest.raises(BudgetExceeded):  # a model without a price cannot be cost-bounded
        preflight([("openai", "unpriced", 300)], LLMBudget(), prices)


def test_run_llm_benchmark_over_budget_makes_no_call():
    rows = [row(f"f{i}") for i in range(5)]
    with pytest.raises(BudgetExceeded):
        run_llm_benchmark(rows, MustNotBeCalled(), "gpt-6-luna", "key", ExperimentType.HISTORICAL_BACKTEST)


# ------------------------------------------------------------------------------- guards
def test_cutoff_audit_blocks_result_bearing_snapshot_before_any_call():
    snap = build_snapshot(row(), T0)
    audit_snapshot(snap, T0, T0)  # clean snapshot passes
    snap["permitted_historical_features"]["home_goals"] = 3  # leakage injected
    with pytest.raises(CutoffViolation):
        audit_snapshot(snap, T0, T0)
    later = T0 + timedelta(hours=1)
    with pytest.raises(CutoffViolation):  # cutoff after kickoff
        audit_snapshot(build_snapshot(row(), later), T0, later)


def test_prospective_post_kickoff_is_rejected_without_contacting_the_provider():
    result = run_one(row(), MustNotBeCalled(), "gpt-6-luna", "key", ExperimentType.PROSPECTIVE)
    assert result.prediction is None and result.call.status == "post_kickoff_rejected"


def test_llm_call_record_rejects_generated_at_before_cutoff():
    from pydantic import ValidationError

    from src.schemas import LLMCallRecord

    with pytest.raises(ValidationError):
        LLMCallRecord(
            fixture_id="f1", provider="openai", model="gpt-6-luna", prompt_version="v1",
            prompt_hash="a" * 8, snapshot_hash="b" * 8,
            information_cutoff=T0, generated_at=T0 - timedelta(hours=1),
            track=ExperimentType.PROSPECTIVE, status="ok", latency_ms=1.0,
        )  # fmt: skip


def test_llm_call_record_keeps_unreported_usage_as_none_not_zero():
    from src.schemas import LLMCallRecord

    rec = LLMCallRecord(
        fixture_id="f1", provider="gemini", model="m", prompt_version="v", prompt_hash="a" * 8,
        snapshot_hash="b" * 8, information_cutoff=T0, generated_at=T0,
        track=ExperimentType.HISTORICAL_BACKTEST, status="provider_error", error_kind="auth",
    )  # fmt: skip
    assert rec.prompt_tokens is None and rec.cost_usd is None and rec.latency_ms is None


# --------------------------------------------------------------------- provider config wiring
def test_resolve_provider_reads_provider_yaml(monkeypatch):
    from src.config import ProviderConfig

    monkeypatch.setenv("TEST_OPENAI_KEY", "sk-real")
    cfg = ProviderConfig(providers={"openai": {"enabled": True, "api_key_env": "TEST_OPENAI_KEY", "model": "gpt-6-luna"}})  # fmt: skip
    provider, model, api_key = resolve_provider(cfg, "openai")
    assert provider.name == "openai" and model == "gpt-6-luna" and api_key == "sk-real"


def test_resolve_provider_rejects_disabled_missing_key_and_tbd_model(monkeypatch):
    from src.config import ProviderConfig

    disabled = ProviderConfig(providers={"openai": {"enabled": False, "api_key_env": "X", "model": "m"}})
    with pytest.raises(ProviderNotConfigured):
        resolve_provider(disabled, "openai")

    monkeypatch.delenv("UNSET_KEY_VAR", raising=False)
    no_key = ProviderConfig(providers={"openai": {"enabled": True, "api_key_env": "UNSET_KEY_VAR", "model": "m"}})  # fmt: skip
    with pytest.raises(ProviderNotConfigured):
        resolve_provider(no_key, "openai")

    monkeypatch.setenv("SOME_KEY", "k")
    tbd = ProviderConfig(providers={"anthropic": {"enabled": True, "api_key_env": "SOME_KEY", "model": "TBD"}})  # fmt: skip
    with pytest.raises(ProviderNotConfigured):
        resolve_provider(tbd, "anthropic")


def test_no_mock_provider_exists_in_the_product():
    """ADR 0024: product code under src/llm contains no mock/fake/dummy/simulation path."""
    pattern = re.compile(r"\b(mock\w*|fake\w*|dummy|simulat\w*)\b", re.I)
    src = Path(__file__).resolve().parents[1] / "src" / "llm"
    hits = [
        f"{p.name}:{i}"
        for p in src.rglob("*.py")
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line) and "no mock" not in line.lower() and "never" not in line.lower()
        and "stand-in" not in line.lower()
    ]
    assert not hits, hits


# ------------------------------------------------------------------------------------ CLI
def test_cli_refuses_without_operator_flag_and_makes_no_call(project, monkeypatch):
    from src.llm import cli as llm_cli

    monkeypatch.delenv("ALLOW_REAL_LLM_CALLS", raising=False)
    rc = llm_cli.main(["--root", str(project), "--provider", "openai", "--limit", "2"])
    assert rc == 4  # REAL_CALLS_DISABLED_BY_OPERATOR


def test_cli_fails_cleanly_when_provider_disabled(project, monkeypatch):
    from src.llm import cli as llm_cli

    monkeypatch.setenv("ALLOW_REAL_LLM_CALLS", "true")
    rc = llm_cli.main(["--root", str(project), "--provider", "anthropic"])
    assert rc == 2
