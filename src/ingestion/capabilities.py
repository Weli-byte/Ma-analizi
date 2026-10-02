"""Which implemented provider supports which capability (ADR 0028) -- the factual answer to "what
real data do we have?", derived from each provider's own `ProviderMeta`, never hand-maintained
prose. A capability nobody supports is reported as `NONE` (and stays UNKNOWN downstream)."""

from src.live.feeds import OLDB_META
from src.odds.espn import META as ESPN_META

from .football_data_org import META as FDORG_META
from .fpl import META as FPL_META
from .interfaces import Capability, ProviderMeta

PROVIDER_METAS: tuple[ProviderMeta, ...] = (FDORG_META, FPL_META, OLDB_META, ESPN_META)


def capability_report() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for cap in Capability:
        providers = [m for m in PROVIDER_METAS if m.supports(cap)]
        out[cap.value] = {
            "supported_by": [m.name for m in providers],
            "status": "SUPPORTED" if providers else "NONE",
            "license_status": sorted({m.license_status for m in providers}),
            "verified_on": {m.name: m.verified_on for m in providers},
            "coverage": {m.name: m.coverage for m in providers},
        }
    return out
