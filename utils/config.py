"""插件配置读取辅助。

AstrBotConfig 本身是 dict，这里提供带类型兜底的读取函数，避免脏配置导致插件崩溃。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

_TRUE_VALUES = {"1", "true", "yes", "on", "y", "t"}
_FALSE_VALUES = {"0", "false", "no", "off", "n", "f"}


def get_str(config: Mapping[str, Any], key: str, default: str = "") -> str:
    value = config.get(key, default)
    if value is None:
        return default
    return str(value)


def get_int(config: Mapping[str, Any], key: str, default: int = 0) -> int:
    value = config.get(key, default)
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def get_float(config: Mapping[str, Any], key: str, default: float = 0.0) -> float:
    value = config.get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return default


def get_bool(config: Mapping[str, Any], key: str, default: bool = False) -> bool:
    value = config.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE_VALUES:
            return True
        if text in _FALSE_VALUES:
            return False
    return default


def validate(config, logger):
    """无效的数值回退到明确默认值，不把 NaN 或无限预算传入服务。"""
    result = dict(config)
    bounds = {
        "weekly_limit": (2, 1, 100),
        "max_file_size_mb": (20, 1, 100),
        "name_max_length": (30, 1, 100),
        "reason_max_length": (100, 1, 1000),
        "history_compare_weeks": (8, 1, 520),
        "similarity_max_chars": (20000, 1000, 100000),
        "ai_timeout_seconds": (60, 5, 300),
        "ai_retry_count": (1, 0, 3),
        "ai_max_input_chars": (24000, 1000, 100000),
        "export_keep_minutes": (10, 1, 1440),
    }
    for key, (default, low, high) in bounds.items():
        value = get_int(config, key, default)
        raw = config.get(key, default)
        valid_type = (
            isinstance(raw, int)
            and not isinstance(raw, bool)
            or isinstance(raw, str)
            and raw.strip().isdecimal()
            or isinstance(raw, float)
            and math.isfinite(raw)
            and raw.is_integer()
        )
        if not valid_type or not low <= value <= high:
            logger.warning("配置 %s 无效，使用默认值 %s", key, default)
            value = default
        result[key] = value
    warn = get_float(config, "similarity_warn_threshold", 0.85)
    high = get_float(config, "similarity_high_threshold", 0.97)
    if not (math.isfinite(warn) and math.isfinite(high) and 0 <= warn <= high <= 1):
        logger.warning("相似度阈值无效，恢复为 0.85 / 0.97")
        warn, high = 0.85, 0.97
    result.update(similarity_warn_threshold=warn, similarity_high_threshold=high)
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        ZoneInfo(get_str(config, "timezone", "Asia/Shanghai"))
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("时区配置无效，使用 Asia/Shanghai")
        result["timezone"] = "Asia/Shanghai"
    return result
