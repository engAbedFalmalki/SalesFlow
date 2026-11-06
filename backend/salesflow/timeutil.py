"""UTC storage / Asia/Riyadh business-day helpers."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import BUSINESS_TIMEZONE

RIYADH = ZoneInfo(BUSINESS_TIMEZONE)
UTC = timezone.utc


def riyadh_to_utc(local: datetime) -> datetime:
    """Interpret a naive datetime as Asia/Riyadh local time; return naive UTC."""
    return local.replace(tzinfo=RIYADH).astimezone(UTC).replace(tzinfo=None)


def utc_to_riyadh(utc_naive: datetime) -> datetime:
    return utc_naive.replace(tzinfo=UTC).astimezone(RIYADH)


def business_date(utc_naive: datetime) -> date:
    return utc_to_riyadh(utc_naive).date()


def date_key(d: date) -> int:
    return d.year * 10000 + d.month * 100 + d.day


def business_day_bounds_utc(d: date) -> tuple[datetime, datetime]:
    start = riyadh_to_utc(datetime.combine(d, time.min))
    return start, start + timedelta(days=1)


def iso_utc(dt: datetime | None) -> str | None:
    """Canonical ISO-8601 UTC string with millisecond precision and Z suffix."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt.isoformat(timespec="milliseconds") + "Z"


def parse_ts(value: object) -> datetime:
    """Parse an ISO-8601 timestamp. Offsets are honoured; naive values are UTC.

    Returns a naive UTC datetime truncated to milliseconds. Raises ValueError.
    """
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        s = value.strip()
        if s.endswith("Z") or s.endswith("z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
    else:
        raise ValueError(f"not a timestamp: {value!r}")
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt.replace(microsecond=(dt.microsecond // 1000) * 1000)
