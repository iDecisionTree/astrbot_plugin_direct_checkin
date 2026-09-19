"""管理员命令服务：管理员维护、人工计次、暂停与恢复。"""

from __future__ import annotations

import re
import time
from datetime import timedelta

from ..models.entities import User
from ..models.enums import PauseScope
from ..repositories.admin_repo import AdminRepository
from ..repositories.audit_repo import AuditRepository
from ..repositories.db import Database
from ..repositories.pause_repo import PauseRepository
from ..repositories.submission_repo import SubmissionRepository
from ..repositories.user_repo import UserRepository
from ..utils import response_templates as T
from ..utils.request_context import received_at
from ..utils.scoring import week_counted as compute_week_counted
from ..utils.time_utils import (
    day_end_beijing,
    now_utc,
    to_beijing,
    utc_iso,
    week_end_beijing,
    week_key_of,
)
from .file_service import FileService
from .reset_service import ResetService

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
        reason_max_length: int = 100,
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
        self._reset_pending: dict[str, tuple[float, str]] = {}
        self.reason_max_length = reason_max_length
        self.reset_service = ResetService(db, file_service)

    async def is_admin(self, qq: str) -> bool:
        return await self.admin_repo.is_admin(qq)

    async def add_admin(self, actor_qq: str, target_qq: str, message_id: str) -> str:
        target_qq = (target_qq or "").strip()
        if not _DIGITS.match(target_qq):
            return T.admin_op_failed("QQ 号必须是纯数字")
        now = utc_iso(received_at.get() or now_utc())
        inserted = await self.admin_repo.add(target_qq, actor_qq, now)
        await self._audit(
            actor_qq, "admin_add", target_qq, message_id, "ok" if inserted else "exists", now
        )
        return T.admin_added(target_qq) if inserted else T.admin_already(target_qq)

    async def remove_admin(self, actor_qq: str, target_qq: str, message_id: str) -> str:
        target_qq = (target_qq or "").strip()
        if not _DIGITS.match(target_qq):
            return T.admin_op_failed("QQ 号必须是纯数字")
        result = await self.admin_repo.remove_protected(target_qq, self.super_admin_qq)
        await self._audit(
            actor_qq,
            "admin_remove",
            target_qq,
            message_id,
            result,
            utc_iso(received_at.get() or now_utc()),
        )
        return {
            "missing": T.admin_not_found_admin,
            "protected": lambda _: T.admin_super_protected(),
            "last": lambda _: T.admin_last_one(),
            "removed": T.admin_removed,
        }[result](target_qq)

    async def adjust(self, actor_qq: str, target_value: str, delta: int, message_id: str) -> str:
        target_value = (target_value or "").strip()
        if not target_value:
            return T.admin_op_failed("请提供学号或 QQ")
        user, conflict = await self.user_repo.resolve_identifier(target_value)
        if conflict:
            return T.admin_op_failed("这个数字同时匹配到不同用户，请使用 sid:学号 或 qq:QQ号")
        if user is None:
            return T.admin_op_failed("没有找到对应用户")

        now_dt = received_at.get() or now_utc()
        week_key = week_key_of(now_dt, self.timezone)
        beijing_date = to_beijing(now_dt, self.timezone).strftime("%Y-%m-%d")
        now = utc_iso(now_dt)
        target_display = T.target_display(user.name, user.student_id)
        target = await self.db.weekly_target(week_key, self.weekly_limit)

        if delta > 0:
            # 人工 +1 与自动打卡共享本周目标额度，超出记为额外。
            _adjustment, counted, week_total = await self.admin_repo.allocate_positive_adjustment(
                user.id,
                week_key,
                beijing_date,
                target,
                actor_qq,
                now,
            )
            await self._audit(
                actor_qq,
                "count_adjust",
                str(user.id),
                message_id,
                f"delta={delta} counted={int(counted)}",
                now,
                target_type="user",
            )
            if counted:
                return T.admin_op_ok(target_display, week_total, target)
            return T.admin_op_ok_extra(target_display, target)

        total = await self.admin_repo.deduct(user.id, week_key, beijing_date, target, actor_qq, now)
        if total is None:
            return T.admin_op_failed("当前周计次已经是 0，不能再减少")
        await self._audit(
            actor_qq,
            "count_adjust",
            str(user.id),
            message_id,
            f"delta={delta}",
            now,
            target_type="user",
        )
        return T.admin_op_ok(target_display, total, target)

    async def skip(self, actor_qq: str, scope: str, reason: str, message_id: str) -> str:
        if len(reason) > self.reason_max_length:
            return T.admin_op_failed(f"暂停原因不能超过 {self.reason_max_length} 个字符")
        aliases = {
            "d": PauseScope.DAY.value,
            "day": PauseScope.DAY.value,
            "w": PauseScope.WEEK.value,
            "week": PauseScope.WEEK.value,
        }
        scope = aliases.get((scope or "").strip().lower(), "")
        if not scope:
            return T.admin_op_failed("暂停范围只能是 d 或 w")
        now_dt = received_at.get() or now_utc()
        now = utc_iso(now_dt)
        if scope == PauseScope.DAY.value:
            end = day_end_beijing(now_dt, self.timezone) + timedelta(microseconds=1)
            exemption = None
        else:
            week_key = week_key_of(now_dt, self.timezone)
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
        pending = self._reset_pending.get(actor_qq)
        pending_at = pending[0] if pending else None
        if not confirm:
            self._reset_pending.pop(actor_qq, None)
            return T.reset_confirm_required()
        if pending_at is None or now_monotonic - pending_at > _RESET_CONFIRM_TTL:
            self._reset_pending[actor_qq] = (now_monotonic, message_id)
            return T.reset_confirm_again()

        if pending and pending[1] == message_id:
            return T.reset_confirm_again()
        self._reset_pending.pop(actor_qq, None)
        counts, result = await self.reset_service.execute()
        await self._audit(
            actor_qq,
            "reset_all",
            "database",
            message_id,
            "ok" if counts is not None else str(result),
            utc_iso(received_at.get() or now_utc()),
            target_type="system",
        )
        if counts is None:
            return str(result)
        return T.reset_done(
            counts.get("users", 0),
            counts.get("submissions", 0),
            counts.get("count_adjustments", 0),
            counts.get("pause_periods", 0),
            result,
        )

    async def resume(self, actor_qq: str, message_id: str) -> str:
        now = utc_iso(received_at.get() or now_utc())
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
        manual_counted = await self.admin_repo.count_counted_in_week(user.id, week_key)
        manual_negatives = await self.admin_repo.count_negatives_in_week(user.id, week_key)
        target = await self.db.weekly_target(week_key, self.weekly_limit)
        return compute_week_counted(auto, manual_counted, manual_negatives, target)

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
