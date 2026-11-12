"""Store in UTC, display in the report timezone (Africa/Cairo by default)."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

REPORT_TZ = "Africa/Cairo"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_report_tz(ts: datetime, tz: str = REPORT_TZ) -> datetime:
    """Convert an aware UTC timestamp to the report timezone. Naive input is rejected."""
    if ts.tzinfo is None:
        raise ValueError("naive datetime: timestamps must be timezone-aware UTC")
    return ts.astimezone(ZoneInfo(tz))


def excel_local(ts: datetime, tz: str = REPORT_TZ) -> datetime:
    """Excel cannot store tz-aware datetimes: return naive local time (the column header names the zone)."""
    return to_report_tz(ts, tz).replace(tzinfo=None)
