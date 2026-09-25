"""The ONLY path allowed to read final-test seasons (ADR 0004).

    python -m src.evaluation.final [--root DIR]      (final mode: clean tree, verified provenance)

Fits on train + validation seasons, evaluates once on the final-test seasons, writes an immutable
artifact and an access-log entry. A second run for the same (data, features, split, models) is
refused: the final test is spent once.
"""

import argparse
import json
import os
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path

from src.cli_utils import configure_output
from src.config import EvaluationConfig, config_dir_for, load_config
from src.data.dataset import resolve_dataset
from src.features.artifact import load_features
from src.models import build_models
from src.provenance import collect
from src.runmode import RunMode, policy

from .context import _FINAL_TOKEN, EvalMode, EvaluationContext, FinalTestAccessError
from .dataset import load_rows
from .runner import EvalSettings, evaluate
from .split import build_split_manifest

ROOT = Path(__file__).resolve().parents[2]


class FinalAlreadyRun(RuntimeError):
    pass


def unlock_final(cfg: EvaluationConfig, mode: RunMode | str, root: Path = ROOT) -> EvaluationContext:
    """Create a FINAL context. Requires FINAL run mode, a clean git tree and logs the access."""
    mode = RunMode(mode)
    if not policy(mode).allow_final_access:
        raise FinalTestAccessError(f"{mode.value} mode may not unlock final-test data")
    prov = collect(mode, root)  # FINAL: real SHA + clean tree, else ProvenanceError
    log = root / "artifacts" / "final_access_log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_sha": prov.git_sha,
        "mode": mode.value,
        "final_test_seasons": list(cfg.final_test_seasons),
    }
    with log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
    return EvaluationContext(EvalMode.FINAL, cfg, _FINAL_TOKEN)


def run_final_evaluation(root: Path = ROOT, mode: RunMode | str = RunMode.FINAL) -> Path:
    mode = RunMode(mode)
    cdir = config_dir_for(root)
    data_cfg = load_config("data", cdir)
    model_cfg = load_config("model", cdir)
    eval_cfg = load_config("evaluation", cdir)

    ref = resolve_dataset(root / data_cfg.processed_dir)
    split = build_split_manifest(eval_cfg, ref)
    tag = f"{ref.data_version}_{model_cfg.feature_version}_{split['split_id']}_{'-'.join(model_cfg.models)}"
    out_dir = root / "artifacts" / "final" / tag
    if out_dir.exists():
        raise FinalAlreadyRun(f"final evaluation for {tag} already exists at {out_dir}; it is spent")

    ctx = unlock_final(eval_cfg, mode, root)
    feats = load_features(root, ref, model_cfg.feature_version)
    fit_rows = load_rows(ref, ctx, [*eval_cfg.train_seasons, *eval_cfg.validation_seasons], feats)
    final_rows = load_rows(ref, ctx, eval_cfg.final_test_seasons, feats)
    settings = EvalSettings(
        run_mode=mode,
        metrics=list(eval_cfg.metrics),
        bins=eval_cfg.calibration_bins,
        bootstrap_samples=eval_cfg.bootstrap_samples,
        bootstrap_seed=eval_cfg.bootstrap_seed,
        max_fallback_rate=getattr(eval_cfg.max_fallback_rate, mode.value),
    )
    rep = evaluate(build_models(model_cfg.models), fit_rows, final_rows, settings)
    out_dir.mkdir(parents=True)
    payload = {
        "split_id": split["split_id"],
        "data_version": ref.data_version,
        "n_final_rows": len(final_rows),
        "results": {r.model_id: {"metrics": r.metrics, "ci": r.confidence_intervals} for r in rep.results},
    }
    target = out_dir / "final_results.json"
    target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.chmod(target, stat.S_IREAD)  # immutable artifact
    return out_dir


def main(argv: list[str] | None = None) -> int:
    configure_output()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default=str(ROOT))
    a = p.parse_args(argv)
    try:
        out = run_final_evaluation(Path(a.root))
    except (RuntimeError, PermissionError, ValueError) as e:
        print(f"FINAL EVALUATION REFUSED: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    print(f"final results written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
