"""NAS 文件暂存 + SQLite 提交标记，支持跨进程崩溃恢复。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import uuid
from pathlib import Path


class ResetService:
    def __init__(self, db, file_service):
        self.db = db
        self.files = file_service
        self.journal = db.path.parent / "reset-journal.json"
        self.lock = asyncio.Lock()
        self.logger = logging.getLogger(__name__)

    def _base(self):
        original = self.files.nas_base_dir
        base = original.resolve()
        if (
            original.is_symlink()
            or not base.is_dir()
            or base == Path(base.anchor)
            or len(base.parts) < 3
            or base == Path.home().resolve()
            or base == self.db.path.parent.resolve()
            or base in self.db.path.resolve().parents
        ):
            raise ValueError("归档目录不符合安全重置要求")
        return base

    def _save(self, data):
        temporary = self.journal.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.journal)

    def _validate(self, data):
        base = self._base()
        operation_id = data["id"]
        if len(operation_id) != 32 or any(c not in "0123456789abcdef" for c in operation_id):
            raise ValueError("恢复日志 ID 无效")
        if data["base"] != str(base):
            raise ValueError("NAS 配置已变化，请恢复原配置后处理重置日志")
        for name in data["entries"]:
            if (
                not name
                or name in {".", ".."}
                or Path(name).name != name
                or "/" in name
                or "\\" in name
            ):
                raise ValueError("恢复日志路径无效")
        trash = base / (".checkin-reset-" + operation_id)
        if trash.is_symlink() or (trash.exists() and trash.resolve().parent != base):
            raise ValueError("暂存目录路径非法")
        return base, trash

    def _stage(self):
        base = self._base()
        entries = list(base.iterdir())
        if any(p.name.startswith(".checkin-reset-") for p in entries):
            raise ValueError("存在未登记暂存目录，请先检查残留")
        # 嵌套挂载点不能纳入全清，链接只移动链接自身。
        for root, dirs, _ in os.walk(base, followlinks=False):
            for name in dirs:
                child = Path(root) / name
                if not child.is_symlink() and (
                    os.path.ismount(child) or getattr(child, "is_junction", lambda: False)()
                ):
                    raise ValueError("归档内含挂载点或目录联接，无法安全重置")
        data = {"id": uuid.uuid4().hex, "base": str(base), "entries": [p.name for p in entries]}
        self._save(data)
        _, trash = self._validate(data)
        trash.mkdir()
        for child in entries:
            child.rename(trash / child.name)
        return data

    def _restore(self, data):
        base, trash = self._validate(data)
        for name in data["entries"]:
            source, destination = trash / name, base / name
            if source.exists() or source.is_symlink():
                if destination.exists() or destination.is_symlink():
                    raise RuntimeError("恢复目标已存在，保留暂存文件等待处理")
                source.rename(destination)
        if trash.exists():
            trash.rmdir()
        self.journal.unlink(missing_ok=True)

    def _purge(self, data):
        _, trash = self._validate(data)
        count = 0
        if trash.exists():
            for root, dirs, files in os.walk(trash, followlinks=False):
                count += len(files)
                for name in dirs:
                    child = Path(root) / name
                    if not child.is_symlink() and (
                        os.path.ismount(child) or getattr(child, "is_junction", lambda: False)()
                    ):
                        raise ValueError("暂存区含挂载点，保留数据等待处理")
            shutil.rmtree(trash)
        self.journal.unlink(missing_ok=True)
        return count

    async def recover(self):
        if not self.journal.exists():
            return None
        data = json.loads(self.journal.read_text(encoding="utf-8"))
        self._validate(data)
        committed = await self.db.fetch(
            lambda conn: (
                conn.execute(
                    "SELECT 1 FROM reset_commits WHERE operation_id=?", (data["id"],)
                ).fetchone()
                is not None
            )
        )
        if committed:
            await asyncio.to_thread(self._purge, data)
            return "已完成上次重置残留文件的清理"
        await asyncio.to_thread(self._restore, data)
        return "已恢复上次未提交重置的归档文件"

    async def execute(self):
        async with self.lock:
            if self.journal.exists():
                message = await self.recover()
                return None, message
            try:
                data = await asyncio.to_thread(self._stage)
                counts = await self.db.reset_checkin_data(data["id"])
            except Exception:
                self.logger.exception("重置暂存或数据库事务失败，尝试恢复归档")
                try:
                    await self.recover()
                except Exception:
                    self.logger.exception("重置文件恢复未完成，保留恢复日志")
                    return (
                        None,
                        "重置未完成，数据库未清空，文件恢复仍待处理。词九已进入维护状态，请检查归档目录后重试。",
                    )
                return None, "重置未完成，数据库未清空，归档文件已恢复。请检查日志与目录权限。"
            try:
                files = await asyncio.to_thread(self._purge, data)
            except Exception:
                self.logger.exception("数据库重置已提交，暂存文件清理待重试")
                return (
                    None,
                    "数据已重置，归档残留文件清理未完成。请修复目录权限后再次执行重置确认，词九会继续清理。",
                )
            return counts, files
