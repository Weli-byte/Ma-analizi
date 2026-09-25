"""S3 baselines. Reference points for later models — deliberately NOT optimized.

Each model: fit(train_rows) then predict_proba(rows) -> (n, 3) array [home, draw, away].
A row of NaN means "this model cannot predict this fixture" (excluded from the common eval set).
"""

from collections import Counter

import numpy as np

from src.evaluation.dataset import EvalRow

# preference order; closing first. Closing odds are a REFERENCE bar, never a time-aligned signal.
MARKET_SOURCES = ["closing:agg_avg", "closing:B365", "pre_match:agg_avg", "pre_match:B365"]


class BaselineModel:
    model_id: str
    model_version = "1.0.0"
    model_class = "baseline"  # baseline | reference_market_baseline
    required_features: tuple[str, ...] = ()

    def __init__(self) -> None:
        self.diagnostics: dict[str, object] = {}

    def fit(self, train: list[EvalRow]) -> "BaselineModel":
        return self

    def predict_proba(self, rows: list[EvalRow]) -> np.ndarray:
        raise NotImplementedError


class AlwaysHome(BaselineModel):
    """p = [1, 0, 0]. Degenerate on purpose: log loss is dominated by the eps clip."""

    model_id = "always_home"

    def predict_proba(self, rows):
        return np.tile([1.0, 0.0, 0.0], (len(rows), 1))


def _freq(rows: list[EvalRow]) -> np.ndarray:
    c = Counter(r.outcome for r in rows)
    n = sum(c.values())
    if n == 0:
        raise ValueError("cannot fit prior on zero rows")
    return np.array([c[0], c[1], c[2]], dtype=float) / n


class HistoricalPrior(BaselineModel):
    """Outcome frequencies of the training period, per league (global fallback)."""

    model_id = "historical_prior"

    def fit(self, train):
        self.global_prior = _freq(train)
        self.league_prior = {
            lg: _freq([r for r in train if r.league_id == lg]) for lg in sorted({r.league_id for r in train})
        }
        self.diagnostics = {
            "global_prior": self.global_prior.round(4).tolist(),
            "league_prior": {k: v.round(4).tolist() for k, v in self.league_prior.items()},
            "training_rows": len(train),
        }
        return self

    def predict_proba(self, rows):
        return np.array([self.league_prior.get(r.league_id, self.global_prior) for r in rows])


class RecentFormNaive(BaselineModel):
    """Split non-draw mass by recent form: share prop. to 1 + points last 5 (fixed rule, no
    weight fitting). Draw probability = training draw rate. Missing form -> historical prior;
    every fallback is counted here AND reported by the availability report (never silent)."""

    model_id = "recent_form_naive"
    HOME, AWAY = "home_form_points_5", "away_form_points_5"
    required_features = (HOME, AWAY)

    def fit(self, train):
        self._prior = HistoricalPrior().fit(train)
        self.draw_rate = float(self._prior.global_prior[1])
        return self

    def predict_proba(self, rows):
        out, fallback = [], 0
        for r in rows:
            h, a = r.features.get(self.HOME), r.features.get(self.AWAY)
            if h is None or a is None:
                out.append(self._prior.predict_proba([r])[0])
                fallback += 1
                continue
            sh, sa = 1.0 + h, 1.0 + a
            d = self.draw_rate
            out.append([(1 - d) * sh / (sh + sa), d, (1 - d) * sa / (sh + sa)])
        self.diagnostics = {"draw_rate": round(self.draw_rate, 4), "prior_fallback_rows": fallback}
        return np.array(out)


class MarketImplied(BaselineModel):
    """De-vigged (proportional normalisation) bookmaker odds. NaN when no odds exist.

    REFERENCE_MARKET_BASELINE: uses CLOSING odds whose timestamp is unknown, so it is a reference
    bar for probability quality, not a time-aligned trading signal (ADR 0007)."""

    model_id = "market_implied"
    model_class = "reference_market_baseline"

    def predict_proba(self, rows):
        out, used = [], Counter()
        for r in rows:
            for src in MARKET_SOURCES:
                if src in r.odds:
                    inv = 1.0 / np.array(r.odds[src])
                    out.append(inv / inv.sum())
                    used[src] += 1
                    break
            else:
                out.append([np.nan] * 3)
        self.diagnostics = {
            "source_usage": dict(sorted(used.items())),
            "no_odds_rows": len(rows) - sum(used.values()),
            "timestamp_quality": "unknown",
        }
        return np.array(out)


REGISTRY: dict[str, type[BaselineModel]] = {
    c.model_id: c for c in (AlwaysHome, HistoricalPrior, RecentFormNaive, MarketImplied)
}


def build_models(names: list[str]) -> list[BaselineModel]:
    unknown = [n for n in names if n not in REGISTRY]
    if unknown:
        raise KeyError(f"unknown baseline models {unknown}; available: {sorted(REGISTRY)}")
    return [REGISTRY[n]() for n in names]


def default_baselines() -> list[BaselineModel]:
    return build_models(list(REGISTRY))
