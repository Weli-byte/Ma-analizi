"""S8: LLM 1X2 benchmark. Every provider call in this file is MOCKED (no network, no API key) --
per the sprint's own spec ("provider calls testlerde mocklanmali")."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from src.evaluation.dataset import EvalRow
from src.llm.parse import MalformedLLMOutput, parse_llm_output
from src.llm.prompt import build_prompt, prompt_hash, snapshot_hash
from src.llm.providers import (
    AnthropicProvider,
    GoogleProvider,
    LLMResponse,
    OpenAIProvider,
    ProviderError,
    cost_usd,
)
from src.llm.runner import ProviderNotConfigured, resolve_provider, run_llm_benchmark, run_one
from src.llm.snapshot import build_snapshot
from src.schemas import ExperimentType

T0 = datetime(2024, 3, 1, tzinfo=UTC)


def row(fixture_id="f1", outcome=0, feats=None):
    return EvalRow(
        fixture_id, "EPL", "2023-24", T0, "H", "A", outcome,
        features=feats or {"home_form_points_5": 10.0, "away_form_points_5": 4.0},
        odds={"closing:agg_avg": (2.0, 3.4, 3.8)},
    )  # fmt: skip


def ok_json(home=0.6, draw=0.25, away=0.15, confidence=0.7, reasoning="home form is strong"):
    return json.dumps(
        {
            "home_probability": home,
            "draw_probability": draw,
            "away_probability": away,
            "confidence": confidence,
            "short_reasoning": reasoning,
        }
    )


# ------------------------------------------------------------------------- snapshot / prompt
def test_snapshot_never_carries_outcome_or_goals():
    r = row(outcome=2)
    snap = build_snapshot(r, T0)
    blob = json.dumps(snap)
    assert "outcome" not in blob and "goal" not in blob.lower()
    assert snap["permitted_historical_features"]["home_form_points_5"] == 10.0
    assert "away_form_points_5" in snap["permitted_historical_features"]


def test_snapshot_omits_none_valued_features():
    r = row(feats={"home_form_points_5": None, "away_form_points_5": 4.0})
    snap = build_snapshot(r, T0)
    assert "home_form_points_5" not in snap["permitted_historical_features"]
    assert snap["permitted_historical_features"]["away_form_points_5"] == 4.0


def test_prompt_is_strict_json_only_instruction_and_deterministic_hash():
    snap = build_snapshot(row(), T0)
    p1, p2 = build_prompt(snap), build_prompt(snap)
    assert p1 == p2
    assert "STRICT JSON" in p1
    assert prompt_hash(p1) == prompt_hash(p2)
    assert snapshot_hash(snap) == snapshot_hash(build_snapshot(row(), T0))


# ------------------------------------------------------------------------------- parse
def test_parse_accepts_well_formed_output():
    parsed = parse_llm_output(ok_json())
    assert parsed["p_home"] == pytest.approx(0.6)
    assert parsed["confidence"] == 0.7
    assert parsed["short_reasoning"] == "home form is strong"


def test_parse_tolerates_a_fenced_code_block():
    fenced = f"```json\n{ok_json()}\n```"
    assert parse_llm_output(fenced)["p_home"] == pytest.approx(0.6)


def test_parse_renormalizes_slightly_off_sums():
    parsed = parse_llm_output(ok_json(home=0.601, draw=0.25, away=0.15))  # sums to 1.001
    assert parsed["p_home"] + parsed["p_draw"] + parsed["p_away"] == pytest.approx(1.0)


@pytest.mark.parametrize(
    "bad",
    [
        "not json at all",
        json.dumps({"home_probability": 0.5}),  # missing keys
        json.dumps({"home_probability": 1.5, "draw_probability": 0.0, "away_probability": 0.0, "confidence": 0.5}),
        json.dumps({"home_probability": 0.9, "draw_probability": 0.9, "away_probability": 0.9, "confidence": 0.5}),
        json.dumps({"home_probability": 0.5, "draw_probability": 0.3, "away_probability": 0.2, "confidence": 2.0}),
        "[1, 2, 3]",  # valid JSON, not an object
    ],
)  # fmt: skip
def test_parse_rejects_malformed_output(bad):
    with pytest.raises(MalformedLLMOutput):
        parse_llm_output(bad)


# ------------------------------------------------------------------------------ providers
def test_openai_provider_builds_request_and_parses_usage(monkeypatch):
    captured = {}

    def fake_post(url, headers, body, timeout=30.0):
        captured.update(url=url, headers=headers, body=body)
        return {"choices": [{"message": {"content": "hi"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    monkeypatch.setattr("src.llm.providers._post_json", fake_post)
    resp = OpenAIProvider().complete("PROMPT", "gpt-4o", "sk-test")
    assert resp.text == "hi" and resp.prompt_tokens == 10 and resp.completion_tokens == 5
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    assert captured["body"]["messages"][0]["content"] == "PROMPT"


def test_anthropic_provider_builds_request_and_parses_usage(monkeypatch):
    def fake_post(url, headers, body, timeout=30.0):
        return {"content": [{"text": "hi"}], "usage": {"input_tokens": 7, "output_tokens": 3}}

    monkeypatch.setattr("src.llm.providers._post_json", fake_post)
    resp = AnthropicProvider().complete("PROMPT", "claude-3-5-sonnet-latest", "key")
    assert resp.text == "hi" and resp.prompt_tokens == 7 and resp.completion_tokens == 3


def test_google_provider_builds_request_and_parses_usage(monkeypatch):
    captured = {}

    def fake_post(url, headers, body, timeout=30.0):
        captured.update(url=url, headers=headers)
        return {
            "candidates": [{"content": {"parts": [{"text": "hi"}]}}],
            "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 2},
        }

    monkeypatch.setattr("src.llm.providers._post_json", fake_post)
    resp = GoogleProvider().complete("PROMPT", "gemini-1.5-pro", "sk-secret")
    assert resp.text == "hi" and resp.prompt_tokens == 4 and resp.completion_tokens == 2
    # the key must be a header, never a URL query param (it would otherwise land in logs/traces)
    assert "sk-secret" not in captured["url"]
    assert captured["headers"]["x-goog-api-key"] == "sk-secret"


def test_post_json_error_scrubs_query_string_secrets(monkeypatch):
    import urllib.error

    from src.llm.providers import _post_json

    def fake_urlopen(req, timeout=30.0):
        raise urllib.error.URLError("boom")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(ProviderError) as exc_info:
        _post_json("https://example.invalid/v1?key=sk-should-not-leak", {}, {})
    assert "sk-should-not-leak" not in str(exc_info.value)


def test_post_json_failure_raises_provider_error(monkeypatch):
    import urllib.error

    from src.llm.providers import _post_json

    def fake_urlopen(req, timeout=30.0):
        raise urllib.error.URLError("boom")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(ProviderError):
        _post_json("https://example.invalid", {}, {})


def test_cost_usd_known_and_unknown_model():
    assert cost_usd("openai", "gpt-4o", 1000, 1000) == pytest.approx(0.0125)
    assert cost_usd("openai", "no-such-model", 1000, 1000) == 0.0


# -------------------------------------------------------------------------------- runner
class _FakeProvider:
    name = "openai"

    def __init__(self, text="x", prompt_tokens=10, completion_tokens=5):
        self.text, self.prompt_tokens, self.completion_tokens = text, prompt_tokens, completion_tokens
        self.calls = 0

    def complete(self, prompt, model, api_key):
        self.calls += 1
        return LLMResponse(self.text, self.prompt_tokens, self.completion_tokens, latency_ms=12.0)


def test_run_one_ok_produces_matching_prediction_and_call_record():
    provider = _FakeProvider(text=ok_json())
    result = run_one(
        row(), provider, "gpt-4o", "key", ExperimentType.HISTORICAL_BACKTEST,
        feature_version="fv2", data_version="dv-aaaaaaaaaaaa",
    )  # fmt: skip
    assert result.call.status == "ok"
    assert result.call.retries == 0
    assert result.call.track == ExperimentType.HISTORICAL_BACKTEST
    assert result.prediction is not None
    assert result.prediction.model_id == "llm_openai_gpt_4o"
    assert result.prediction.p_home == pytest.approx(0.6)
    assert result.prediction.information_cutoff == result.prediction.generated_at  # backtest convention
    # LLMCallRecord's generated_at is the REAL call time, independent of the backtest cutoff
    assert result.call.generated_at >= result.call.information_cutoff


def test_run_one_retries_then_succeeds():
    provider = _FakeProvider(text="garbage")
    calls = {"n": 0}
    real_complete = provider.complete

    def flaky(prompt, model, api_key):
        calls["n"] += 1
        if calls["n"] < 2:
            return real_complete(prompt, model, api_key)
        return LLMResponse(ok_json(), 10, 5, 12.0)

    provider.complete = flaky
    result = run_one(row(), provider, "gpt-4o", "key", ExperimentType.PROSPECTIVE, max_retries=2)
    assert result.call.status == "ok" and result.call.retries == 1


def test_run_one_exhausts_retries_and_reports_no_prediction():
    provider = _FakeProvider(text="still garbage")
    result = run_one(row(), provider, "gpt-4o", "key", ExperimentType.PROSPECTIVE, max_retries=1)
    assert result.prediction is None
    assert result.call.status == "malformed_json_exhausted"
    assert result.call.retries == 1


def test_run_one_provider_error_reports_no_prediction():
    class BoomProvider:
        name = "openai"

        def complete(self, prompt, model, api_key):
            raise ProviderError("network down")

    result = run_one(row(), BoomProvider(), "gpt-4o", "key", ExperimentType.PROSPECTIVE)
    assert result.prediction is None
    assert result.call.status == "provider_error"


def test_run_llm_benchmark_processes_every_row():
    provider = _FakeProvider(text=ok_json())
    rows = [row(f"f{i}") for i in range(3)]
    results = run_llm_benchmark(rows, provider, "gpt-4o", "key", ExperimentType.PROSPECTIVE)
    assert len(results) == 3
    assert all(r.prediction is not None for r in results)
    assert {r.prediction.fixture_id for r in results} == {"f0", "f1", "f2"}


def test_historical_track_is_flagged_distinctly_from_prospective():
    """S8 scope: separate historical replay from forward-only benchmark, memorization risk
    explicit via `track`, not hidden."""
    provider = _FakeProvider(text=ok_json())
    hist = run_one(row(), provider, "gpt-4o", "key", ExperimentType.HISTORICAL_BACKTEST)
    fwd = run_one(row(), provider, "gpt-4o", "key", ExperimentType.PROSPECTIVE)
    assert hist.call.track != fwd.call.track


# --------------------------------------------------------------------- provider config wiring
def test_resolve_provider_reads_provider_yaml(monkeypatch):
    from src.config import ProviderConfig

    monkeypatch.setenv("TEST_OPENAI_KEY", "sk-real")
    cfg = ProviderConfig(providers={"openai": {"enabled": True, "api_key_env": "TEST_OPENAI_KEY", "model": "gpt-4o"}})  # fmt: skip
    provider, model, api_key = resolve_provider(cfg, "openai")
    assert provider.name == "openai" and model == "gpt-4o" and api_key == "sk-real"


def test_resolve_provider_rejects_disabled_and_missing_key(monkeypatch):
    from src.config import ProviderConfig

    disabled = ProviderConfig(providers={"openai": {"enabled": False, "api_key_env": "X", "model": "m"}})
    with pytest.raises(ProviderNotConfigured):
        resolve_provider(disabled, "openai")

    monkeypatch.delenv("UNSET_KEY_VAR", raising=False)
    enabled_no_key = ProviderConfig(providers={"openai": {"enabled": True, "api_key_env": "UNSET_KEY_VAR", "model": "m"}})  # fmt: skip
    with pytest.raises(ProviderNotConfigured):
        resolve_provider(enabled_no_key, "openai")


# ------------------------------------------------------------------------------------ CLI
def test_cli_runs_end_to_end_on_the_golden_project(project, monkeypatch):
    from conftest import build_all

    from src.llm import cli as llm_cli
    from src.llm.providers import PROVIDERS

    build_all(project, mode="research")
    (project / "configs" / "provider.yaml").write_text(
        "providers:\n  openai: {enabled: true, api_key_env: TEST_LLM_KEY, model: gpt-4o}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TEST_LLM_KEY", "sk-test")
    monkeypatch.setitem(PROVIDERS, "openai", _FakeProvider(text=ok_json()))

    rc = llm_cli.main(["--root", str(project), "--provider", "openai", "--limit", "2"])
    assert rc == 0

    out_dirs = list((project / "artifacts" / "llm_runs").iterdir())
    assert len(out_dirs) == 1
    files = {p.name for p in out_dirs[0].iterdir()}
    assert {"predictions.jsonl", "calls.jsonl", "summary.md", "hashes.json"} <= files
    assert "HISTORICAL_BACKTEST" in (out_dirs[0] / "summary.md").read_text(encoding="utf-8")


def test_cli_fails_cleanly_when_provider_disabled(project):
    from src.llm import cli as llm_cli

    rc = llm_cli.main(["--root", str(project), "--provider", "anthropic"])
    assert rc == 2


def test_llm_call_record_rejects_generated_at_before_cutoff():
    from pydantic import ValidationError

    from src.schemas import LLMCallRecord

    with pytest.raises(ValidationError):
        LLMCallRecord(
            fixture_id="f1", provider="openai", model="gpt-4o", prompt_version="v1",
            prompt_hash="a" * 8, snapshot_hash="b" * 8,
            information_cutoff=T0, generated_at=T0 - timedelta(hours=1),
            track=ExperimentType.PROSPECTIVE, status="ok", latency_ms=1.0,
            prompt_tokens=1, completion_tokens=1, cost_usd=0.0,
            raw_response_sha256="c" * 8, confidence=0.5,
        )  # fmt: skip
