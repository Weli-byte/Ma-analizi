"""In-play 1X2 model (ADR 0029): pre-match goal rates + observed score/time -> outcome probabilities.

Method: remaining goals of each side are independent Poisson variables with rate
`pre-match rate * remaining_minutes / 90` (constant scoring intensity over the match). The final
goal difference is the current difference plus the difference of the two remaining counts; the
1X2 probabilities are read off that distribution. This is a deliberately simple, transparent
model. It does NOT use cards, substitutions, VAR, lineups or injuries (no feed supplies the first
three; the rest have no validated effect here), and it assumes a flat intensity (no late-goal
surge, no stoppage-time knowledge beyond `MIN_REMAINING_MIN`).
"""

import numpy as np
from scipy.stats import poisson

MATCH_MINUTES = 90.0
MIN_REMAINING_MIN = 2.0  # while IN_PLAY past minute ~88: stoppage time is unknown, keep a small tail
MAX_GOALS = 12  # remaining goals per side; the Poisson tail beyond this is negligible at these rates


def remaining_minutes(minute: int) -> float:
    return max(MIN_REMAINING_MIN, MATCH_MINUTES - float(minute))


def inplay_probs(
    rate_home: float, rate_away: float, minute: int, score_home: int, score_away: int
) -> tuple[float, float, float]:
    """(p_home, p_draw, p_away) for the FINAL result at full time."""
    if rate_home <= 0 or rate_away <= 0:
        raise ValueError("pre-match goal rates must be positive")
    frac = remaining_minutes(minute) / MATCH_MINUTES
    k = np.arange(MAX_GOALS + 1)
    pmf_h = poisson.pmf(k, rate_home * frac)
    pmf_a = poisson.pmf(k, rate_away * frac)
    joint = np.outer(pmf_h, pmf_a)
    joint = joint / joint.sum()  # renormalise the (negligible) truncated tail
    diff_now = score_home - score_away
    final_diff = diff_now + k[:, None] - k[None, :]
    p_home = float(joint[final_diff > 0].sum())
    p_draw = float(joint[final_diff == 0].sum())
    p_away = float(joint[final_diff < 0].sum())
    return p_home, p_draw, p_away
