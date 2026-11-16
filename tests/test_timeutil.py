import re
from datetime import datetime, timedelta, timezone

import pytest

from backend.timeutil import utc_now_iso


def test_aware_utc_drops_microseconds():
    dt = datetime(2026, 11, 16, 12, 0, 0, 987654, tzinfo=timezone.utc)
    assert utc_now_iso(dt) == "2026-11-16T12:00:00Z"


def test_non_utc_is_converted():
    dt = datetime(2026, 11, 16, 17, 30, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    assert utc_now_iso(dt) == "2026-11-16T12:00:00Z"


def test_naive_raises():
    with pytest.raises(ValueError):
        utc_now_iso(datetime(2026, 11, 16, 12, 0, 0))


def test_default_format():
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", utc_now_iso())
