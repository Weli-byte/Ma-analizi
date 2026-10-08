"""When to spend a The Odds API credit (ADR 0030 amendment).

The free plan gives 500 credits per month and one odds request for one league costs 2, so quotes are
collected only around the moments that matter: the S13 stage times and the last minutes before
kickoff (the closing reference for CLV). One request returns every event of a league, so one call
serves all matches of that league whose window is open.
"""

from datetime import datetime, timedelta

# minutes BEFORE kickoff: (window opens, window closes). Wider than the S13 stage tolerances because the
# cloud scheduler is best-effort (cron can run late); a quote near a stage is all that is needed.
WINDOWS_MIN: tuple[tuple[int, int], ...] = ((1440, 1380), (95, 65), (35, 10), (12, 0))


def due_window(kickoff_utc: datetime, now: datetime) -> int | None:
    """Index of the collection window `now` falls in for this kickoff (None = not due)."""
    if now >= kickoff_utc:
        return None
    for i, (start, end) in enumerate(WINDOWS_MIN):
        if kickoff_utc - timedelta(minutes=start) <= now < kickoff_utc - timedelta(minutes=end):
            return i
    return None
