"""Sprint 5 (ADR 0067): the PredictionRouter contract (dry run). It SELECTS which registered model would serve a (target, league, stage) request; it never
produces a probability and never fabricates one. Production traffic is closed (fraction 0.0 until Sprint 6): only an artifact whose gate status allows it
can be chosen, and only for `canary` or `shadow` use. A registered artifact that is missing, corrupted or does not match its hash falls back to the global
model with an alarm; if the global model is also unusable the router raises (a real error, no invented answer)."""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

GLOBAL, LEAGUE_ADAPTED, LOCAL, BLENDED = "GLOBAL", "LEAGUE_ADAPTED", "LOCAL", "BLENDED"
MODES = (GLOBAL, LEAGUE_ADAPTED, LOCAL, BLENDED)
USES = ("production", "canary", "shadow")
CANARY_STATUSES = {"APPROVED_FOR_CANARY"}
SHADOW_STATUSES = {"APPROVED_FOR_CANARY", "SHADOW_INCONCLUSIVE"}


class RouterError(RuntimeError):
    """No usable model: the caller must fail, not guess."""


@dataclass(frozen=True)
class RegistryEntry:
    model_id: str
    version: str
    mode: str
    target: str
    league: str              # a competition id or "*" for the global model of the target
    status: str              # gate status, or GLOBAL_REFERENCE
    artifact_path: str | None
    artifact_sha256: str
    artifact_kind: str       # booster_file | recipe


@dataclass
class RouteDecision:
    target: str
    league: str
    stage: str
    use: str
    mode: str
    model_id: str
    version: str
    fallback_reason: str | None
    alarms: list[str] = field(default_factory=list)


def artifact_ok(entry: RegistryEntry, root: Path) -> tuple[bool, str]:
    """A booster file must exist and hash to the registered value; a recipe artifact is checked by the hash of its canonical text (stored inline)."""
    if entry.artifact_path is None:
        return False, "no artifact registered"
    p = root / entry.artifact_path
    if not p.is_file():
        return False, f"artifact file missing: {entry.artifact_path}"
    if hashlib.sha256(p.read_bytes()).hexdigest() != entry.artifact_sha256:
        return False, f"artifact hash mismatch: {entry.artifact_path}"
    return True, "ok"


class PredictionRouter:
    def __init__(self, registry: list[RegistryEntry], root: Path, disabled_leagues: set[str] | None = None, production_fraction: float = 0.0) -> None:
        for e in registry:
            if e.mode not in MODES:
                raise ValueError(f"unknown mode {e.mode!r}")
        self.registry, self.root = registry, root
        self.disabled = set(disabled_leagues or ())
        self.production_fraction = production_fraction
        self.alarms: list[str] = []

    def _global(self, target: str) -> RegistryEntry:
        for e in self.registry:
            if e.mode == GLOBAL and e.target == target and e.league == "*":
                ok, why = artifact_ok(e, self.root)
                if not ok:
                    raise RouterError(f"the global model for {target} is unusable ({why}); refusing to invent a prediction")
                return e
        raise RouterError(f"no global model registered for {target}")

    def route(self, target: str, league: str, stage: str, *, use: str, data_available: bool = True, force_model_id: str | None = None) -> RouteDecision:
        if use not in USES:
            raise ValueError(f"unknown use {use!r}")
        glob = self._global(target)
        base = dict(target=target, league=league, stage=stage, use=use)
        candidates = [e for e in self.registry if e.mode != GLOBAL and e.target == target and e.league == league]
        if force_model_id is not None:
            forced = [e for e in self.registry if e.model_id == force_model_id]
            allowed = CANARY_STATUSES if use == "canary" else SHADOW_STATUSES if use == "shadow" else set()
            if not forced or forced[0].status not in allowed:
                raise RouterError(f"model {force_model_id!r} is not approved for {use}: unapproved models cannot be routed")
            candidates = forced

        def fall(reason: str, alarm: str | None = None) -> RouteDecision:
            alarms = [alarm] if alarm else []
            self.alarms += alarms
            return RouteDecision(**base, mode=GLOBAL, model_id=glob.model_id, version=glob.version, fallback_reason=reason, alarms=alarms)

        if use == "production":
            return fall("PRODUCTION_CLOSED: production traffic opens in Sprint 6")
        if league in self.disabled:
            return fall("LEAGUE_DISABLED")
        if not data_available:
            return fall("DATA_UNAVAILABLE")
        allowed = CANARY_STATUSES if use == "canary" else SHADOW_STATUSES
        eligible = [e for e in candidates if e.status in allowed]
        if not eligible:
            return fall("NO_APPROVED_MODEL" if not candidates else "MODEL_NOT_APPROVED_FOR_" + use.upper())
        e = eligible[0]
        ok, why = artifact_ok(e, self.root)
        if not ok:
            return fall("ARTIFACT_INVALID", f"{e.model_id}: {why}")
        return RouteDecision(**base, mode=e.mode, model_id=e.model_id, version=e.version, fallback_reason=None)
