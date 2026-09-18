"""路径与文件哈希工具。"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WHITESPACE = re.compile(r"\s+")
_MULTI_DOT = re.compile(r"\.{2,}")


def sanitize_filename(name: str, max_length: int = 100) -> str:
    """把用户提供的文件名转换为安全的单段文件名。

    - 只取最后一段，消除路径穿越；
    - 去除控制字符与平台非法字符；
    - 保留原始扩展名；
    - 结果为空时回退为 ``file``。
    """

    raw = (name or "").strip().replace("\\", "/")
    raw = raw.split("/")[-1]
    raw = _UNSAFE_CHARS.sub("_", raw)
    raw = _WHITESPACE.sub("_", raw)
    raw = _MULTI_DOT.sub(".", raw)
    raw = raw.strip(" ._")
    if not raw:
        return "file"
    if len(raw) > max_length:
        path = Path(raw)
        suffix = path.suffix
        stem = path.stem
        keep = max(1, max_length - len(suffix))
        raw = f"{stem[:keep]}{suffix}"
    return raw


def safe_join(base: Path, *parts: str) -> Path:
    """在 base 下安全拼接路径，若越界则抛出 ValueError。"""

    base_resolved = base.resolve()
    candidate = base_resolved
    for part in parts:
        candidate = candidate / part
    candidate = candidate.resolve()
    if candidate != base_resolved and base_resolved not in candidate.parents:
        raise ValueError(f"path escapes base directory: {candidate}")
    return candidate


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
