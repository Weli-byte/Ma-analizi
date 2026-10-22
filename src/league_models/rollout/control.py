"""Sprint 6 (ADR 0068): rollout configuration, the LeagueModelRegistry / EligibilityGate and the deterministic canary router.

Safe by construction: the kill switch (config OR the fail-closed flag file) routes every request to the global model, the canary fraction is 0 by default,
only a registry entry that the Sprint 5 gate marked APPROVED_FOR_CANARY and whose data licences are PERMITTED can ever be served, an artifact that does
not match its hash falls back to GLOBAL with an alarm, and every decision is written to an audit log that stores a salted hash of the user, never the user."""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

from src.league_models.competitions import ROOT
from src.league_models.licenses import SourceLicense
from src.league_models.prediction_router import CANARY_STATUSES, GLOBAL, PredictionRouter, RegistryEntry

from .records import _append

ROLLOUT_CONFIG = ROOT / "configs" / "league_models" / "rollout.yaml"
S5_REGISTRY = ROOT / "docs" / "league_models" / "s5_model_registry.json"
CONFIG_KEYS = {"version", "kill_switch", "kill_switch_file", "registry_artifacts_root", "shadow", "canary", "monitoring"}


class RolloutConfigError(ValueError):
    pass


@dataclass(frozen=True)
class RolloutConfig:
    raw: dict
    fingerprint: str

    def __getitem__(self, k):
        return self.raw[k]


def load_rollout(path: Path = ROLLOUT_CONFIG) -> RolloutConfig:
    text = path.read_text(encoding="utf-8")
    raw = yaml.safe_load(text)
    missing = CONFIG_KEYS - set(raw)
    if missing:
        raise RolloutConfigError(f"rollout config is missing {sorted(missing)}")
    frac = raw["canary"]["active_fraction"]
    if not 0.0 <= float(frac) <= 1.0:
        raise RolloutConfigError("canary.active_fraction must be in [0, 1]")
    fractions = [s["fraction"] for s in raw["canary"]["stages"]]
    if fractions != sorted(fractions):
        raise RolloutConfigError("canary stages must be written in increasing fraction order")
    return RolloutConfig(raw, hashlib.sha256(text.encode("utf-8")).hexdigest())


def kill_active(cfg: RolloutConfig, root: Path = ROOT) -> tuple[bool, str]:
    if cfg["kill_switch"]:
        return True, "KILL_SWITCH_CONFIG"
    if (root / cfg["kill_switch_file"]).exists():
        return True, "KILL_SWITCH_FLAG_FILE"
    return False, ""


def trip_kill_switch(cfg: RolloutConfig, reason: str, root: Path = ROOT, now: datetime | None = None) -> Path:
    """The automatic fail-closed action of a critical alarm: create the flag file. Removing it is a deliberate manual act."""
    p = root / cfg["kill_switch_file"]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"tripped_at": (now or datetime.now(UTC)).isoformat(), "reason": reason}, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return p


# ------------------------------------------------------------------------------------------------ registry and eligibility
class LeagueModelRegistry:
    """The Sprint 5 registry (immutable file) as the single source of what may run where."""

    def __init__(self, entries: list[RegistryEntry], root: Path = ROOT) -> None:
        self.entries, self.root = entries, root

    @staticmethod
    def load(path: Path = S5_REGISTRY, root: Path = ROOT) -> "LeagueModelRegistry":
        if not path.is_file():
            raise RolloutConfigError(f"registry {path.name} is missing: no model may be routed")
        data = json.loads(path.read_text(encoding="utf-8"))
        return LeagueModelRegistry([RegistryEntry(**e) for e in data["entries"]], root)

    def candidates(self, statuses) -> list[RegistryEntry]:
        return [e for e in self.entries if e.mode != GLOBAL and e.status in statuses]

    def globals(self) -> list[RegistryEntry]:
        return [e for e in self.entries if e.mode == GLOBAL]


def eligibility(entry: RegistryEntry, licenses: dict[str, SourceLicense], data_license_ids: list[str], *, require_permitted: bool = True) -> tuple[bool, str]:
    """EligibilityGate: gate status AND data licence. A model fed by a RESEARCH_ONLY source is never user-visible, whatever its score."""
    if entry.status not in CANARY_STATUSES:
        return False, f"STATUS_{entry.status}"
    for lid in data_license_ids:
        lic = licenses.get(lid)
        if lic is None:
            return False, f"LICENSE_UNKNOWN:{lid}"
        if require_permitted and lic.plan_status != "PERMITTED":
            return False, f"LICENSE_{lic.plan_status}:{lid}"
    return True, "ELIGIBLE"


# ------------------------------------------------------------------------------------------------ canary router
def bucket(salt: str, user_id: str) -> int:
    return int(hashlib.sha256(f"{salt}|{user_id}".encode()).hexdigest()[:8], 16) % 10000


@dataclass(frozen=True)
class CanaryDecision:
    served: str                 # GLOBAL or the candidate model id
    version: str
    reason: str
    bucket: int
    fraction: float
    alarms: tuple[str, ...] = ()


class CanaryRouter:
    def __init__(self, cfg: RolloutConfig, registry: LeagueModelRegistry, licenses: dict[str, SourceLicense], data_license_ids: dict[str, list[str]], audit_path: Path,
                 root: Path = ROOT, disabled_leagues: set[str] | None = None) -> None:
        self.cfg, self.registry, self.licenses = cfg, registry, licenses
        self.data_license_ids, self.audit_path, self.root = data_license_ids, audit_path, root
        self.router = PredictionRouter(registry.entries, registry.root, disabled_leagues, production_fraction=0.0)

    def _audit(self, user_id: str, target: str, league: str, stage: str, d: CanaryDecision, now: datetime) -> None:
        h = hashlib.sha256(f"{self.cfg['canary']['salt']}|audit|{user_id}".encode()).hexdigest()[:16]
        _append(self.audit_path, json.dumps({"at": now.isoformat(), "user_hash": h, "league": league, "target": target, "stage": stage, **{k: v for k, v in asdict(d).items() if k != "alarms"},
                                             "alarms": list(d.alarms), "config_fingerprint": self.cfg.fingerprint}, sort_keys=True))

    def route(self, user_id: str, league: str, target: str, stage: str, now: datetime | None = None) -> CanaryDecision:
        now = now or datetime.now(UTC)
        b = bucket(self.cfg["canary"]["salt"], user_id)
        frac = float(self.cfg["canary"]["active_fraction"])
        glob = self.router._global(target)  # raises RouterError when the global model is unusable: no invented answer

        def done(reason: str, served: str = "GLOBAL", version: str = "", alarms=()) -> CanaryDecision:
            d = CanaryDecision(served, version or glob.version, reason, b, frac, tuple(alarms))
            self._audit(user_id, target, league, stage, d, now)
            return d

        killed, why = kill_active(self.cfg, self.root)
        if killed:
            return done(why)
        if frac <= 0.0:
            return done("FRACTION_ZERO")
        if b >= int(frac * 10000):
            return done("OUTSIDE_COHORT")
        route = self.router.route(target, league, stage, use="canary")
        if route.mode == GLOBAL:
            return done(route.fallback_reason or "NO_APPROVED_MODEL", alarms=route.alarms)
        entry = next(e for e in self.registry.entries if e.model_id == route.model_id and e.target == target and e.league == league)
        ok, reason = eligibility(entry, self.licenses, self.data_license_ids.get(entry.model_id, []), require_permitted=bool(self.cfg["canary"]["require_license_permitted"]))
        if not ok:
            return done(reason)
        return done("CANARY", route.model_id, route.version)


def stage_gate(cfg: RolloutConfig, current_stage: str, observed_days: float, resolved_pairs: int, ci_upper_vs_global: float | None, critical_alarms: int, licence_permitted: bool) -> dict:
    """May the owner advance from `current_stage`? The answer is advice with reasons; the code never changes the fraction."""
    stages = cfg["canary"]["stages"]
    names = [s["name"] for s in stages]
    if current_stage not in names:
        raise RolloutConfigError(f"unknown stage {current_stage!r}")
    cur = stages[names.index(current_stage)]
    nxt = stages[names.index(current_stage) + 1]["name"] if names.index(current_stage) + 1 < len(stages) else None
    checks = {
        "min_observation_days": observed_days >= cur["min_observation_days"],
        "min_resolved_pairs": resolved_pairs >= cur["min_resolved_pairs"],
        "interval_below_zero_vs_global": ci_upper_vs_global is not None and ci_upper_vs_global < 0,
        "no_critical_alarm": critical_alarms == 0,
        "licence_permitted": licence_permitted,
    }
    ok = nxt is not None and all(checks.values())
    return {"current": current_stage, "next": nxt, "may_advance": ok, "checks": checks,
            "note": "advice only; a stage change is a config edit by the owner. With few resolved pairs no improvement is claimed." if not ok else "criteria met; the owner decides"}  # fmt: skip
