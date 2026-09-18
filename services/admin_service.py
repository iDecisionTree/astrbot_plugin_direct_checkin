"""管理员命令服务：管理员维护、人工计次、暂停与恢复。"""

from __future__ import annotations

import re
import time

from ..models.entities import User
from ..models.enums import PauseScope
from ..repositories.admin_repo import AdminRepository
from ..repositories.audit_repo import AuditRepository
from ..repositories.db import Database
from ..repositories.pause_repo import PauseRepository
from ..repositories.submission_repo import SubmissionRepository
from ..repositories.user_repo import UserRepository
from ..utils import response_templates as T
from ..utils.time_utils import (
    day_end_beijing,
    now_utc,
    utc_iso,
    week_end_beijing,
    week_key_now,
)
from .file_service import FileService

_DIGITS = re.compile(r"^\d+$")

#: 重置二次确认的有效期（秒）。
_RESET_CONFIRM_TTL = 120


class AdminService:
    def __init__(
        self,
        *,
        admin_repo: AdminRepository,
        user_repo: UserRepository,
        submission_repo: SubmissionRepository,
        pause_repo: PauseRepository,
        audit_repo: AuditRepository,
        db: Database,
        file_service: FileService,
        weekly_limit: int,
        timezone: str,
        super_admin_qq: str = "",
    ) -> None:
        self.admin_repo = admin_repo
        self.user_repo = user_repo
        self.submission_repo = submission_repo
        self.pause_repo = pause_repo
        self.audit_repo = audit_repo
        self.db = db
        self.file_service = file_service
        self.weekly_limit = max(1, weekly_limit)
        self.timezone = timezone
        self.super_admin_qq = (super_admin_qq or "").strip()
        self._reset_pending: dict[str, float] = {}

    async def is_admin(self, qq: str) -> bool:
        return await self.admin_repo.is_admin(qq)

    async def add_admin(self, actor_qq: str, target_qq: str, message_id: str) -> str:
        target_qq = (target_qq or "").strip()
        if not _DIGITS.match(target_qq):
            return T.admin_op_failed("QQ 号必须是纯数字")
        now = utc_iso(now_utc())
        inserted = await self.admin_repo.add(target_qq, actor_qq, now)
        await self._audit(
            actor_qq, "admin_add", target_qq, message_id, "ok" if inserted else "exists", now
        )
        return T.admin_added(target_qq) if inserted else T.admin_already(target_qq)

    async def remove_admin(self, actor_qq: str, target_qq: str, message_id: str) -> str:
        target_qq = (target_qq or "").strip()
        if not _DIGITS.match(target_qq):
            return T.admin_op_failed("QQ 号必须是纯数字")
        if not await self.admin_repo.is_admin(target_qq):
            return T.admin_not_found_admin(target_qq)
        if self.super_admin_qq and target_qq == self.super_admin_qq:
            await self._audit(
                actor_qq,
                "admin_remove",
                target_qq,
                message_id,
                "super_protected",
                utc_iso(now_utc()),
            )
            return T.admin_super_protected()
        if await self.admin_repo.count() <= 1:
            await self._audit(
                actor_qq, "admin_remove", target_qq, message_id, "last_one", utc_iso(now_utc())
            )
            return T.admin_last_one()
        now = utc_iso(now_utc())
        await self.admin_repo.remove(target_qq)
        await self._audit(actor_qq, "admin_remove", target_qq, message_id, "ok", now)
        return T.admin_removed(target_qq)

    async def adjust(self, actor_qq: str, target_value: str, delta: int, message_id: str) -> str:
        target_value = (target_value or "").strip()
        if not target_value:
            return T.admin_op_failed("请提供学号或 QQ")
        user, conflict = await self.user_repo.resolve_identifier(target_value)
        if conflict:
            return T.admin_op_failed("这个数字同时匹配到不同用户，请改用学号")
        if user is None:
            return T.admin_op_failed("没有找到对应用户")

        week_key = week_key_now(self.timezone)
        now = utc_iso(now_utc())
        if delta < 0:
            current = await self._effective_count(user, week_key)
            if current <= 0:
                return T.admin_op_failed("当前周计次已经是 0，不能再减少")

        await self.admin_repo.add_adjustment(user.id, week_key, delta, actor_qq, now)
        effective = await self._effective_count(user, week_key)
        display = min(max(0, effective), self.weekly_limit)
        await self._audit(
            actor_qq,
            "count_adjust",
            str(user.id),
            message_id,
            f"delta={delta}",
            now,
            target_type="user",
        )
        return T.admin_op_ok(T.target_display(user.name, user.student_id), display)

    async def skip(self, actor_qq: str, scope: str, reason: str, message_id: str) -> str:
        aliases = {
            "d": PauseScope.DAY.value,
            "day": PauseScope.DAY.value,
            "w": PauseScope.WEEK.value,
            "week": PauseScope.WEEK.value,
        }
        scope = aliases.get((scope or "").strip().lower(), "")
        if not scope:
            return T.admin_op_failed("暂停范围只能是 d 或 w")
        now_dt = now_utc()
        now = utc_iso(now_dt)
        if scope == PauseScope.DAY.value:
            end = day_end_beijing(now_dt, self.timezone)
            exemption = None
        else:
            week_key = week_key_now(self.timezone)
            end = week_end_beijing(week_key, self.timezone)
            exemption = week_key
        await self.pause_repo.create(
            scope=scope,
            start_at=now,
            scheduled_end_at=utc_iso(end),
            reason=reason,
            created_by_qq=actor_qq,
            now=now,
            exemption_week_key=exemption,
        )
        await self._audit(actor_qq, "pause", scope, message_id, "ok", now)
        return T.skip_ok(scope, reason)

    async def reset(self, actor_qq: str, confirm: bool, message_id: str) -> str:
        """重置所有打卡数据（用户绑定、提交、人工调整、暂停、审计）。

        需要连续两次 /d reset confirm 才会执行；会同时删除 NAS 归档文件，
        但保留管理员表，避免插件失管。
        """

        now_monotonic = time.monotonic()
        pending_at = self._reset_pending.get(actor_qq)
        if not confirm:
            self._reset_pending.pop(actor_qq, None)
            return T.reset_confirm_required()
        if pending_at is None or now_monotonic - pending_at > _RESET_CONFIRM_TTL:
            self._reset_pending[actor_qq] = now_monotonic
            return T.reset_confirm_again()

        # 第二次 confirm：真正执行。
        self._reset_pending.pop(actor_qq, None)
        try:
            counts = await self.db.reset_checkin_data()
            file_stats = await self.file_service.purge_all()
        except Exception:  # noqa: BLE001 - 重置失败不得静默
            await self._audit(
                actor_qq,
                "reset_all",
                "database",
                message_id,
                "failed",
                utc_iso(now_utc()),
                target_type="system",
            )
            return T.reset_failed()
        await self._audit(
            actor_qq,
            "reset_all",
            "database",
            message_id,
            "ok",
            utc_iso(now_utc()),
            target_type="system",
        )
        return T.reset_done(
            counts.get("users", 0),
            counts.get("submissions", 0),
            counts.get("count_adjustments", 0),
            counts.get("pause_periods", 0),
            file_stats.get("files", 0),
        )

    async def resume(self, actor_qq: str, message_id: str) -> str:
        now = utc_iso(now_utc())
        rowcount = await self.pause_repo.resume_active(now)
        await self._audit(
            actor_qq, "resume", "pause", message_id, "ok" if rowcount else "none", now
        )
        if rowcount == 0:
            return T.resume_none()
        return T.resume_ok()

    async def effective_count(self, user: User, week_key: str) -> int:
        return await self._effective_count(user, week_key)

    async def _effective_count(self, user: User, week_key: str) -> int:
        auto = await self.submission_repo.count_auto_counted(user.id, week_key)
        adjustment = await self.admin_repo.sum_adjustments(user.id, week_key)
        return auto + adjustment

    async def _audit(
        self,
        actor_qq: str,
        action: str,
        target_id: str,
        message_id: str,
        result: str,
        now: str,
        target_type: str = "admin",
    ) -> None:
        try:
            await self.audit_repo.write(
                actor_qq=actor_qq,
                actor_role="admin",
                action=action,
                target_type=target_type,
                target_id=target_id,
                request_message_id=message_id,
                result=result,
                now=now,
            )
        except Exception:  # noqa: BLE001
            pass
