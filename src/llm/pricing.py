"""Central price lookup (ADR 0024). Prices live in `configs/pricing.yaml`, never in provider code.
An unknown model yields `None` (no estimate) -- never a fabricated 0."""

from dataclasses import dataclass
from pathlib import Path

from src.config import load_config

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs"


@dataclass(frozen=True)
class PriceTable:
    version: str
    retrieved_at_utc: str
    models: dict[str, tuple[float, float]]  # "provider:model" -> (input, output) USD per 1M tokens

    def has(self, provider: str, model: str) -> bool:
        return f"{provider}:{model}" in self.models

    def estimate(
        self, provider: str, model: str, input_tokens: int | None, output_tokens: int | None
    ) -> float | None:
        rates = self.models.get(f"{provider}:{model}")
        if rates is None or input_tokens is None or output_tokens is None:
            return None
        return round((input_tokens * rates[0] + output_tokens * rates[1]) / 1_000_000, 8)


def load_price_table(config_dir: Path = CONFIG_DIR) -> PriceTable:
    cfg = load_config("pricing", config_dir)
    models = {k: (v.input_per_mtok, v.output_per_mtok) for k, v in cfg.models.items()}
    return PriceTable(cfg.pricing_version, cfg.retrieved_at_utc, models)
