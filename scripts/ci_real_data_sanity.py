"""S0-S7 hardening, Phase 3 — CI real-data sanity layer.

    python scripts/ci_real_data_sanity.py [--root DIR] [--mode strict]

CI so far (`tests/test_golden_and_reproducibility.py`) only exercises a 36-fixture SYNTHETIC
golden project. This script runs the SAME pipeline -> features -> baselines -> walk-forward
chain on `tests/fixtures/real_smoke/root`: three genuinely real, unmodified EPL seasons
(2021-22..2023-24, 1140 matches, football-data.co.uk) committed as a small, fixed fixture —
real data, but small and network-free, so it is fast and deterministic enough to run on every
PR. It is NOT a substitute for the full 2019-2024 benchmark (that belongs in a scheduled/nightly
job per the hardening plan); it exists to catch what only shows up on real (not hand-crafted)
data: real team-name variety, real season-boundary/promotion-relegation churn, real null
patterns in optional columns, real odds quirks.

What it checks (no crash is necessary but not sufficient):
  - ingestion/normalization: expected raw/accepted row counts, zero rejected rows, all three
    seasons reach HISTORICAL_COMPLETE status.
  - feature generation: `build_features` succeeds, which already runs the leakage audit
    (`src.features.leakage_audit.audit_leakage`) internally and raises on any violation — a
    failure here IS a leakage-audit failure, not a separate check bolted on afterwards.
  - split generation: the split manifest's periods are chronological (train < validation <
    final-test), enforced by `split.build_split_manifest` itself.
  - every configured model (baselines, Elo, Poisson, Dixon-Coles, XGBoost, LightGBM) produces
    a finite, valid probability distribution for every common-set fixture (`metrics.validate`,
    called again here explicitly, on top of the check `evaluate()` already performs internally).
  - walk-forward smoke: at least one fold executes, its ledger is well-formed.
  - determinism: the entire chain is run TWICE from a clean state; predictions/report/metric
    hashes must be byte-identical between the two runs.
"""

import argparse
import shutil
import sys
import time
from pathlib import Path

from src.data.dataset import resolve_dataset
from src.data.pipeline import run_pipeline
from src.evaluation.metrics import validate
from src.evaluation.run_baselines import run_baselines
from src.evaluation.walk_forward import run_walk_forward
from src.features.builder import build_features
from src.runmode import RunMode

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "real_smoke" / "root"
AS_OF = __import__("datetime").date(2024, 6, 1)  # fixed: keeps the fixture's split stable
EXPECTED_RAW_ROWS = 1140  # 3 real EPL seasons x 380 matches
EXPECTED_SEASONS = {"2021-22", "2022-23", "2023-24"}


class SanityError(RuntimeError):
    pass


def _clean(root: Path) -> None:
    for generated in ("data/processed", "data/features", "artifacts"):
        shutil.rmtree(root / generated, ignore_errors=True)


def _run_once(root: Path, mode: str, label: str) -> dict:
    t0 = time.time()
    pipe = run_pipeline(root, mode, as_of=AS_OF)
    summary = pipe.report["summary"]
    if summary["raw_rows"] != EXPECTED_RAW_ROWS:
        raise SanityError(f"[{label}] expected {EXPECTED_RAW_ROWS} raw rows, got {summary['raw_rows']}")
    if summary["accepted_fixtures"] != EXPECTED_RAW_ROWS:
        raise SanityError(f"[{label}] {summary['rejected_rows']} rows rejected; expected 0")
    seasons_status = {
        k.split("|")[1]: v["status"] for k, v in pipe.report["seasons"].items() if k.startswith("EPL|")
    }
    bad = {s: st for s, st in seasons_status.items() if s in EXPECTED_SEASONS and st != "historical_complete"}
    if bad:
        raise SanityError(f"[{label}] seasons not HISTORICAL_COMPLETE: {bad}")

    build_features(root, mode, audit_samples=60)  # raises BuildError on any leakage violation

    baselines = run_baselines(root, mode)
    rep = baselines.report
    for r in rep.results:
        for k, v in r.metrics.items():
            if not (v == v and abs(v) != float("inf")):  # nan/inf check without importing numpy here
                raise SanityError(f"[{label}] {r.model_id}.{k} is not finite: {v}")
    common_probs = baselines.report.probs
    for model_id, p in common_probs.items():
        try:
            validate(p)
        except ValueError as e:
            raise SanityError(f"[{label}] {model_id} produced invalid probabilities: {e}") from e
    expected_models = {"always_home", "historical_prior", "recent_form_naive", "market_implied",
                        "elo", "poisson", "dixon_coles", "xgboost", "lightgbm"}  # fmt: skip
    got_models = {r.model_id for r in rep.results}
    if got_models != expected_models:
        raise SanityError(f"[{label}] model set mismatch: got {got_models}, expected {expected_models}")

    wf = run_walk_forward(root, mode)
    if not wf.fold_results:
        raise SanityError(f"[{label}] walk-forward produced zero folds")
    for fr in wf.fold_results:
        if set(fr.metrics) != expected_models:
            raise SanityError(f"[{label}] fold {fr.fold.index} model set mismatch: {set(fr.metrics)}")

    ref = resolve_dataset(root / "data" / "processed")
    elapsed = round(time.time() - t0, 1)
    print(f"[{label}] OK in {elapsed}s — data_version={ref.data_version}, "
          f"baseline_hashes={baselines.hashes}, walk_forward_hashes={wf.hashes}")  # fmt: skip
    return {"baseline_hashes": baselines.hashes, "walk_forward_hashes": wf.hashes}


def run_sanity(root: Path = FIXTURE_ROOT, mode: str = "strict") -> None:
    RunMode(mode)  # validates the mode string early
    _clean(root)
    run_a = _run_once(root, mode, "RUN A")
    _clean(root)
    run_b = _run_once(root, mode, "RUN B")
    if run_a != run_b:
        raise SanityError(
            "NON-DETERMINISTIC: rerunning the identical chain produced different hashes:\n"
            f"  RUN A: {run_a}\n  RUN B: {run_b}"
        )
    print("DETERMINISM OK: RUN A and RUN B produced byte-identical hashes.")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default=str(FIXTURE_ROOT))
    p.add_argument("--mode", default="strict", choices=[m.value for m in RunMode if m != RunMode.FINAL])
    a = p.parse_args(argv)
    try:
        run_sanity(Path(a.root), a.mode)
    except (SanityError, RuntimeError, ValueError) as e:
        print(f"REAL-DATA SANITY FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    print("REAL-DATA SANITY: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
