"""北京时间 / UTC 时间与考核周计算工具。

约定：
- 数据库中统一存储 UTC ISO 字符串。
- 周统计固定使用 Asia/Shanghai，周一 00:00:00 ~ 周日 23:59:59。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "Asia/Shanghai"

#: 当运行环境缺少 IANA 时区数据（例如未安装 tzdata 的 Windows）时的兜底时区。
_FALLBACK_TZ = timezone(timedelta(hours=8), "UTC+08:00")


def get_tz(name: str | None = None) -> timezone:
    """获取时区对象，失败时回退到默认时区或固定 +08:00。"""

    try:
        return ZoneInfo(name or DEFAULT_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        pass
    try:
        return ZoneInfo(DEFAULT_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return _FALLBACK_TZ


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def to_utc(dt: datetime) -> datetime:
    """将 datetime 归一到 UTC。naive 值按 UTC 处理。"""

    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def utc_iso(dt: datetime) -> str:
    return to_utc(dt).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return to_utc(parsed)


def to_beijing(dt: datetime, tz_name: str | None = None) -> datetime:
    return to_utc(dt).astimezone(get_tz(tz_name))


def beijing_now(tz_name: str | None = None) -> datetime:
    return datetime.now(get_tz(tz_name))


def week_key_of(dt: datetime, tz_name: str | None = None) -> str:
    """返回 dt 所在北京时间自然周的周一日期，格式 YYYY-MM-DD。"""

    local = to_beijing(dt, tz_name)
    monday = local.date() - timedelta(days=local.weekday())
    return monday.isoformat()


def week_key_now(tz_name: str | None = None) -> str:
    return week_key_of(beijing_now(tz_name), tz_name)


def parse_week_key(week_key: str) -> date:
    return date.fromisoformat(week_key)


def week_start_beijing(week_key: str, tz_name: str | None = None) -> datetime:
    """给定周键，返回该周周一 00:00:00（北京时间）。"""

    monday = parse_week_key(week_key)
    return datetime(monday.year, monday.month, monday.day, tzinfo=get_tz(tz_name))


def week_end_beijing(week_key: str, tz_name: str | None = None) -> datetime:
    """给定周键，返回该周结束时刻（下周一 00:00:00 北京时间，右开区间）。"""

    return week_start_beijing(week_key, tz_name) + timedelta(days=7)


def day_end_beijing(dt: datetime, tz_name: str | None = None) -> datetime:
    """返回 dt 所在北京日期的 23:59:59.999999。"""

    local = to_beijing(dt, tz_name)
    return datetime(
        local.year,
        local.month,
        local.day,
        23,
        59,
        59,
        999999,
        tzinfo=get_tz(tz_name),
    )


def format_beijing(
    dt: datetime | None, fmt: str = "%Y-%m-%d %H:%M:%S", tz_name: str | None = None
) -> str:
    if dt is None:
        return ""
    return to_beijing(dt, tz_name).strftime(fmt)
