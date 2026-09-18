"""文本规范化、指纹与相似度工具。"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import Counter

_ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff]")
_WHITESPACE = re.compile(r"\s+")

DEFAULT_KEYWORDS = (
    "实现",
    "测试",
    "问题",
    "总结",
    "下一步",
    "代码",
    "结果",
    "实验",
    "调试",
    "优化",
    "修复",
    "功能",
    "实践",
    "收获",
)


def normalize_text(text: str) -> str:
    """规范化文本：Unicode 归一、去零宽字符、统一空白（用于指纹与相似度）。"""

    if not text:
        return ""
    value = unicodedata.normalize("NFKC", text)
    value = _ZERO_WIDTH.sub("", value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = _WHITESPACE.sub(" ", value)
    return value.strip().casefold()


def normalize_text_lines(text: str) -> str:
    """规范化文本但保留换行，便于按段落挑选关键内容。"""

    if not text:
        return ""
    value = unicodedata.normalize("NFKC", text)
    value = _ZERO_WIDTH.sub("", value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r"\n\s*\n+", "\n", value)
    return value.strip()


def text_sha256(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def truncate(text: str, max_length: int) -> str:
    if max_length <= 0 or len(text) <= max_length:
        return text
    return text[:max_length]


def char_ngrams(text: str, n: int = 3) -> Counter[str]:
    if not text:
        return Counter()
    if len(text) < n:
        return Counter({text: 1})
    return Counter(text[i : i + n] for i in range(len(text) - n + 1))


def cosine_similarity(left: Counter[str], right: Counter[str]) -> float:
    if not left or not right:
        return 0.0
    common = set(left) & set(right)
    if not common:
        return 0.0
    dot = sum(left[gram] * right[gram] for gram in common)
    norm_left = math.sqrt(sum(value * value for value in left.values()))
    norm_right = math.sqrt(sum(value * value for value in right.values()))
    if norm_left == 0 or norm_right == 0:
        return 0.0
    return dot / (norm_left * norm_right)


def similarity(left_text: str, right_text: str, n: int = 3, max_chars: int = 20000) -> float:
    """计算两段文本的字符 n-gram cosine 相似度，范围 0~1。"""

    left = truncate(normalize_text(left_text), max_chars)
    right = truncate(normalize_text(right_text), max_chars)
    if not left or not right:
        return 0.0
    return cosine_similarity(char_ngrams(left, n), char_ngrams(right, n))


def select_relevant_text(
    text: str,
    max_chars: int,
    keywords: tuple[str, ...] = DEFAULT_KEYWORDS,
) -> str:
    """在超长时优先保留标题/关键词段落，并做确定性截断。"""

    normalized = normalize_text_lines(text)
    if max_chars <= 0 or len(normalized) <= max_chars:
        return normalized

    lines = [line for line in normalized.split("\n") if line.strip()]
    head = normalized[: max_chars // 3]
    tail = normalized[-(max_chars // 6) :]
    picked: list[str] = []
    budget = max_chars - len(head) - len(tail)
    for line in lines:
        if len(line) > 500:
            line = line[:500]
        if any(keyword in line for keyword in keywords):
            if budget - len(line) <= 0:
                break
            picked.append(line)
            budget -= len(line)
    segments = [head]
    if picked:
        segments.append("【关键段落】")
        segments.extend(picked)
    segments.append("【结尾】")
    segments.append(tail)
    return truncate("\n".join(segments), max_chars)
