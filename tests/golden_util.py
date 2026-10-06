"""Shared helpers for golden/regression/reproducibility tests and scripts/update_golden.py."""

import csv
import hashlib
import io
import json
from datetime import date
from pathlib import Path

from src.data.dataset import open_db, resolve_dataset
from src.data.pipeline import run_pipeline
from src.evaluation.run_baselines import run_baselines
from src.features.artifact import artifact_dir
from src.features.builder import build_features

AS_OF = date(2026, 9, 25)
GOLDEN_DIR = Path(__file__).parent / "fixtures" / "golden" / "expected"

# Byte-exact hash reproducibility is only claimed/enforced across heterogeneous CI hardware for
# these CLOSED-FORM models (no iterative floating-point fit). elo/poisson/dixon_coles run many
# iterations of np.sum/exp/log (gradient ascent, IPF) whose CPU-dependent SIMD dispatch can
# differ in the last bit between GitHub Actions runner instances -- confirmed empirically: the
# same commit's predictions_sha256 varied across separate CI runs (never locally, never within a
# single isolated process) while every metric value still matched to 1e-8. See ADR-0017's second
# amendment. Their correctness/stability IS still verified, via metrics tolerance
# (test_golden_metrics_match_with_tolerance), not exact-hash comparison.
CLOSED_FORM_MODELS = frozenset(
    {"always_home", "historical_prior", "recent_form_naive", "market_implied"}
)


def _closed_form_predictions_hash(predictions_jsonl: str) -> str:
    lines = [
        ln for ln in predictions_jsonl.strip().splitlines()
        if json.loads(ln)["model_id"] in CLOSED_FORM_MODELS
    ]  # fmt: skip
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def normalized_fixtures_csv(root: Path) -> str:
    ref = resolve_dataset(root / "data" / "processed")
    con = open_db(ref.db_path)
    rows = con.execute(
        "SELECT f.fixture_id, f.kickoff_utc, f.home_id, f.away_id, r.home_goals, r.away_goals, f.status, "
        "f.season_status, f.result_available_at_utc, f.result_available_at_source, f.kickoff_time_known "
        "FROM fixtures f LEFT JOIN results r USING (fixture_id) ORDER BY f.fixture_id"
    ).fetchall()
    con.close()
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["fixture_id", "kickoff_utc", "home_id", "away_id", "home_goals", "away_goals", "status",
                "season_status", "result_available_at_utc", "result_available_at_source", "kickoff_time_known"])  # fmt: skip
    for r in rows:
        w.writerow([v.isoformat() if hasattr(v, "isoformat") else v for v in r])
    return out.getvalue()


def run_chain(root: Path, mode: str = "strict") -> dict:
    """pipeline -> features -> baselines. Returns the deterministic summary (no timestamps)."""
    run_pipeline(root, mode, as_of=AS_OF)
    build_features(root, mode, audit_samples=60)
    out = run_baselines(root, mode)
    ref = resolve_dataset(root / "data" / "processed")
    lineage = json.loads((artifact_dir(root, ref.data_version, "fv2") / "feature_lineage.json").read_text())
    report = json.loads((out.out_dir / "report.json").read_text())["report"]
    metrics = {r["model_id"]: {k: round(v, 9) for k, v in r["metrics"].items()} for r in report["results"]}
    pred_text = (out.out_dir / "predictions.jsonl").read_text()
    return {
        "data_version": ref.data_version,
        "dataset_content_hash": ref.meta["content_hash"],
        "table_hashes": ref.meta["table_hashes"],
        "features_content_hash": lineage["content_hash"],
        "features_rows": lineage["rows"],
        # Exact-hash-checked (byte reproducibility across heterogeneous CI hardware confirmed):
        "closed_form_predictions_sha256": _closed_form_predictions_hash(pred_text),
        # NOT exact-hash-checked across CI runs (see CLOSED_FORM_MODELS docstring above) --
        # informational / same-process regression signal only; real correctness guard for
        # elo/poisson/dixon_coles is the metrics-tolerance check below.
        "predictions_sha256": out.hashes["predictions"],
        "metrics_sha256": out.hashes["metrics"],
        "report_sha256": out.hashes["report"],
        "report_json_sha256": out.hashes["report_json"],
        "n_predictions": len(pred_text.strip().splitlines()),
        "metrics": metrics,
    }
