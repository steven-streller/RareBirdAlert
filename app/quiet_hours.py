from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _parse_hhmm(value: str) -> tuple[int, int] | None:
    try:
        hours, minutes = value.strip().split(":", 1)
        hours, minutes = int(hours), int(minutes)
    except (ValueError, AttributeError):
        return None
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        return None
    return hours, minutes


def is_within_quiet_hours(now_utc: datetime, start: str, end: str, tz_name: str) -> bool:
    """True if `now_utc` falls inside the user's configured quiet-hours
    window. Falls back to UTC for an unrecognized timezone name rather than
    raising - a typo in a free-text settings field shouldn't ever crash the
    notification pipeline, just silently use UTC instead of the intended
    timezone until corrected.

    `now_utc` is treated as UTC even if naive (no tzinfo) - the rest of this
    codebase uses naive `datetime.utcnow()` throughout, and a naive
    datetime's `.astimezone()` would otherwise silently assume the *system*
    local timezone instead of UTC.
    """
    start_hm = _parse_hhmm(start)
    end_hm = _parse_hhmm(end)
    if start_hm is None or end_hm is None or start_hm == end_hm:
        return False

    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo("UTC")

    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=UTC)
    local_now = now_utc.astimezone(tz)
    now_minutes = local_now.hour * 60 + local_now.minute
    start_minutes = start_hm[0] * 60 + start_hm[1]
    end_minutes = end_hm[0] * 60 + end_hm[1]

    if start_minutes < end_minutes:
        return start_minutes <= now_minutes < end_minutes
    # Window spans midnight (e.g. 22:00 -> 07:00).
    return now_minutes >= start_minutes or now_minutes < end_minutes
