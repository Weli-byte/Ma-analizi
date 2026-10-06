"""Timezone conversion with explicit DST handling (ADR 0005).

Source kickoff times are local wall-clock times in a known timezone. Canonical data is UTC-aware.
Wall-clock times that do not exist (spring-forward gap) or occur twice (autumn fold) are REJECTED,
never guessed.
"""

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

CONVERSION_VERSION = "tzconv-1"


class InvalidLocalTime(ValueError):
    """Wall-clock time that is nonexistent or ambiguous in the given timezone."""


def local_to_utc(day: date, clock: time, tz_name: str) -> datetime:
    tz = ZoneInfo(tz_name)
    naive = datetime.combine(day, clock)
    first = naive.replace(tzinfo=tz, fold=0)
    second = naive.replace(tzinfo=tz, fold=1)
    utc_first = first.astimezone(UTC)
    if utc_first.astimezone(tz).replace(tzinfo=None) != naive:
        raise InvalidLocalTime(f"{naive} does not exist in {tz_name} (DST gap)")
    if utc_first != second.astimezone(UTC):
        raise InvalidLocalTime(f"{naive} is ambiguous in {tz_name} (DST fold)")
    return utc_first


def date_only_to_utc(day: date) -> datetime:
    """Unknown kickoff time: 00:00 UTC of the match day. Earlier than any real kickoff, so a
    cutoff derived from it is leakage-conservative. Callers must flag kickoff_time_known=False."""
    return datetime.combine(day, time(0), tzinfo=UTC)
