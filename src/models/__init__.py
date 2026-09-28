from src.config import EloConfig, PoissonConfig

from .baselines import REGISTRY as _BASELINE_REGISTRY
from .baselines import (
    AlwaysHome,
    BaselineModel,
    HistoricalPrior,
    MarketImplied,
    RecentFormNaive,
)
from .elo import EloModel, RatingEvent
from .poisson_dc import DixonColesModel, PoissonModel, TeamStrength

REGISTRY: dict[str, type[BaselineModel]] = {
    **_BASELINE_REGISTRY,
    EloModel.model_id: EloModel,
    PoissonModel.model_id: PoissonModel,
    DixonColesModel.model_id: DixonColesModel,
}


def build_models(
    names: list[str],
    elo_config: EloConfig | None = None,
    poisson_config: PoissonConfig | None = None,
) -> list[BaselineModel]:
    unknown = [n for n in names if n not in REGISTRY]
    if unknown:
        raise KeyError(f"unknown model {unknown}; available: {sorted(REGISTRY)}")
    out = []
    for n in names:
        if n == EloModel.model_id and elo_config is not None:
            out.append(EloModel(**elo_config.model_dump()))
        elif n in (PoissonModel.model_id, DixonColesModel.model_id) and poisson_config is not None:
            out.append(REGISTRY[n](**poisson_config.model_dump()))
        else:
            out.append(REGISTRY[n]())
    return out


def default_baselines() -> list[BaselineModel]:
    return build_models(list(REGISTRY))


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
    "TeamStrength",
    "build_models",
    "default_baselines",
]
