"""Odds math (ADR 0030). Pure functions; selections are ordered (H, D, A) like every 1X2 vector.

- implied probability  = 1 / decimal odds (includes the bookmaker margin);
- de-vig (proportional): implied probabilities divided by their sum (the "fair" probabilities under
  the proportional-margin assumption; other methods, e.g. Shin, are not used);
- edge = model probability - de-vigged market probability;
- EV   = model probability * decimal odds - 1  (expected profit per 1 unit staked, at the OFFERED
  price, i.e. including the margin);
- CLV  = price taken / de-vigged closing price - 1, with fair closing price = 1 / de-vigged prob.
"""

import numpy as np


def implied_probs(odds) -> np.ndarray:
    o = np.asarray(odds, dtype=float)
    if (o <= 1.0).any():
        raise ValueError("decimal odds must be > 1")
    return 1.0 / o


def overround(odds) -> float:
    return float(implied_probs(odds).sum() - 1.0)


def devig(odds) -> np.ndarray:
    p = implied_probs(odds)
    return p / p.sum()


def edge(model_probs, odds) -> np.ndarray:
    return np.asarray(model_probs, dtype=float) - devig(odds)


def ev(model_probs, odds) -> np.ndarray:
    return np.asarray(model_probs, dtype=float) * np.asarray(odds, dtype=float) - 1.0


def clv(odds_taken: float, closing_odds_triple, selection_index: int) -> float:
    """Positive = the price taken was better than the fair closing price."""
    fair_close_price = 1.0 / devig(closing_odds_triple)[selection_index]
    return float(odds_taken / fair_close_price - 1.0)
