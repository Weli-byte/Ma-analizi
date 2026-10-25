"""`python -m src.league_models.rollout <command>` (Sprint 6, ADR 0068). Commands: shadow | reconcile | monitor | scorecard | dashboard | status.

All commands are read-only towards the outside world (one free-tier schedule read at most) and append-only towards the stores. Nothing routes user traffic."""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from src.cli_utils import configure_output, load_dotenv
from src.league_models.competitions import ROOT
from src.league_models.licenses import load_manifest

from . import dashboard, evaluation, monitoring
from .control import LeagueModelRegistry, kill_active, load_rollout
from .records import OutcomeStore, ProspectiveStore, ScoreForecastStore
from .shadow import run_shadow, season_label


def _paths(root: Path):
    d = root / "artifacts" / "league_models" / "rollout"
    return d, ProspectiveStore(d / "prospective.jsonl"), OutcomeStore(d / "outcomes.jsonl")


def _runs(d: Path) -> list[dict]:
    p = d / "shadow_runs.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.is_file() else []


def main(argv=None) -> int:
    configure_output()
    load_dotenv()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["shadow", "reconcile", "monitor", "scorecard", "dashboard", "status", "gate", "prepare", "notify"])
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--now", default=None, help="ISO UTC override for rehearsals; default is the real clock")
    a = ap.parse_args(argv)
    root = Path(a.root)
    now = datetime.fromisoformat(a.now.replace("Z", "+00:00")).astimezone(UTC) if a.now else datetime.now(UTC)
    cfg = load_rollout()
    d, store, outcomes = _paths(root)
    if a.command == "prepare":  # warm the open-source cache in a fresh checkout (the cloud runner): the same fetch the product's history loader does
        from src.config import config_dir_for, load_config
        from src.data.teams import TeamDirectory
        from src.markets.of_source import load_world

        cdir = config_dir_for(root)
        world = load_world(root, TeamDirectory.load(cdir / "team_aliases.yaml"), load_config("markets", cdir), now)
        sys.stdout.write(json.dumps({"history_matches": len(world.history), "stale_files": world.stale_files, "missing_files": world.missing_files}, default=str) + "\n")
        return 0
    if a.command == "notify":
        from src.mlops import notify

        sent = []
        state = d / "notified.json"
        alerts_path = d / "alerts.jsonl"
        for line in (alerts_path.read_text(encoding="utf-8").splitlines() if alerts_path.is_file() else []):
            al = json.loads(line)
            fresh = (now - datetime.fromisoformat(al["at"])).total_seconds() < 2 * 3600       # only what the last runs raised: the file keeps the whole history
            if al.get("severity") in notify.SEVERITIES and fresh:
                key = f"alert:{al['key']}:{al['severity']}:{now.date().isoformat()}"            # at most one message per alert and day
                sent.append((key, notify.notify_once(state, key, f"[{al['severity'].upper()}] Gölge sistem: {al['key']}\n{al['message']}")))
        gate_path = d / "shadow_gate.json"
        if gate_path.is_file():
            for fam, v in json.loads(gate_path.read_text(encoding="utf-8"))["families"].items():
                if v["status"] != "SHADOW":
                    key = f"gate:{fam}:{v['status']}"
                    sent.append((key, notify.notify_once(state, key, f"Gölge model {fam}: {v['status']}\n{v['status_reason']}\n(Öneridir, hiçbir şey otomatik değişmez.)")))
        sys.stdout.write(json.dumps({"configured": notify.configured(), "results": dict(sent)}, indent=2) + "\n")
        return 0
    if a.command == "gate":
        from . import gate as gate_mod

        res = gate_mod.evaluate(store.all(), outcomes.all(), now)
        gate_mod.write(d / "shadow_gate.json", res)
        sys.stdout.write(json.dumps({f: {"status": v["status"], "n": v["resolved_pairs_min_over_rule_targets"]} for f, v in res["families"].items()}, indent=2) + "\n")
        return 0
    if a.command == "shadow":
        rep = run_shadow(root, now, cfg=cfg)
        sys.stdout.write(json.dumps({k: v for k, v in rep.items() if k != "per_fixture_ms"}, indent=2, sort_keys=True, default=str) + "\n")
        return 0 if rep["status"] in ("OK", "SHADOW_DISABLED") else 2
    if a.command == "reconcile":
        from src.config import config_dir_for, load_config
        from src.ingestion.football_data_org import FootballDataOrgProvider

        key = load_config("ingestion", config_dir_for(root)).api_key("football-data-org")
        if not key:
            sys.stdout.write(json.dumps({"status": "NOT_CONFIGURED"}) + "\n")
            return 2
        rep = evaluation.fetch_outcomes(FootballDataOrgProvider(key), cfg["shadow"]["providers_competitions"], season_label(now), store, outcomes, now)
        sys.stdout.write(json.dumps(rep, indent=2, sort_keys=True) + "\n")
        return 0
    records, outs = store.all(), outcomes.all()
    sc = evaluation.scorecard(records, outs, now)
    if a.command == "scorecard":
        evaluation.dump(d / "scorecard.json", sc)
        evaluation.dump(d / "score_scorecard.json", evaluation.score_scorecard(ScoreForecastStore(d / "score_forecasts.jsonl").all(), outs, now))
        sys.stdout.write(json.dumps(sc, indent=2, sort_keys=True) + "\n")
        return 0
    registry = LeagueModelRegistry.load(root=root / cfg["registry_artifacts_root"])
    alerts = monitoring.run_checks(records=records, outcomes=outs, runs=_runs(d), registry=registry, licenses=load_manifest(), cfg=cfg, now=now)
    if a.command == "monitor":
        dec = monitoring.apply_alerts(alerts, cfg, root, d, now)
        sys.stdout.write(json.dumps({**dec, "alert_keys": [x.key for x in alerts]}, indent=2, sort_keys=True) + "\n")
        return 1 if dec["fail_closed"] else 0
    killed, why = kill_active(cfg, root)
    kill = {"routing": f"GLOBAL ONLY ({why})" if killed else "canary routing enabled by config"}
    if a.command == "status":
        sys.stdout.write(json.dumps({"kill": kill, "records": sc["records_total"], "resolved_fixtures": sc["resolved_fixtures"], "canary_fraction": cfg["canary"]["active_fraction"]}, indent=2) + "\n")
        return 0
    if a.command == "dashboard":
        bt = None
        hp = root / "docs" / "league_models" / "s5_holdout_report.json"
        if hp.is_file():
            h = json.loads(hp.read_text(encoding="utf-8"))
            bt = {"label": h["label"], "fixtures": h["fixtures_scored"], "1x2_log_loss_calibrated": {k: round(v["log_loss"], 4) for k, v in h["calibrated_metrics"]["1x2"].items() if k in ("elo_s2", "E_elo_bridge", "B_gbm_league")}}
        dashboard.write(d / "dashboard.html", dashboard.render(sc, records, [json.loads(x) for x in (d / "alerts.jsonl").read_text(encoding="utf-8").splitlines()] if (d / "alerts.jsonl").is_file() else [], kill, bt, now))
        sys.stdout.write(f"written {d / 'dashboard.html'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
