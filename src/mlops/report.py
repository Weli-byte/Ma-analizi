"""S16 MLOps report (ADR 0032):

    python -m src.mlops.report [--collect-results] [--registry] [--fail-on-critical]

Collects every monitor (data freshness, provider health, scheduler heartbeats, LLM health, prediction
distribution, feature drift/missingness, settled metric drift), the version registry and a checklist of
real-world verifications that can only happen when real events occur (first live match, first locked
stage, first announced lineup, ...). Writes artifacts/ops/report.{json,md}, appends alerts, prints them.
Nothing here retrains, switches providers or edits a model.
"""

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from src.cli_utils import configure_output, load_dotenv
from src.config import config_dir_for, load_config
from src.data.dataset import resolve_dataset
from src.schemas import FixtureStatus

from . import alerts as alerts_mod
from . import monitor
from .oplog import ops_dir, read_rows, read_rows_checked
from .registry import write_registry
from .retrain_gate import evaluate_retrain

ROOT = Path(__file__).resolve().parents[2]
OUTCOME_INDEX = {"H": 0, "D": 1, "A": 2}


def collect_results(root: Path) -> int:
    """Finished PL/PD results from football-data.org -> the ingestion store (also feeds the live match
    history) and `artifacts/ops/results.jsonl` (fixture id `fdorg-<id>`, used to settle forecasts).
    Returns the number of NEW results."""
    from src.data.teams import TeamDirectory
    from src.ingestion.football_data_org import FootballDataOrgProvider
    from src.ingestion.results import ingest_finished, read_store
    from src.llm.forecast import LEAGUES, current_season

    cdir = config_dir_for(root)
    key = load_config("ingestion", cdir).api_key("football-data-org")
    if not key:
        raise RuntimeError("FOOTBALL_DATA_ORG_API_KEY is not set")
    directory = TeamDirectory.load(cdir / "team_aliases.yaml")
    provider = FootballDataOrgProvider(key)
    season = current_season(datetime.now(UTC))
    new = 0
    for code, (repo_league, country) in LEAGUES.items():
        counts = ingest_finished(root, provider.list_fixtures(code, season), directory, country, repo_league)
        new += counts["new"]
    path = ops_dir() / "results.jsonl"
    known = {r["fixture_id"] for r in read_rows("results.jsonl")}
    with path.open("a", encoding="utf-8") as fh:
        for r in read_store(root):
            if r["fixture_id"] not in known:
                hg, ag = r["home_goals"], r["away_goals"]
                outcome = "H" if hg > ag else "A" if hg < ag else "D"
                row = {"fixture_id": r["fixture_id"], "outcome": outcome, "score": [hg, ag]}
                fh.write(json.dumps({**row, "kickoff_utc": r["kickoff_utc"]}) + "\n")
    return new


def _reference_probs(root: Path, data_version: str, feature_version: str) -> dict[str, np.ndarray]:
    out: dict[str, list] = {}
    for p in root.glob(f"artifacts/walk_forward/{data_version}_{feature_version}_*/predictions.jsonl"):
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                out.setdefault(r["model_id"], []).append([r["p_home"], r["p_draw"], r["p_away"]])
    return {k: np.array(v) for k, v in out.items()}


def _settled(root: Path) -> list[tuple[np.ndarray, int]]:
    results = {r["fixture_id"]: r["outcome"] for r in read_rows("results.jsonl")}
    out = []
    for sp in root.glob(monitor.SNAP_GLOB):
        if not (sp.parent / "LOCK.json").exists() or sp.parent.parent.name not in results:
            continue
        for line in (sp.parent / "predictions.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                out.append(
                    (
                        np.array([r["p_home"], r["p_draw"], r["p_away"]]),
                        OUTCOME_INDEX[results[r["fixture_id"]]],
                    )
                )
    return out


def checklist(root: Path) -> list[dict]:
    """Real-world verifications that cannot be forced: each is DONE only when real evidence exists."""
    stages = [p.parent for p in root.glob(monitor.SNAP_GLOB) if (p.parent / "LOCK.json").exists()]
    lineups_seen = any(
        json.loads((s / "snapshot.json").read_text(encoding="utf-8"))["availability"]
        .get("lineups", {})
        .get("status")
        == "OBSERVED"
        for s in stages
    )
    items = [
        (
            "first real in-play football-data.org payload captured",
            any(root.glob("artifacts/live/_captures/fdorg_*.json")),
            "artifacts/live/_captures/",
        ),
        (
            "first locked S13 stage produced from real fixtures",
            bool(stages),
            "artifacts/snapshots/*/*/LOCK.json",
        ),
        (
            "first real lineup observed before kickoff",
            lineups_seen,
            "stage snapshot availability.lineups == OBSERVED",
        ),
        (
            "first settled paper bet / result collected",
            bool(read_rows("results.jsonl")),
            "artifacts/ops/results.jsonl",
        ),
        (
            "exact-timestamp odds source configured (THE_ODDS_API_KEY)",
            bool(os.environ.get("THE_ODDS_API_KEY")),
            "needs a real response parsed (live test)",
        ),
        (
            "Anthropic provider configured (ANTHROPIC_API_KEY)",
            bool(os.environ.get("ANTHROPIC_API_KEY")),
            "owner action, planned for ~2026-11",
        ),
        (
            "API-Football key configured (API_FOOTBALL_KEY)",
            bool(os.environ.get("API_FOOTBALL_KEY")),
            "owner action",
        ),
    ]
    return [{"item": a, "done": b, "evidence": c} for a, b, c in items]


def build_report(root: Path, now: datetime | None = None) -> dict:
    root = Path(root)
    now = now or datetime.now(UTC)
    cdir = config_dir_for(root)
    cfg = load_config("mlops", cdir)
    model_cfg = load_config("model", cdir)
    report: dict = {"generated_at": now.isoformat()}

    latest = None
    ref = None
    try:
        from src.features.builder import load_matches

        ref = resolve_dataset(root / load_config("data", cdir).processed_dir)
        finished = [m.kickoff_utc for m in load_matches(ref) if m.status == FixtureStatus.FINISHED]
        latest = max(finished, default=None)
    except Exception as e:  # noqa: BLE001 - reported as FAILED freshness, not hidden
        report["data_error"] = f"{type(e).__name__}: {e}"
    report["data_freshness"] = monitor.data_freshness(latest, now, cfg)
    calls_rows, bad_calls = read_rows_checked("provider_calls.jsonl")
    beats_rows, bad_beats = read_rows_checked("heartbeats.jsonl")
    report["ops_log_corrupt_lines"] = {"provider_calls": bad_calls, "heartbeats": bad_beats}
    report["providers"] = monitor.provider_health(calls_rows, now, cfg)
    report["heartbeats"] = monitor.heartbeat_gaps(beats_rows, now, cfg)
    calls, sizes = monitor.read_llm_calls(root)
    report["llm"] = monitor.llm_health(calls, sizes, now, cfg)

    snaps, probs = monitor.read_stage_artifacts(root)
    reference = _reference_probs(root, ref.data_version, model_cfg.feature_version) if ref else {}
    report["prediction_distribution"] = monitor.prediction_distribution(probs, reference, cfg)
    training = []
    if ref:
        try:
            from src.features.artifact import load_features

            training = list(load_features(root, ref, model_cfg.feature_version).rows.values())
        except Exception as e:  # noqa: BLE001
            report["feature_error"] = f"{type(e).__name__}: {e}"
    report["feature_health"] = monitor.feature_health([s["features"] for s in snaps], training, cfg)
    settled = _settled(root)
    ref_ll = None
    report["metric_drift"] = monitor.metric_drift(settled, ref_ll, cfg)
    report["checklist"] = checklist(root)
    report["retrain_gate"] = evaluate_retrain(root, model_cfg.feature_version)
    return report


def render_md(report: dict, alerts: list) -> str:
    lines = [
        f"# Operations report ({report['generated_at']})",
        "",
        "## Alerts",
        "",
        alerts_mod.render(alerts),
        "",
    ]
    lines += ["## Data freshness", "", f"`{json.dumps(report['data_freshness'])}`", "", "## Providers", ""]
    for n, h in report["providers"].items():
        lines.append(
            f"- **{n}** {h['status']}: {h['requests']} req/24h, error rate {h['error_rate']}, "
            f"p95 {h['latency_p95_ms']} ms, last success {h['last_success']}"
        )
    lines += ["", "## Scheduler heartbeats", ""]
    for n, h in report["heartbeats"].items():
        lines.append(
            f"- **{n}** {h['status']}: {h.get('beats', 0)} beats, last {h.get('last_beat')}, "
            f"{h.get('n_gaps', 0)} gap(s)"
        )
    lines += [
        "",
        "## LLM",
        "",
        f"`{json.dumps(report['llm'])}`",
        "",
        "## Checklist of real-world verifications",
        "",
    ]
    lines += [f"- [{'x' if c['done'] else ' '}] {c['item']} ({c['evidence']})" for c in report["checklist"]]
    r = report["retrain_gate"]
    lines += ["", "## Retraining gate (no automatic retraining exists)", "", f"eligible: {r['eligible']}"]
    lines += [f"- [{'x' if c['ok'] else ' '}] {c['check']}: {c['detail']}" for c in r["checks"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    configure_output()
    load_dotenv()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", default=str(ROOT))
    p.add_argument(
        "--collect-results", action="store_true", help="fetch finished results (football-data.org)"
    )
    p.add_argument("--registry", action="store_true", help="refresh artifacts/registry/registry.json")
    p.add_argument("--fail-on-critical", action="store_true")
    a = p.parse_args(argv)
    root = Path(a.root)
    try:
        if a.collect_results:
            print(f"results collected: {collect_results(root)} new")
        if a.registry:
            path, changed = write_registry(root)
            print(f"registry {'updated' if changed else 'unchanged'}: {path}")
        now = datetime.now(UTC)
        report = build_report(root, now)
    except (RuntimeError, ValueError, KeyError) as e:
        print(f"MLOPS REPORT FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    alerts = alerts_mod.evaluate(report)
    alerts_mod.persist(alerts, now)
    out = ops_dir()
    (out / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    (out / "report.md").write_text(render_md(report, alerts), encoding="utf-8")
    print(alerts_mod.render(alerts))
    print(f"report: {out / 'report.md'}")
    return 1 if (a.fail_on_critical and any(x.severity == "critical" for x in alerts)) else 0


if __name__ == "__main__":
    raise SystemExit(main())
