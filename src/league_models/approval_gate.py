"""Sprint 5 (ADR 0067): the model approval gate. A pre-declared, configurable and auditable decision policy (configs/league_models/approval_policy.yaml).

Inputs are measured facts about one (league, target) cell and one candidate; the output is a status and the full list of checks with their values.
Accuracy is reported but can never open the gate. The thresholds are a product decision, not a mathematical guarantee; the Holm adjustment only
controls the family-wise error of the confirmation tests that were actually run."""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .competitions import ROOT

POLICY_PATH = ROOT / "configs" / "league_models" / "approval_policy.yaml"
APPROVED, SHADOW, REJECTED, BLOCKED, GLOBAL_KEPT = "APPROVED_FOR_CANARY", "SHADOW_INCONCLUSIVE", "REJECTED", "BLOCKED", "GLOBAL_KEPT"
STATUSES = (APPROVED, SHADOW, REJECTED, BLOCKED, GLOBAL_KEPT)


def load_policy(path: Path = POLICY_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def holm(p_values: dict, alpha: float) -> dict:
    """Holm-Bonferroni step-down: True for every hypothesis rejected while controlling the family-wise error at `alpha`."""
    order = sorted(p_values, key=lambda k: (p_values[k], str(k)))
    m = len(order)
    out = dict.fromkeys(order, False)
    for i, k in enumerate(order):
        if p_values[k] <= alpha / (m - i):
            out[k] = True
        else:
            break
    return out


@dataclass
class PairedVsReference:
    delta_log_loss: float
    ci_lower: float
    ci_upper: float
    p_one_sided: float            # share of bootstrap draws with delta >= 0 (small = the candidate is better)
    delta_brier: float
    delta_accuracy: float = 0.0   # reported only
    delta_ece: float = 0.0        # candidate minus reference, calibrated


@dataclass
class CellEvidence:
    league: str
    target: str
    candidate: str
    n_rows: int
    coverage: float
    training_matches: int
    local_model: bool
    license_status: str
    no_leakage: bool
    snapshot_lineage: bool
    artifact_valid: bool
    latency_ms: float
    refs: dict[str, PairedVsReference] = field(default_factory=dict)
    holm_pass: bool = False


@dataclass
class CellDecision:
    status: str
    reasons: list[str]
    checks: list[dict]


def _check(checks: list[dict], name: str, passed: bool, detail) -> bool:
    checks.append({"check": name, "passed": bool(passed), "detail": detail})
    return bool(passed)


def evaluate_cell(policy: dict, ev: CellEvidence) -> CellDecision:
    g = policy["holdout_gate"]
    checks: list[dict] = []
    blockers: list[str] = []
    if ev.n_rows <= 0:
        return CellDecision(BLOCKED, ["no holdout rows for this cell"], [{"check": "has_rows", "passed": False, "detail": 0}])
    if not _check(checks, "license", ev.license_status == g["require_license_status"], ev.license_status):
        blockers.append(f"license status is {ev.license_status}, not {g['require_license_status']}")
    if not _check(checks, "no_leakage", ev.no_leakage, ev.no_leakage):
        blockers.append("a leakage audit failed")
    if not _check(checks, "snapshot_lineage", ev.snapshot_lineage, ev.snapshot_lineage):
        blockers.append("the data snapshot differs from the pre-registered one")
    if not _check(checks, "coverage", ev.coverage >= g["min_oos_coverage"], round(ev.coverage, 4)):
        blockers.append(f"out-of-sample coverage {ev.coverage:.3f} is below {g['min_oos_coverage']}")
    if blockers:
        return CellDecision(BLOCKED, blockers, checks)

    rejected: list[str] = []
    if not _check(checks, "model_artifact_valid", ev.artifact_valid, ev.artifact_valid):
        rejected.append("the model artifact is invalid")
    lat_ok = ev.latency_ms <= g["max_latency_ms_per_fixture"]
    if not _check(checks, "no_material_latency_regression", lat_ok, round(ev.latency_ms, 3)):
        rejected.append(f"latency {ev.latency_ms:.1f} ms per fixture exceeds {g['max_latency_ms_per_fixture']}")
    worse = [r for r, s in ev.refs.items() if s.ci_lower > 0]
    _check(checks, "not_significantly_worse_than_any_reference", not worse, worse or "none")
    if worse:
        rejected.append("significantly worse than " + ", ".join(worse))
    ece_bad = [r for r, s in ev.refs.items() if s.delta_ece > g["max_ece_regression"]]
    _check(checks, "no_material_calibration_regression", not ece_bad, ece_bad or "none")
    if ece_bad:
        rejected.append("calibration (ECE) materially worse than " + ", ".join(ece_bad))
    if rejected:
        return CellDecision(REJECTED, rejected, checks)

    need = g["min_training_matches"]["local_models" if ev.local_model else "uefa" if ev.league.startswith("UEFA") else "domestic"]
    enough_train = _check(checks, "min_training_data", ev.training_matches >= need, {"training_matches": ev.training_matches, "required": need})
    enough_rows = _check(checks, "min_holdout_rows", ev.n_rows >= g["min_holdout_rows"], {"rows": ev.n_rows, "required": g["min_holdout_rows"]})
    below_all = all(s.ci_upper < 0 and s.delta_log_loss < 0 for s in ev.refs.values()) and bool(ev.refs)
    _check(checks, "baseline_delta_log_loss_below_zero_with_confidence", below_all, {r: [round(s.ci_lower, 5), round(s.ci_upper, 5)] for r, s in ev.refs.items()})
    brier_ok = all(s.delta_brier < 0 for s in ev.refs.values()) if g["require_brier_improvement"] else True
    _check(checks, "brier_not_worse", brier_ok, {r: round(s.delta_brier, 5) for r, s in ev.refs.items()})
    _check(checks, "holm_adjusted_confirmation", ev.holm_pass, ev.holm_pass)
    _check(checks, "accuracy_cannot_open_the_gate", True, {r: round(s.delta_accuracy, 4) for r, s in ev.refs.items()})
    if enough_train and enough_rows and below_all and brier_ok and ev.holm_pass:
        return CellDecision(APPROVED, ["every gate passed; the log-loss interval is below 0 against every reference and survives the Holm adjustment"], checks)
    why = []
    if not enough_train:
        why.append("training volume below the tier for this model type")
    if not enough_rows:
        why.append("too few holdout rows for a confirmation")
    if not below_all:
        why.append("the log-loss interval against at least one reference includes 0")
    if not brier_ok:
        why.append("Brier is not better against every reference")
    if below_all and not ev.holm_pass:
        why.append("the interval excludes 0 but does not survive the multiple-comparison adjustment")
    return CellDecision(SHADOW, why, checks)
