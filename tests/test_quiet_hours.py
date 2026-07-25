from datetime import UTC, datetime

from app.quiet_hours import is_within_quiet_hours


def test_within_a_same_day_window():
    now = datetime(2026, 7, 25, 13, 0, tzinfo=UTC)  # 13:00 UTC
    assert is_within_quiet_hours(now, "12:00", "14:00", "UTC") is True


def test_outside_a_same_day_window():
    now = datetime(2026, 7, 25, 15, 0, tzinfo=UTC)
    assert is_within_quiet_hours(now, "12:00", "14:00", "UTC") is False


def test_within_a_midnight_spanning_window_late_at_night():
    now = datetime(2026, 7, 25, 23, 30, tzinfo=UTC)
    assert is_within_quiet_hours(now, "22:00", "07:00", "UTC") is True


def test_within_a_midnight_spanning_window_early_morning():
    now = datetime(2026, 7, 26, 5, 0, tzinfo=UTC)
    assert is_within_quiet_hours(now, "22:00", "07:00", "UTC") is True


def test_outside_a_midnight_spanning_window():
    now = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)
    assert is_within_quiet_hours(now, "22:00", "07:00", "UTC") is False


def test_start_equal_to_end_is_treated_as_no_quiet_hours():
    now = datetime(2026, 7, 25, 22, 0, tzinfo=UTC)
    assert is_within_quiet_hours(now, "22:00", "22:00", "UTC") is False


def test_invalid_timezone_falls_back_to_utc():
    now = datetime(2026, 7, 25, 23, 0, tzinfo=UTC)
    assert is_within_quiet_hours(now, "22:00", "07:00", "Not/A_Real_Zone") is True


def test_invalid_time_strings_are_treated_as_no_quiet_hours():
    now = datetime(2026, 7, 25, 23, 0, tzinfo=UTC)
    assert is_within_quiet_hours(now, "not-a-time", "07:00", "UTC") is False
    assert is_within_quiet_hours(now, "22:00", "", "UTC") is False


def test_naive_utc_datetime_is_treated_as_utc_not_system_local_time():
    now_naive = datetime(2026, 7, 25, 13, 0)  # no tzinfo, mirrors datetime.utcnow()
    assert is_within_quiet_hours(now_naive, "12:00", "14:00", "UTC") is True


def test_timezone_conversion_across_a_date_boundary():
    # 23:30 UTC on the 25th is 01:30 CEST (UTC+2) on the 26th - inside a
    # 22:00-07:00 window in the user's local timezone, not the UTC calendar day.
    now = datetime(2026, 7, 25, 23, 30, tzinfo=UTC)
    assert is_within_quiet_hours(now, "22:00", "07:00", "Europe/Berlin") is True
    assert is_within_quiet_hours(now, "02:00", "06:00", "Europe/Berlin") is False
