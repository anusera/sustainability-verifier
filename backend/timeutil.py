"""Shared UTC timestamp helper used by audit.py and verdict.py."""
from datetime import datetime, timezone


def utc_now_iso(now: datetime | None = None) -> str:
    """Return a UTC ISO 8601 timestamp with seconds precision and a Z suffix.

    now=None uses the current time. A timezone-aware value is converted to UTC.
    A naive datetime raises ValueError, because its timezone is unknown.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("utc_now_iso needs a timezone-aware datetime")
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
