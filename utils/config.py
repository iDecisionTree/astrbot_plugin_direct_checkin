"""插件配置读取辅助。

AstrBotConfig 本身是 dict，这里提供带类型兜底的读取函数，避免脏配置导致插件崩溃。
"""

from __future__ import annotations

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
    except (TypeError, ValueError):
        return default


def get_float(config: Mapping[str, Any], key: str, default: float = 0.0) -> float:
    value = config.get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
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
