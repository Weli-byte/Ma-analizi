"""ADR 0076: the pre-registered live gate of the exploratory shadow families. Reads the stored shadow records and the append-only outcomes, pairs each family with the production-family
global record of the same fixture and snapshot (t-24h only), and applies `configs/league_models/shadow_gate.yaml` (hash-pinned in the tests). It decides nothing about routing."""

import hashlib
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import yaml

from src.league_models import bench_metrics as bm
from src.league_models.competitions import ROOT

from .evaluation import _loss, pairs

GATE_PATH = ROOT / "configs" / "league_models" / "shadow_gate.yaml"


def load_gate(path: Path = GATE_PATH) -> dict:
    text = path.read_text(encoding="utf-8")
    gate = yaml.safe_load(text)
    gate["_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return gate


def evaluate(records: list[dict], outcomes: dict[str, dict], now: datetime, gate: dict | None = None) -> dict:
    gate = gate or load_gate()
    prs = pairs(records, outcomes)
    interim, decision = int(gate["interim_look_at"]), int(gate["decision_at"])
    out: dict = {"gate_version": gate["version"], "gate_sha256": gate["_sha256"], "generated_at_utc": now.isoformat(), "stage": gate["stage"], "interim_look_at": interim, "decision_at": decision,
                 "kind": "PROSPECTIVE shadow evidence; recommends, never routes", "families": {}}  # fmt: skip
    for fam in gate["families"]:
        entry: dict = {"targets": {}}
        verdicts: dict[str, dict] = {}
        for target in [*gate["rule_targets"], *gate["information_targets"]]:
            rows = prs.get((gate["stage"], target, f"{fam}:{target}"), [])
            n = len(rows)
            if n < 2:
                entry["targets"][target] = {"resolved_pairs": n, "verdict": "TOO_FEW_PAIRS", "rule_target": target in gate["rule_targets"]}
                if target in gate["rule_targets"]:
                    verdicts[target] = {"n": n, "lower": float("-inf"), "upper": float("inf")}
                continue
            pc = np.array([x[0] for x in rows])
            pg = np.array([x[1] for x in rows])
            y = np.array([x[2] for x in rows])
            lc, lg = _loss(target, pc, y), _loss(target, pg, y)
            blocks = [bm.iso_week_block(datetime.fromisoformat(x[3])) for x in rows]
            d = bm.paired_block_bootstrap(lc, lg, blocks, 2000, 20261025, 0.05)
            verdict = "FAMILY_BETTER" if d.upper < 0 else ("FAMILY_WORSE" if d.lower > 0 else "NO_DIFFERENCE_DEMONSTRATED")
            entry["targets"][target] = {"resolved_pairs": n, "log_loss_family": round(float(lc.mean()), 5), "log_loss_global": round(float(lg.mean()), 5), "delta": round(d.delta, 5),
                                        "ci95": [round(d.lower, 5), round(d.upper, 5)], "verdict": verdict, "rule_target": target in gate["rule_targets"]}  # fmt: skip
            if target in gate["rule_targets"]:
                verdicts[target] = {"n": n, "lower": d.lower, "upper": d.upper}
        n_min = min((v["n"] for v in verdicts.values()), default=0)
        if verdicts and n_min >= interim and any(v["lower"] > 0 for v in verdicts.values()):
            status, why = "RETIRE", "clearly worse than the global model on at least one rule target"
        elif len(verdicts) == len(gate["rule_targets"]) and n_min >= decision and all(v["upper"] < 0 for v in verdicts.values()):
            status, why = "PROMOTION_CANDIDATE", "clearly better than the global model on both rule targets (a recommendation to the owner, nothing is routed)"
        else:
            status, why = "SHADOW", f"{n_min} of {decision} resolved pairs; no clear verdict yet" if n_min < decision else "no clear difference on both rule targets"
        entry.update({"status": status, "status_reason": why, "resolved_pairs_min_over_rule_targets": n_min})
        out["families"][fam] = entry
    return out


def write(path: Path, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
