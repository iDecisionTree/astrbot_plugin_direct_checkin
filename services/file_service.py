"""打卡文件获取、校验与 NAS 归档。"""

from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..models.entities import User
from ..models.enums import ErrorCode
from ..utils.path_utils import ensure_dir, safe_join, sanitize_filename, sha256_file
from . import docx_parser

_MAX_UNCOMPRESSED_FACTOR = 50
_MAX_UNCOMPRESSED_CAP = 250 * 1024 * 1024


class FileServiceError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(slots=True)
class StoredFile:
    path: Path
    size: int
    sha256: str
    original_filename: str
    quoted_message_id: str
    source_path: Path


@dataclass(slots=True)
class RepliedFile:
    component: Any
    name: str
    url: str
    quoted_message_id: str


def locate_replied_file(message_components: Iterable[Any]) -> RepliedFile | None:
    """从消息链中定位被引用消息里的文件段。

    只在引用消息内查找，不会把当前消息自带的文件当成打卡文件。
    """

    from astrbot.api.message_components import File, Reply

    for component in message_components or []:
        if not isinstance(component, Reply):
            continue
        for inner in component.chain or []:
            if isinstance(inner, File):
                return RepliedFile(
                    component=inner,
                    name=str(inner.name or ""),
                    url=str(inner.url or ""),
                    quoted_message_id=str(component.id or ""),
                )
    return None


class FileService:
    def __init__(self, nas_base_dir: str, max_file_size_mb: int, logger: Any) -> None:
        self.nas_base_dir = Path(nas_base_dir)
        self.max_file_size_bytes = max(1, max_file_size_mb) * 1024 * 1024
        self.max_uncompressed_bytes = min(
            self.max_file_size_bytes * _MAX_UNCOMPRESSED_FACTOR,
            _MAX_UNCOMPRESSED_CAP,
        )
        self.logger = logger

    def check_ready(self) -> bool:
        """严格模式：归档根目录必须已存在且可写。"""

        base = self.nas_base_dir
        if not base.exists() or not base.is_dir():
            return False
        return os.access(base, os.W_OK)

    async def prepare(
        self,
        replied: RepliedFile,
        user: User,
        submission_id: str,
        now_beijing_label: str,
    ) -> StoredFile:
        original_filename = self._resolve_filename(replied)
        if original_filename.lower().endswith(".doc"):
            raise FileServiceError(ErrorCode.UNSUPPORTED_WORD_FORMAT, "旧版 .doc 不支持")
        if not original_filename.lower().endswith(".docx"):
            raise FileServiceError(ErrorCode.UNSUPPORTED_WORD_FORMAT, "只支持 .docx")

        if not self.check_ready():
            raise FileServiceError(ErrorCode.NAS_WRITE_FAILED, "归档目录不可写")

        try:
            source_path = await replied.component.get_file()
        except Exception as exc:  # noqa: BLE001 - 适配器异常需统一转为友好错误
            self.logger.warning("下载引用文件失败: %s", exc)
            raise FileServiceError(ErrorCode.FILE_DOWNLOAD_FAILED, "文件下载失败") from exc

        if not source_path or not Path(source_path).is_file():
            raise FileServiceError(ErrorCode.FILE_DOWNLOAD_FAILED, "文件下载失败")

        source = Path(source_path)
        size = source.stat().st_size
        if size <= 0:
            raise FileServiceError(ErrorCode.FILE_DOWNLOAD_FAILED, "文件为空")
        if size > self.max_file_size_bytes:
            raise FileServiceError(ErrorCode.FILE_TOO_LARGE, "文件超过大小上限")

        try:
            await asyncio.to_thread(docx_parser.validate_docx, source, self.max_uncompressed_bytes)
        except docx_parser.DocxValidationError as exc:
            raise FileServiceError(exc.code, exc.message) from exc

        sha = await asyncio.to_thread(sha256_file, source)

        stored_path = await self._store(
            source, user.student_id, submission_id, original_filename, now_beijing_label
        )
        return StoredFile(
            path=stored_path,
            size=size,
            sha256=sha,
            original_filename=original_filename,
            quoted_message_id=replied.quoted_message_id,
            source_path=source,
        )

    async def _store(
        self,
        source: Path,
        student_id: str,
        submission_id: str,
        original_filename: str,
        now_label: str,
    ) -> Path:
        try:
            target_dir = safe_join(self.nas_base_dir, student_id)
            await asyncio.to_thread(ensure_dir, target_dir)
            safe_name = sanitize_filename(original_filename)
            stem = Path(safe_name).stem
            suffix = Path(safe_name).suffix or ".docx"
            filename = f"{now_label}_{submission_id[:8]}_{stem}{suffix}"
            target = safe_join(target_dir, filename)
            await asyncio.to_thread(shutil.copy2, source, target)
        except ValueError as exc:
            raise FileServiceError(ErrorCode.NAS_WRITE_FAILED, "目标路径非法") from exc
        except OSError as exc:
            self.logger.error("写入 NAS 失败: %s", exc)
            raise FileServiceError(ErrorCode.NAS_WRITE_FAILED, "写入归档目录失败") from exc
        return target

    @staticmethod
    def _resolve_filename(replied: RepliedFile) -> str:
        name = (replied.name or "").strip()
        if not name and replied.url:
            name = Path(replied.url.split("?")[0]).name
        if not name:
            name = "document.docx"
        return sanitize_filename(name)

    async def purge_all(self) -> dict[str, int]:
        """删除归档根目录下的全部文件与空子目录，保留根目录本身。

        全程不跟随符号链接，确保不会越出 ``nas_base_dir``。
        返回 {"files", "dirs", "errors"} 计数。
        """

        base = self.nas_base_dir
        base_resolved = base.resolve()

        def work() -> dict[str, int]:
            stats = {"files": 0, "dirs": 0, "errors": 0}
            if not base.is_dir():
                return stats
            # topdown=False：先删文件，再自底向上删空目录。
            for root, dirs, files in os.walk(base, topdown=False, followlinks=False):
                root_path = Path(root)
                for name in files:
                    target = root_path / name
                    try:
                        target.unlink()
                        stats["files"] += 1
                    except OSError:
                        stats["errors"] += 1
                for name in dirs:
                    target = root_path / name
                    # 符号链接目录：仅删除链接本身，绝不进入或删除其目标。
                    if target.is_symlink():
                        try:
                            target.unlink()
                            stats["dirs"] += 1
                        except OSError:
                            stats["errors"] += 1
                        continue
                    try:
                        if target.resolve() != base_resolved:
                            target.rmdir()
                            stats["dirs"] += 1
                    except OSError:
                        # 目录非空（例如删除失败残留）时保留。
                        pass
            return stats

        return await asyncio.to_thread(work)

    async def cleanup_temp(self, stored: StoredFile) -> None:
        """清理适配器下载产生的临时文件（仅限 AstrBot 临时目录内）。"""

        source = Path(stored.source_path)
        if not source.exists():
            return
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

            temp_root = Path(get_astrbot_temp_path()).resolve()
            resolved = source.resolve()
            if temp_root in resolved.parents:
                await asyncio.to_thread(resolved.unlink, True)
        except Exception:  # noqa: BLE001 - 清理失败不影响主流程
            pass
