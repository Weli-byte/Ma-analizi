from src.config import EloConfig, GBMConfig, PoissonConfig

from .baselines import REGISTRY as _BASELINE_REGISTRY
from .baselines import (
    AlwaysHome,
    BaselineModel,
    HistoricalPrior,
    MarketImplied,
    RecentFormNaive,
)
from .elo import EloModel, RatingEvent
from .gbm import GBMModel, LGBMModel, XGBModel
from .poisson_dc import DixonColesJointMLE, DixonColesModel, PoissonModel, TeamStrength

REGISTRY: dict[str, type[BaselineModel]] = {
    **_BASELINE_REGISTRY,
    EloModel.model_id: EloModel,
    PoissonModel.model_id: PoissonModel,
    DixonColesModel.model_id: DixonColesModel,
    DixonColesJointMLE.model_id: DixonColesJointMLE,
    XGBModel.model_id: XGBModel,
    LGBMModel.model_id: LGBMModel,
}

_GBM_IDS = {XGBModel.model_id, LGBMModel.model_id}
_POISSON_FAMILY_IDS = {PoissonModel.model_id, DixonColesModel.model_id, DixonColesJointMLE.model_id}
# Research-only: never part of the "run everything sensible" convenience helper below. Must be
# named explicitly (build_models([...])) -- e.g. for the Phase 7 DC_v1-vs-DC_v2 walk-forward
# comparison this variant exists for (ADR-0014's Phase 7 amendment).
_RESEARCH_ONLY_IDS = {DixonColesJointMLE.model_id}


def build_models(
    names: list[str],
    elo_config: EloConfig | None = None,
    poisson_config: PoissonConfig | None = None,
    gbm_config: GBMConfig | None = None,
) -> list[BaselineModel]:
    unknown = [n for n in names if n not in REGISTRY]
    if unknown:
        raise KeyError(f"unknown model {unknown}; available: {sorted(REGISTRY)}")
    out = []
    for n in names:
        if n == EloModel.model_id and elo_config is not None:
            params = elo_config.model_dump()
            params.pop("tuning", None)  # tuning config drives src.models.elo_tuning, not __init__
            out.append(EloModel(**params))
        elif n in _POISSON_FAMILY_IDS and poisson_config is not None:
            out.append(REGISTRY[n](**poisson_config.model_dump()))
        elif n in _GBM_IDS and gbm_config is not None:
            out.append(REGISTRY[n](**gbm_config.model_dump()))
        else:
            out.append(REGISTRY[n]())
    return out


def default_baselines() -> list[BaselineModel]:
    return build_models([n for n in REGISTRY if n not in _RESEARCH_ONLY_IDS])


__all__ = [
    "REGISTRY",
    "AlwaysHome",
    "BaselineModel",
    "HistoricalPrior",
    "MarketImplied",
    "RecentFormNaive",
    "EloModel",
    "RatingEvent",
    "PoissonModel",
    "DixonColesModel",
    "DixonColesJointMLE",
    "TeamStrength",
    "GBMModel",
    "XGBModel",
    "LGBMModel",
    "build_models",
    "default_baselines",
]
