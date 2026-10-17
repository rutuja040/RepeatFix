"""Time helpers.

Project convention: all timestamps are stored in SQLite as **naive UTC** datetimes.
SQLite stores datetimes as text without timezone information, so a single consistent
convention avoids ambiguous comparisons in date-range filters.

The API serializes these as ISO-8601 strings with an explicit ``+00:00`` offset so
consumers (including the frontend) can parse them unambiguously.
"""

from datetime import datetime, timezone
from typing import Optional


def utcnow() -> datetime:
    """Current UTC time as a naive datetime."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_naive_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Normalize an aware datetime to naive UTC; treat naive input as UTC already."""
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def serialize_utc(value: Optional[datetime]) -> Optional[str]:
    """Serialize a stored naive-UTC datetime with an explicit UTC offset."""
    naive = to_naive_utc(value)
    if naive is None:
        return None
    return naive.isoformat() + "+00:00"
