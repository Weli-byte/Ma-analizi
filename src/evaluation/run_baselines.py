"""Run S3 baselines on the chronological split from configs/evaluation.yaml.

    python -m src.evaluation.run_baselines

Fits on train seasons, reports on the validation seasons. The final-test seasons are never loaded.
"""

import json
import subprocess
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from src.config import load_config
from src.models import default_baselines
from src.schemas import ExperimentRecord

from .dataset import load_rows
from .runner import EvalReport, evaluate

ROOT = Path(__file__).resolve().parents[2]
NOTES = [
    "always_home is degenerate (p=[1,0,0]); its log loss is dominated by the 1e-15 clip.",
    "market_implied uses closing odds (Avg, else B365): a reference bar for later models, NOT a "
    "pre-cutoff signal. It is not tuned and not a competitor to be 'beaten' honestly.",
    "recent_form_naive: fixed rule (share prop. to 1 + points last 5; draw = training rate).",
    "Metrics are on the common fixture set (fixtures every model can predict); no single winner "
    "is declared. CIs arrive in S9.",
]


def render_md(rep: EvalReport, data_version: str, feature_version: str) -> str:
    L = [
        f"# S3 baseline benchmark — {data_version} / {feature_version}",
        "",
        f"- fit on: {rep.train_range[0]} .. {rep.train_range[1]}",
        f"- evaluated on: {rep.eval_range[0]} .. {rep.eval_range[1]} (validation; final test untouched)",  # noqa: E501
        f"- eval fixtures: {rep.n_eval_rows} | common set: {rep.n_common_rows}",
        "",
        "| model | n | Log Loss | Brier | RPS | Accuracy |",
        "|---|---|---|---|---|---|",
    ]
    for r in rep.results:
        m = r.metrics
        L.append(
            f"| {r.model_id} v{r.model_version} | {m['n']} | {m['log_loss']:.4f} | "
            f"{m['brier']:.4f} | {m['rps']:.4f} | {m['accuracy']:.3f} |"
        )
    for title, attr in (("league", "by_league"), ("season", "by_season")):
        L += [
            "",
            f"## By {title}",
            "",
            f"| model | {title} | n | Log Loss | Brier | RPS | Accuracy |",
            "|---|---|---|---|---|---|---|",
        ]
        for r in rep.results:
            for k, m in getattr(r, attr).items():
                L.append(
                    f"| {r.model_id} | {k} | {m['n']} | {m['log_loss']:.4f} | "
                    f"{m['brier']:.4f} | {m['rps']:.4f} | {m['accuracy']:.3f} |"
                )
    L += ["", "## Diagnostics"]
    for r in rep.results:
        L.append(
            f"- {r.model_id}: unpredictable_rows={r.unpredictable_rows}; "
            f"{json.dumps(r.diagnostics, sort_keys=True)}"
        )
    L += ["", "## Notes", *[f"- {n}" for n in NOTES]]
    return "\n".join(L) + "\n"


def git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=ROOT,
            check=True,
        ).stdout.strip()
        return out or "unknown"
    except Exception:
        return "unknown"


def main() -> None:
    data_cfg, eval_cfg, model_cfg = (
        load_config("data"),
        load_config("evaluation"),
        load_config("model"),
    )
    proc = ROOT / data_cfg.processed_dir / data_cfg.data_version
    feats = ROOT / "data" / "features" / model_cfg.feature_version / "features.parquet"
    if not feats.exists():
        raise SystemExit("features missing: run `python -m src.features.builder` first")
    train = load_rows(proc / "football.duckdb", feats, eval_cfg.train_seasons)
    test = load_rows(proc / "football.duckdb", feats, eval_cfg.validation_seasons)
    rep = evaluate(default_baselines(), train, test)

    out = ROOT / "reports"
    out.mkdir(exist_ok=True)
    tag = f"baselines_{data_cfg.data_version}_{model_cfg.feature_version}"
    (out / f"{tag}.json").write_text(
        json.dumps(asdict(rep), indent=2, sort_keys=True), encoding="utf-8"
    )
    (out / f"{tag}.md").write_text(
        render_md(rep, data_cfg.data_version, model_cfg.feature_version), encoding="utf-8"
    )
    exp = ExperimentRecord(
        run_id=f"s3-{tag}",
        config={
            "models": [r.model_id for r in rep.results],
            "train_seasons": eval_cfg.train_seasons,
            "validation_seasons": eval_cfg.validation_seasons,
            "feature_version": model_cfg.feature_version,
        },
        dataset_version=data_cfg.data_version,
        git_sha=git_sha(),
        seed=model_cfg.seed,
        created_at=datetime.now(UTC),
        metrics={f"{r.model_id}.{k}": v for r in rep.results for k, v in r.metrics.items()},
    )
    (out / f"experiment_{tag}.json").write_text(exp.model_dump_json(indent=2), encoding="utf-8")
    print(render_md(rep, data_cfg.data_version, model_cfg.feature_version))


if __name__ == "__main__":
    main()
