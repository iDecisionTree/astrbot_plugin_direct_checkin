"""计次规则工具。

统一定义"每周有效计次"与"累计有效计分次数"口径，供打卡、人工调整、导出、统计共用。
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


def cumulative_valid(passed_submissions: int, manual_net: int) -> int:
    """累计有效计分次数 = 所有通过的提交 + 全部人工调整净和，最小为 0。"""

    total = passed_submissions + manual_net
    return total if total > 0 else 0
