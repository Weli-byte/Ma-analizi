"""S10 (ADR 0021): audit of persisted LLM artifacts, and the post-kickoff guard's effect."""

from datetime import UTC, datetime

from src.llm.audit import (
    audit_calls_jsonl,
    audit_predictions_jsonl,
    partition_clean,
)
from src.llm.audit import (
    main as audit_main,
)
from src.schemas import PredictionRecord
from src.schemas.lifecycle import dump_records

T0 = datetime(2024, 3, 1, tzinfo=UTC)


def valid_prediction(fixture_id="f1") -> PredictionRecord:
    return PredictionRecord(
        fixture_id=fixture_id, model_id="llm_openai_gpt_4o", model_version="1.0.0",
        feature_version="fv2", data_version="dv-aaaaaaaaaaaa", kickoff_utc=T0,
        information_cutoff=T0, generated_at=T0, p_home=0.5, p_draw=0.3, p_away=0.2,
    )  # fmt: skip


# ------------------------------------------------------------------------- predictions.jsonl
def test_audit_predictions_clean_file_has_no_violations():
    text = dump_records([valid_prediction()])
    assert audit_predictions_jsonl(text) == []


def test_audit_predictions_flags_tampered_content():
    rec = valid_prediction()
    data = rec.model_dump(mode="json")
    data["p_home"], data["p_draw"], data["p_away"] = 0.2, 0.3, 0.5  # still sums to 1, hash now stale
    tampered_line = __import__("json").dumps(data)
    violations = audit_predictions_jsonl(tampered_line)
    assert len(violations) == 1 and violations[0].kind == "content_tampered"
    assert violations[0].critical


def test_audit_predictions_flags_invalid_json_gracefully():
    violations = audit_predictions_jsonl("not json at all\n")
    assert len(violations) == 1 and violations[0].fixture_id is None


def test_audit_predictions_ignores_blank_lines():
    text = dump_records([valid_prediction()])
    assert audit_predictions_jsonl(f"\n{text}\n\n") == []


# -------------------------------------------------------------------------------- calls.jsonl
def valid_call_dict(fixture_id="f1") -> dict:
    return {
        "fixture_id": fixture_id, "provider": "openai", "model": "gpt-6-luna",
        "prompt_version": "llm-prompt-v2", "prompt_hash": "a" * 8, "snapshot_hash": "b" * 8,
        "information_cutoff": T0.isoformat(), "generated_at": T0.isoformat(),
        "track": "historical_backtest", "status": "ok", "retries": 0, "latency_ms": 12.0,
        "prompt_tokens": 10, "completion_tokens": 5, "cost_usd": 0.001,
        "raw_response_sha256": "c" * 8, "confidence": 0.7, "short_reasoning": "x",
    }  # fmt: skip


def test_audit_calls_clean_file_has_no_violations():
    import json

    text = json.dumps(valid_call_dict())
    assert audit_calls_jsonl(text) == []


def test_audit_calls_flags_missing_timestamp():
    import json

    d = valid_call_dict()
    d["generated_at"] = ""
    violations = audit_calls_jsonl(json.dumps(d))
    assert len(violations) == 1
    assert violations[0].kind == "missing_timestamp" and violations[0].critical


def test_audit_calls_flags_invalid_json():
    violations = audit_calls_jsonl("{not valid json")
    assert len(violations) == 1 and violations[0].kind == "invalid_json"


def test_audit_calls_flags_schema_violation():
    import json

    d = valid_call_dict()
    d["track"] = "not_a_real_track"
    violations = audit_calls_jsonl(json.dumps(d))
    assert len(violations) == 1 and violations[0].kind == "invalid_record"


# ---------------------------------------------------------------------------- partition_clean
def test_partition_clean_excludes_only_critical_fixtures_and_reports_count():
    clean_pred = valid_prediction("clean")
    tampered = valid_prediction("tampered")
    predictions = [clean_pred, tampered]
    violations = audit_predictions_jsonl(
        dump_records([clean_pred])
        + "\n"
        + __import__("json").dumps({**tampered.model_dump(mode="json"), "p_home": 0.99})
    )
    result, excluded = partition_clean(predictions, violations)
    assert excluded == 1
    assert {p.fixture_id for p in result} == {"clean"}


def test_partition_clean_is_a_noop_with_no_violations():
    predictions = [valid_prediction("a"), valid_prediction("b")]
    result, excluded = partition_clean(predictions, [])
    assert excluded == 0 and len(result) == 2


# ------------------------------------------------------------------------------------- CLI
def test_audit_main_reports_zero_when_no_llm_runs_dir(tmp_path):
    assert audit_main(["--root", str(tmp_path)]) == 0


def test_audit_main_scans_every_run_dir_and_returns_nonzero_on_critical(tmp_path):
    run_dir = tmp_path / "artifacts" / "llm_runs" / "run1"
    run_dir.mkdir(parents=True)
    (run_dir / "predictions.jsonl").write_text("not json\n", encoding="utf-8")
    assert audit_main(["--root", str(tmp_path)]) == 1


def test_audit_main_returns_zero_on_a_clean_run_dir(tmp_path):
    run_dir = tmp_path / "artifacts" / "llm_runs" / "run1"
    run_dir.mkdir(parents=True)
    (run_dir / "predictions.jsonl").write_text(dump_records([valid_prediction()]) + "\n", encoding="utf-8")
    assert audit_main(["--root", str(tmp_path)]) == 0


# ------------------------------------------------------------------- post-kickoff guard (S10)
def test_post_kickoff_rejection_never_produces_a_predictionrecord():
    """A PROSPECTIVE request at/after kickoff is refused BEFORE the provider is contacted
    (ADR 0024 phase 22): no prediction, and the provider object is never touched."""
    from src.evaluation.dataset import EvalRow
    from src.llm.runner import run_one
    from src.schemas import ExperimentType

    class MustNotBeCalled:
        name = "openai"

        def complete(self, *a, **k):
            raise AssertionError("provider contacted after kickoff")

    row = EvalRow("f1", "EPL", "2023-24", T0, "H", "A", 0, features={"home_form_points_5": 1.0})
    result = run_one(row, MustNotBeCalled(), "gpt-6-luna", "key", ExperimentType.PROSPECTIVE)
    assert result.prediction is None
    assert result.call.status == "post_kickoff_rejected"
    assert result.call.raw_response_sha256 is None and result.call.latency_ms is None
