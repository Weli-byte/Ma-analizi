from datetime import UTC, date, datetime, time

import pytest

from src.data.timezones import CONVERSION_VERSION, InvalidLocalTime, date_only_to_utc, local_to_utc

LONDON = "Europe/London"
MADRID = "Europe/Madrid"


@pytest.mark.parametrize(
    "day,clock,tz,expected",
    [
        # UK winter (GMT = UTC)
        (date(2024, 1, 13), time(15, 0), LONDON, datetime(2024, 1, 13, 15, 0, tzinfo=UTC)),
        # UK summer (BST = UTC+1): Burnley v Man City, 2023-08-11 20:00 local = 19:00 UTC
        (date(2023, 8, 11), time(20, 0), LONDON, datetime(2023, 8, 11, 19, 0, tzinfo=UTC)),
        # Spain summer (CEST = UTC+2): Athletic v Barcelona, 2019-08-16 21:00 local = 19:00 UTC
        (date(2019, 8, 16), time(21, 0), MADRID, datetime(2019, 8, 16, 19, 0, tzinfo=UTC)),
        # Spain winter (CET = UTC+1)
        (date(2024, 1, 14), time(21, 0), MADRID, datetime(2024, 1, 14, 20, 0, tzinfo=UTC)),
        # DST transition days (times that exist)
        (date(2024, 3, 31), time(15, 0), LONDON, datetime(2024, 3, 31, 14, 0, tzinfo=UTC)),  # BST began
        (date(2024, 3, 30), time(15, 0), LONDON, datetime(2024, 3, 30, 15, 0, tzinfo=UTC)),  # day before
        (date(2024, 10, 27), time(15, 0), LONDON, datetime(2024, 10, 27, 15, 0, tzinfo=UTC)),  # GMT again
        (date(2024, 10, 26), time(15, 0), LONDON, datetime(2024, 10, 26, 14, 0, tzinfo=UTC)),  # last BST day
        # midnight and year boundaries
        (date(2024, 3, 30), time(23, 59), LONDON, datetime(2024, 3, 30, 23, 59, tzinfo=UTC)),
        (date(2024, 7, 1), time(0, 0), LONDON, datetime(2024, 6, 30, 23, 0, tzinfo=UTC)),
        (date(2023, 12, 31), time(23, 30), MADRID, datetime(2023, 12, 31, 22, 30, tzinfo=UTC)),
        (date(2024, 1, 1), time(0, 30), MADRID, datetime(2023, 12, 31, 23, 30, tzinfo=UTC)),  # crosses year
    ],
)
def test_known_conversions(day, clock, tz, expected):
    out = local_to_utc(day, clock, tz)
    assert out == expected and out.utcoffset().total_seconds() == 0


def test_nonexistent_local_time_is_rejected():
    with pytest.raises(InvalidLocalTime, match="does not exist"):
        local_to_utc(date(2024, 3, 31), time(1, 30), LONDON)  # spring-forward gap


def test_ambiguous_local_time_is_rejected():
    with pytest.raises(InvalidLocalTime, match="ambiguous"):
        local_to_utc(date(2024, 10, 27), time(1, 30), LONDON)  # autumn fold


def test_unknown_time_is_midnight_utc_never_later_than_real_kickoff():
    d = date_only_to_utc(date(2024, 5, 1))
    assert d == datetime(2024, 5, 1, 0, 0, tzinfo=UTC)
    assert d <= local_to_utc(date(2024, 5, 1), time(12, 30), LONDON)


def test_conversion_version_is_declared():
    assert CONVERSION_VERSION.startswith("tzconv-")
