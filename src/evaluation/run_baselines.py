"""Run the S3 baselines on the chronological split from configs/evaluation.yaml.

    python -m src.evaluation.run_baselines [--root DIR] [--mode research]

Fits on train seasons, reports on validation seasons. Final-test seasons are never loaded
(EvaluationContext blocks them). Outputs go to artifacts/runs/<data_version>_<fv>_<split_id>/.
"""

import argparse
import hashlib
import json
import shutil
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.cli_utils import configure_output
from src.config import config_dir_for, load_config
from src.data.dataset import resolve_dataset
from src.features.artifact import load_features
from src.models import build_models
from src.provenance import collect
from src.runmode import RunMode
from src.schemas import ExperimentRecord, PredictionLedger, PredictionRecord, transition
from src.schemas.common import PredictionStatus as PS
from src.schemas.lifecycle import dump_records
from src.versioning import canonical_json

from .context import EvalMode, make_context
from .dataset import load_rows
from .runner import EvalReport, EvalSettings, evaluate
from .split import build_split_manifest

ROOT = Path(__file__).resolve().parents[2]
NOTES = [
    "always_home is degenerate (p=[1,0,0]); its log loss is dominated by the 1e-15 clip.",
    "market_implied is a REFERENCE_MARKET_BASELINE: it uses closing odds (timestamp unknown), so "
    "it is a reference bar for probability quality, NOT a competitor and NOT a trading signal.",
    "recent_form_naive: fixed rule (share prop. to 1 + points last 5; draw = training rate); "
    "fallbacks are counted in the availability report.",
    "Confidence intervals are percentile bootstraps over fixtures; overlapping intervals mean the "
    "difference is NOT established. No significance claim is made.",
    "Accuracy uses fractional credit on exact ties. No single overall winner is declared.",
]


def _f(x: float) -> str:
    return f"{x:.4f}"


def render_md(rep: EvalReport, meta: dict) -> str:
    L = [
        f"# Baseline benchmark — {meta['data_version']} / {meta['feature_version']} / {meta['split_id']}",
        "",
        f"- fit on: {rep.train_range[0]} .. {rep.train_range[1]}",
        f"- evaluated on: {rep.eval_range[0]} .. {rep.eval_range[1]} (validation; final test never loaded)",
        f"- eval fixtures: {rep.n_eval_rows} | common set: {rep.n_common_rows}",
        "",
        "| model | class | n | Log Loss [95% CI] | Brier [95% CI] | RPS [95% CI] | ECE | Accuracy |",
        "|---|---|---|---|---|---|---|---|",
    ]

    def cell(r, name):
        ci = r.confidence_intervals.get(name)
        v = _f(r.metrics[name])
        return f"{v} [{_f(ci['lower'])}, {_f(ci['upper'])}]" if ci else v

    for r in rep.results:
        L.append(
            f"| {r.model_id} v{r.model_version} | {r.model_class} | {r.metrics['n']} | "
            f"{cell(r, 'log_loss')} | {cell(r, 'brier')} | {cell(r, 'rps')} | "
            f"{_f(r.metrics['ece'])} | {r.metrics['accuracy']:.3f} |"
        )
    for title, attr in (("league", "by_league"), ("season", "by_season")):
        L += [
            "",
            f"## By {title}",
            "",
            f"| model | {title} | n | Log Loss | Brier | RPS | ECE | Accuracy |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for r in rep.results:
            for k, m in getattr(r, attr).items():
                L.append(
                    f"| {r.model_id} | {k} | {m['n']} | {_f(m['log_loss'])} | {_f(m['brier'])} | "
                    f"{_f(m['rps'])} | {_f(m['ece'])} | {m['accuracy']:.3f} |"
                )
    L += ["", "## Feature availability / diagnostics"]
    for r in rep.results:
        a = r.availability
        line = f"- {r.model_id}: unpredictable_rows={r.unpredictable_rows}"
        if a:
            line += (
                f"; fallback_rate={a['fallback_rate']}; fallback_rows={a['fallback_rows']}; "
                f"unexpected_missing_rows={a['unexpected_missing_rows']}; reasons={a['reasons']}"
            )
        L.append(line + f"; {json.dumps(r.diagnostics, sort_keys=True)}")
    L += ["", "## Notes", *[f"- {n}" for n in NOTES]]
    return "\n".join(L) + "\n"


@dataclass(frozen=True)
class RunOutput:
    out_dir: Path
    report: EvalReport
    meta: dict
    hashes: dict[str, str]


def _predictions(rep: EvalReport, meta: dict, cutoff_offset_hours: float) -> list[PredictionRecord]:
    ledger = PredictionLedger()
    versions = {r.model_id: r.model_version for r in rep.results}
    for model_id, probs in rep.probs.items():
        for row, p in zip(rep.common_rows, probs, strict=True):
            cutoff = row.kickoff_utc - timedelta(hours=cutoff_offset_hours)
            rec = PredictionRecord(
                fixture_id=row.fixture_id,
                model_id=model_id,
                model_version=versions[model_id],
                feature_version=meta["feature_version"],
                data_version=meta["data_version"],
                kickoff_utc=row.kickoff_utc,
                information_cutoff=cutoff,
                generated_at=cutoff,
                p_home=float(p[0]),
                p_draw=float(p[1]),
                p_away=float(p[2]),
            )
            for status in (PS.DRAFT, PS.PUBLISHED, PS.LOCKED, PS.EVALUATED):
                rec = rec if status == PS.DRAFT else transition(rec, status)
                ledger.append(rec)
    return ledger.latest_records()


def run_baselines(root: Path = ROOT, mode: RunMode | str = RunMode.RESEARCH) -> RunOutput:
    mode = RunMode(mode)
    cdir = config_dir_for(root)
    data_cfg = load_config("data", cdir)
    model_cfg = load_config("model", cdir)
    eval_cfg = load_config("evaluation", cdir)
    feat_cfg = load_config("features", cdir)
    prov = collect(mode, root)

    ref = resolve_dataset(root / data_cfg.processed_dir)
    feats = load_features(root, ref, model_cfg.feature_version)  # stale artifacts fail loudly
    split = build_split_manifest(eval_cfg, ref)
    train = load_rows(ref, make_context(EvalMode.TRAIN, eval_cfg), eval_cfg.train_seasons, feats)
    test = load_rows(ref, make_context(EvalMode.VALIDATION, eval_cfg), eval_cfg.validation_seasons, feats)
    settings = EvalSettings(
        run_mode=mode,
        metrics=list(eval_cfg.metrics),
        bins=eval_cfg.calibration_bins,
        bootstrap_samples=eval_cfg.bootstrap_samples,
        bootstrap_seed=eval_cfg.bootstrap_seed,
        max_fallback_rate=getattr(eval_cfg.max_fallback_rate, mode.value),
    )
    rep = evaluate(build_models(model_cfg.models), train, test, settings)

    meta = {
        "data_version": ref.data_version,
        "feature_version": model_cfg.feature_version,
        "split_id": split["split_id"],
    }
    tag = f"{ref.data_version}_{model_cfg.feature_version}_{split['split_id']}"
    out_dir = root / "artifacts" / "runs" / tag
    tmp = out_dir.with_name(f".tmp-{uuid.uuid4().hex[:8]}")
    tmp.mkdir(parents=True)
    try:
        records = _predictions(rep, meta, feat_cfg.cutoff_offset_hours)
        pred_text = dump_records(records)
        (tmp / "predictions.jsonl").write_text(pred_text + "\n", encoding="utf-8")
        report_json = json.dumps(
            {
                "meta": meta,
                "split_id": split["split_id"],
                "report": {k: v for k, v in asdict(rep).items() if k not in ("common_rows", "probs")},
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
        (tmp / "report.json").write_text(report_json, encoding="utf-8")
        report_md = render_md(rep, meta)
        (tmp / "report.md").write_text(report_md, encoding="utf-8")
        (tmp / "split_manifest.json").write_text(
            json.dumps(split, indent=2, sort_keys=True), encoding="utf-8"
        )
        exp_dir = tmp / "experiments"
        exp_dir.mkdir()
        for r in rep.results:
            exp = ExperimentRecord(
                experiment_id=f"{tag}_{r.model_id}",
                run_mode=mode,
                git_sha=prov.git_sha,
                git_dirty=prov.git_dirty,
                dirty_files=prov.dirty_files,
                python_version=prov.python_version,
                platform=prov.platform,
                dependency_lock_hash=prov.dependency_lock_hash,
                data_version=ref.data_version,
                feature_version=model_cfg.feature_version,
                config={
                    "model": model_cfg.model_dump(),
                    "evaluation": eval_cfg.model_dump(),
                    "features": feat_cfg.model_dump(),
                },
                model_name=r.model_id,
                model_version=r.model_version,
                seed=model_cfg.seed,
                split_id=split["split_id"],
                train_rows=len(train),
                validation_rows=len(test),
                final_test_rows=0,
                metrics=r.metrics,
                created_at_utc=datetime.now(UTC),
            )
            (exp_dir / f"{r.model_id}.json").write_text(exp.model_dump_json(indent=2), encoding="utf-8")
        hashes = {
            "predictions": hashlib.sha256(pred_text.encode()).hexdigest(),
            "metrics": hashlib.sha256(
                canonical_json({r.model_id: r.metrics for r in rep.results}).encode()
            ).hexdigest(),
            "report": hashlib.sha256(report_md.encode()).hexdigest(),
            "report_json": hashlib.sha256(report_json.encode()).hexdigest(),
        }
        (tmp / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")
        if out_dir.exists():
            shutil.rmtree(out_dir)
        tmp.replace(out_dir)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
    return RunOutput(out_dir, rep, meta, hashes)


def main(argv: list[str] | None = None) -> int:
    configure_output()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--mode", default="research", choices=[m.value for m in RunMode if m != RunMode.FINAL])
    a = p.parse_args(argv)
    try:
        out = run_baselines(Path(a.root), a.mode)
    except (RuntimeError, ValueError, PermissionError, KeyError) as e:
        print(f"BASELINE RUN FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    print((out.out_dir / "report.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
