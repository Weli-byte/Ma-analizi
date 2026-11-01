"""Canary policy for the served market model (ADR 0097).

Stages shadow -> 5 % -> 25 % -> 50 % -> 100 %, each with a minimum observation window and a minimum number of SETTLED matches of the canary arm before a recommendation can be made
(a small sample never produces a percentage claim). Stop criteria (any one triggers ROLLBACK to the champion): a critical SLO breach, a failed leakage gate, the challenger's paired
log-loss interval against the champion wholly above 0 on the canary window, or its ECE worse by more than ECE_STOP. Without a challenger bound in `configs/release/canary.yaml` the stage is
shadow and every user gets the champion. Only a candidate whose sprint 17 promotion gate says PROMOTION_CANDIDATE may be bound (`bindable`).

    python -m src.release.canary status
    python -m src.release.canary evaluate --evidence <json>     # prints the recommendation; changes nothing
"""

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from src.cli_utils import configure_output

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "configs" / "release" / "canary.yaml"


@dataclass(frozen=True)
class Stage:
    name: str
    fraction: float
    min_days: int
    min_settled: int


STAGES = (Stage("shadow", 0.0, 14, 150), Stage("canary_5", 0.05, 14, 200), Stage("canary_25", 0.25, 14, 300), Stage("canary_50", 0.50, 14, 400), Stage("full_100", 1.0, 28, 600))
ECE_STOP = 0.02


def load(path: Path = CONFIG) -> dict:
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if cfg["stage"] not in {s.name for s in STAGES}:
        raise ValueError(f"unknown canary stage {cfg['stage']!r}")
    if cfg["stage"] != "shadow" and not cfg.get("challenger"):
        raise ValueError("a canary stage needs a bound challenger")
    return cfg


def stage(name: str) -> Stage:
    return next(s for s in STAGES if s.name == name)


def route(user_id: str, cfg: dict) -> str:
    """'champion' or 'challenger' for one user; deterministic, so a user never flips between models inside a stage."""
    if cfg.get("kill_switch") or not cfg.get("challenger"):
        return "champion"
    bucket = int(hashlib.sha256(f"{cfg['salt']}|{user_id}".encode()).hexdigest(), 16) % 10000
    return "challenger" if bucket < stage(cfg["stage"]).fraction * 10000 else "champion"


def bindable(candidate: dict) -> bool:
    return candidate.get("promotion_gate") == "PROMOTION_CANDIDATE" and candidate.get("leakage_gate") == "PASS"


def evaluate(cfg: dict, ev: dict) -> dict:
    """`ev`: days observed in the stage, settled matches of the canary arm, the paired delta (challenger minus champion, interval), ECE delta, critical SLO breaches, leakage status."""
    if not cfg.get("challenger"):
        return {"action": "HOLD", "reason": "no challenger bound: every user gets the champion", "stage": cfg["stage"]}
    stops = []
    if ev.get("critical_slo"):
        stops.append("critical SLO breach: " + ", ".join(ev["critical_slo"]))
    if ev.get("leakage") != "PASS":
        stops.append("leakage gate not passed")
    if ev.get("delta_interval") and ev["delta_interval"][0] > 0:
        stops.append("challenger worse than the champion (paired interval wholly above 0)")
    if ev.get("ece_delta") is not None and ev["ece_delta"] > ECE_STOP:
        stops.append(f"calibration worse by {ev['ece_delta']:.3f} (> {ECE_STOP})")
    if stops:
        return {"action": "ROLLBACK", "reason": "; ".join(stops), "stage": cfg["stage"], "next": {"challenger": None, "stage": "shadow"}}
    s = stage(cfg["stage"])
    if ev.get("days", 0) < s.min_days or ev.get("settled", 0) < s.min_settled:
        return {"action": "HOLD", "reason": f"window not complete: {ev.get('days', 0)}/{s.min_days} days, {ev.get('settled', 0)}/{s.min_settled} settled matches", "stage": s.name}
    if not (ev.get("delta_interval") and ev["delta_interval"][1] < 0):
        return {"action": "HOLD", "reason": "no demonstrated improvement on the canary window", "stage": s.name}
    i = [x.name for x in STAGES].index(s.name)
    if i + 1 == len(STAGES):
        return {"action": "COMPLETE", "reason": "the challenger serves everyone; make it the champion by an ADR", "stage": s.name}
    return {"action": "ADVANCE_RECOMMENDED", "reason": "window complete, improvement demonstrated, no stop criterion", "stage": s.name, "next": {"stage": STAGES[i + 1].name}}


def apply(cfg: dict, decision: dict, path: Path = CONFIG) -> dict:
    """Only a ROLLBACK is applied by code (fail safe); an advance is the owner's edit of the file."""
    if decision["action"] != "ROLLBACK":
        return cfg
    new = {**cfg, **decision["next"]}
    text = Path(path).read_text(encoding="utf-8")
    def rewrite(line: str) -> str:
        if line.startswith("challenger:"):
            return f"challenger: null                 # rolled back: {decision['reason'][:80]}"
        return "stage: shadow" if line.startswith("stage:") else line

    Path(path).write_text("\n".join(rewrite(line) for line in text.splitlines()) + "\n", encoding="utf-8", newline="\n")
    return new


def main(argv=None) -> int:
    configure_output()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=("status", "evaluate"))
    ap.add_argument("--evidence", default=None)
    ap.add_argument("--config", default=str(CONFIG))
    a = ap.parse_args(argv)
    cfg = load(Path(a.config))
    if a.action == "status":
        print(json.dumps({**cfg, "fraction": stage(cfg["stage"]).fraction}, sort_keys=True))
    else:
        print(json.dumps(evaluate(cfg, json.loads(Path(a.evidence).read_text(encoding="utf-8")) if a.evidence else {}), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
