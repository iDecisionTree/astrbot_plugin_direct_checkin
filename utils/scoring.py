"""计次规则工具。

定义每周封顶的有效净次数；累计值由报表按各周目标分别计算后相加。
"""

from __future__ import annotations


def week_counted(
    auto_counted: int,
    manual_counted: int,
    manual_negatives: int,
    weekly_limit: int,
) -> int:
    """本周有效计次（自动计次 + 人工计次 - 人工扣减），范围 0~weekly_limit。"""

    raw = auto_counted + manual_counted - manual_negatives
    if raw < 0:
        raw = 0
    if raw > weekly_limit:
        raw = weekly_limit
    return raw
