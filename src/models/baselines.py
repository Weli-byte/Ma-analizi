"""S3 baselines. Reference points for later models — deliberately NOT optimized.

Each model: fit(train_rows) then predict_proba(rows) -> (n, 3) array [home, draw, away].
A row of NaN means "this model cannot predict this fixture" (excluded from the common eval set).
"""

from collections import Counter

import numpy as np

from src.evaluation.dataset import EvalRow

MARKET_SOURCES = [  # preference order; closing first (reference bar only)
    "closing:Avg", "closing:B365", "pre_match_unspecified:Avg", "pre_match_unspecified:B365",
]  # fmt: skip


class BaselineModel:
    model_id: str
    model_version = "1.0.0"

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
            lg: _freq([r for r in train if r.league_id == lg])
            for lg in {r.league_id for r in train}
        }
        self.diagnostics = {
            "global_prior": self.global_prior.round(4).tolist(),
            "league_prior": {k: v.round(4).tolist() for k, v in sorted(self.league_prior.items())},
            "training_rows": len(train),
        }
        return self

    def predict_proba(self, rows):
        return np.array([self.league_prior.get(r.league_id, self.global_prior) for r in rows])


class RecentFormNaive(BaselineModel):
    """Split non-draw mass by recent form: share prop. to 1 + points last 5 (fixed rule, no
    weight fitting). Draw probability = training draw rate. Missing form -> historical prior."""

    model_id = "recent_form_naive"
    HOME, AWAY = "home_form_points_5", "away_form_points_5"

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
    Uses closing odds when present => a reference bar, not a pre-cutoff signal."""

    model_id = "market_implied"

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
        }
        return np.array(out)


def default_baselines() -> list[BaselineModel]:
    return [AlwaysHome(), HistoricalPrior(), RecentFormNaive(), MarketImplied()]
