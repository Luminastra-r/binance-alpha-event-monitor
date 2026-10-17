"""Timezone helpers.

All internal times are timezone-aware UTC datetimes (or ISO strings ending in Z).
Display uses the configured timezone (Asia/Shanghai by default).
Never do naive string arithmetic on times.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

DISPLAY_TZ = ZoneInfo("Asia/Shanghai")
UTC = timezone.utc

# kept in sync with Config.TIMEZONE at first use
_display_tz = DISPLAY_TZ


def configure_display_tz(name: str) -> None:
    global _display_tz
    try:
        _display_tz = ZoneInfo(name)
    except Exception:
        _display_tz = DISPLAY_TZ


def display_tz() -> ZoneInfo:
    return _display_tz


def utcnow() -> datetime:
    return datetime.now(UTC)


def to_utc(dt: datetime) -> datetime:
    """Naive datetimes are assumed to be in the display timezone (per spec)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_display_tz).astimezone(UTC)
    return dt.astimezone(UTC)


def to_display(dt: datetime) -> datetime:
    return dt.astimezone(_display_tz)


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime | None:
    """Parse an ISO-ish timestamp to an aware UTC datetime. Returns None on failure."""
    if not s:
        return None
    t = s.strip()
    try:
        t = t.replace("Z", "+00:00")
        dt = datetime.fromisoformat(t)
        return to_utc(dt)
    except Exception:
        return None


def fmt_display_md_hm(dt: datetime) -> str:
    """e.g. 10月16日 16:00"""
    return to_display(dt).strftime("%m月%d日 %H:%M")


def fmt_display_hm(dt: datetime) -> str:
    """e.g. 16:00"""
    return to_display(dt).strftime("%H:%M")


def fmt_display_full(dt: datetime) -> str:
    """e.g. 2026-10-16 16:00 (北京时间)"""
    return to_display(dt).strftime("%Y-%m-%d %H:%M")


def display_date_key(dt: datetime) -> str:
    """Calendar date in the display timezone, e.g. 2026-10-16."""
    return to_display(dt).strftime("%Y-%m-%d")


def seconds_between(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds()
