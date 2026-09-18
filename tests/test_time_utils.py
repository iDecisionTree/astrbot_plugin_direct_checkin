from datetime import datetime

from astrbot_plugin_direct_checkin.utils.time_utils import (
    day_end_beijing,
    get_tz,
    parse_iso,
    to_beijing,
    utc_iso,
    week_end_beijing,
    week_key_of,
    week_start_beijing,
)


def test_week_boundary_between_sunday_and_monday():
    tz = get_tz("Asia/Shanghai")
    monday = datetime(2024, 1, 1, 0, 0, 0, tzinfo=tz)
    sunday = datetime(2023, 12, 31, 23, 59, 59, tzinfo=tz)
    assert week_key_of(monday) == "2024-01-01"
    assert week_key_of(sunday) == "2023-12-25"


def test_week_key_isolated_from_server_timezone():
    # 2024-01-01 00:30 北京时间 == 2023-12-31 16:30 UTC
    utc_moment = datetime.fromisoformat("2023-12-31T16:30:00+00:00")
    assert week_key_of(utc_moment) == "2024-01-01"
    # 2023-12-31 23:30 北京时间仍在上一周
    utc_moment2 = datetime.fromisoformat("2023-12-31T15:30:00+00:00")
    assert week_key_of(utc_moment2) == "2023-12-25"


def test_week_start_and_end():
    start = week_start_beijing("2024-01-01")
    end = week_end_beijing("2024-01-01")
    assert start.isoformat().startswith("2024-01-01T00:00:00")
    assert end.isoformat().startswith("2024-01-08T00:00:00")
    assert (end - start).days == 7


def test_day_end_beijing():
    moment = datetime.fromisoformat("2024-01-01T10:00:00+00:00")
    end = day_end_beijing(moment)
    local = to_beijing(moment)
    assert end.year == local.year and end.month == local.month and end.day == local.day
    assert end.hour == 23 and end.minute == 59 and end.second == 59


def test_iso_round_trip():
    moment = datetime.fromisoformat("2024-01-01T10:00:00+00:00")
    assert parse_iso(utc_iso(moment)) == moment
    assert parse_iso(None) is None
    assert parse_iso("not-a-date") is None
