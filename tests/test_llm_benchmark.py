"""Phase G (ADR 0026): benchmark engine, coverage, LLM_REAL evaluation, ensemble integration.

All LLM data here is REAL: `tests/fixtures/real_provider_captures/benchmark_historical/<provider>/`
holds predictions/calls/responses recorded from actual OpenAI, Gemini and Groq calls on real
validation-split fixtures (`python -m src.llm.benchmark`), with the real outcomes from the dataset.
"""

import json
from datetime import datetime
from pathlib import Path

import pytest

from src.config import LLMBudget
from src.evaluation.dataset import EvalRow
from src.evaluation.run_ensemble import MIN_LLM_FIXTURES, EnsembleError, add_llm_models
from src.llm.benchmark import lowered_budget, run_benchmark, sample_rows
from src.llm.coverage import CallContext, build_coverage, render_coverage_md
from src.llm.evaluation import MODEL_CLASS_LLM_REAL, audit_provenance, evaluate_llm_predictions
from src.schemas import LLMCallRecord, PredictionRecord

RUNS = Path(__file__).parent / "fixtures" / "real_provider_captures" / "benchmark_historical"
PROVIDERS = ["openai", "gemini", "groq"]
METRICS = ["log_loss", "brier", "rps", "ece_raw", "accuracy"]


def load_run(provider: str):
    d = RUNS / provider
    preds = [PredictionRecord.from_json(x) for x in (d / "predictions.jsonl").read_text().splitlines() if x]
    calls = [LLMCallRecord.model_validate_json(x) for x in (d / "calls.jsonl").read_text().splitlines() if x]
    outcomes = json.loads((d / "outcomes.json").read_text())
    return d, preds, calls, outcomes


def rows_from(outcomes: dict) -> dict[str, EvalRow]:
    return {
        fid: EvalRow(
            fid, o["league_id"], o["season"], datetime.fromisoformat(o["kickoff_utc"]), "H", "A", o["outcome"]
        )
        for fid, o in outcomes.items()
    }


# ------------------------------------------------------------------------------ engine bits
def test_cli_budget_flags_can_only_lower_the_configured_budget():
    base = LLMBudget(max_requests_per_run=3, max_estimated_cost_usd=0.01, max_concurrency=1)
    low = lowered_budget(base, 2, 0.005, 1)
    assert (low.max_requests_per_run, low.max_estimated_cost_usd) == (2, 0.005)
    high = lowered_budget(base, 500, 5.0, 16)  # attempt to loosen: ignored
    assert (high.max_requests_per_run, high.max_estimated_cost_usd, high.max_concurrency) == (3, 0.01, 1)


def test_sample_rows_is_deterministic_evenly_spaced_and_not_the_first_n():
    rows = list(range(100))
    s = sample_rows(rows, 4)
    assert s == [0, 25, 50, 75] and s == sample_rows(rows, 4)
    assert sample_rows(rows, 500) == rows


def test_benchmark_without_operator_flag_prints_plan_and_calls_nothing(project, monkeypatch, capsys):
    from conftest import build_all

    build_all(project, mode="research")
    (project / "configs" / "provider.yaml").write_text(
        "budget: {max_total_tokens: 15000}\nproviders:\n  openai: {enabled: true, api_key_env: TEST_PLAN_KEY, model: gpt-6-luna}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TEST_PLAN_KEY", "present-for-plan-only")  # never sent anywhere: no call is made
    out_dir, rc = run_benchmark(
        project, "historical", ["openai"], 2, "PL", "validation", None, None, None,
        "llm-prompt-v2", 0.0, allow_real_calls=False,
    )  # fmt: skip
    assert (out_dir, rc) == (None, 4)  # REAL_CALLS_DISABLED_BY_OPERATOR
    out = capsys.readouterr().out
    for key in ("MATCH COUNT", "REQUEST COUNT", "PROVIDER COUNT", "MODEL COUNT",
                "ESTIMATED MAX COST", "ESTIMATED TOKEN BUDGET"):  # fmt: skip
        assert key in out
    assert not (project / "artifacts" / "llm_runs").exists()


def test_benchmark_rejects_unknown_prompt_version_and_final_test_split(project):
    with pytest.raises(ValueError, match="prompt_version"):
        run_benchmark(project, "historical", None, 1, "PL", "validation", None, None, None,
                      "llm-prompt-v999", 0.0, False)  # fmt: skip
    with pytest.raises(ValueError):  # final-test seasons are locked: only validation/train
        run_benchmark(project, "historical", None, 1, "PL", "final", None, None, None,
                      "llm-prompt-v2", 0.0, False)  # fmt: skip


# --------------------------------------------------------------------- coverage (real calls)
def test_coverage_counts_every_real_request_by_provider_model_league_season_stage():
    pairs = []
    for prov in PROVIDERS:
        _, _, calls, outcomes = load_run(prov)
        rows = rows_from(outcomes)
        pairs += [
            (CallContext(rows[c.fixture_id].league_id, rows[c.fixture_id].season,
                         rows[c.fixture_id].kickoff_utc), c)
            for c in calls
        ]  # fmt: skip
    rep = build_coverage(pairs)
    assert rep["total_requests"] == 6
    for prov in PROVIDERS:
        b = rep["by_provider"][prov]
        assert b["requests"] == 2 and b["success"] + b["failed"] == 2
        assert b["coverage"] + b["failure_rate"] == pytest.approx(1.0)
    assert set(rep["by_league"]) == {"EPL", "LALIGA"} and set(rep["by_stage"]) == {"T-0h"}
    assert "Failed requests are counted" in render_coverage_md(rep)


def test_coverage_counts_failures_instead_of_dropping_them():
    _, _, calls, outcomes = load_run("openai")
    rows = rows_from(outcomes)
    failed = calls[0].model_copy(update={"status": "provider_error", "error_kind": "rate_limit"})

    def ctx(c):
        r = rows[c.fixture_id]
        return CallContext(r.league_id, r.season, r.kickoff_utc)

    rep = build_coverage([(ctx(failed), failed), (ctx(calls[1]), calls[1])])
    b = rep["by_provider"]["openai"]
    assert (b["requests"], b["success"], b["failed"]) == (2, 1, 1) and b["failure_rate"] == 0.5
    assert b["statuses"] == {"ok": 1, "provider_error": 1}


# --------------------------------------------------------------- LLM_REAL evaluation (real)
def test_real_llm_predictions_are_evaluated_as_llm_real_with_raw_and_calibrated_kept_apart():
    preds, outcomes = [], {}
    for prov in PROVIDERS:
        _, p, _, o = load_run(prov)
        preds += p
        outcomes.update(o)
    assert all(audit_provenance(p) == [] for p in preds)
    ev = evaluate_llm_predictions(preds, rows_from(outcomes), METRICS, bins=10)
    assert ev["model_class"] == MODEL_CLASS_LLM_REAL and ev["rejected_incomplete_provenance"] == 0
    assert len(ev["results"]) == 3  # one per provider/model, never pooled
    for r in ev["results"]:
        assert r["model_class"] == MODEL_CLASS_LLM_REAL and r["metrics"]["n"] == 2
        assert {"log_loss", "brier", "rps", "ece_raw"} <= set(r["metrics"])
    # 2 rows per model are far below the calibration minimum: reported as skipped, never faked
    assert all(c["skipped"] and "need >=" in c["reason"] for c in ev["calibration"].values())
    assert (
        "No single" in ev["leaderboard_md"] or "winner" in ev["leaderboard_md"].lower() or ev["leaderboard"]
    )


def test_fixtures_without_a_known_outcome_are_counted_awaiting_not_scored():
    _, preds, _, outcomes = load_run("openai")
    rows = rows_from(outcomes)
    first = next(iter(rows))
    ev = evaluate_llm_predictions(preds, {first: rows[first]}, METRICS)
    (model_id,) = ev["awaiting_result"]
    assert ev["awaiting_result"][model_id] == 1 and ev["results"][0]["metrics"]["n"] == 1


# --------------------------------------------------------------------- ensemble integration
def test_ensemble_accepts_real_llm_models_but_excludes_thin_coverage_and_reports_it():
    _, openai_preds, _, _ = load_run("openai")
    dv = openai_preds[0].data_version
    base = [p.model_copy(update={"model_id": "test_base", "p_home": 0.4, "p_draw": 0.3, "p_away": 0.3})
            for p in openai_preds]  # fmt: skip
    preds, models, cov = add_llm_models(base, ["test_base"], [RUNS / "openai", RUNS / "groq"], dv)
    assert len(preds) == len(base) + 4  # real LLM records appended, base untouched
    assert models == ["test_base"]  # 2 fixtures < MIN_LLM_FIXTURES: excluded, not padded
    assert len(cov) == 2
    for c in cov.values():
        assert c["model_class"] == "LLM_REAL" and c["included"] is False
        assert c["oof_fixtures_covered"] == 2 and f"fewer than {MIN_LLM_FIXTURES}" in c["reason"]


def test_ensemble_rejects_llm_predictions_with_a_stale_data_version():
    _, openai_preds, _, _ = load_run("openai")
    with pytest.raises(EnsembleError, match="stale data_version"):
        add_llm_models(openai_preds, [], [RUNS / "openai"], "dv-000000000000")
    with pytest.raises(EnsembleError, match="not found"):
        add_llm_models(openai_preds, [], [RUNS / "nope"], openai_preds[0].data_version)
