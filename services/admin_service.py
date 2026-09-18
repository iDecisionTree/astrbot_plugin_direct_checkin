"""管理员命令服务：管理员维护、人工计次、暂停与恢复。"""

from __future__ import annotations

import re

from ..models.entities import User
from ..models.enums import PauseScope
from ..repositories.admin_repo import AdminRepository
from ..repositories.audit_repo import AuditRepository
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

_DIGITS = re.compile(r"^\d+$")


class AdminService:
    def __init__(
        self,
        *,
        admin_repo: AdminRepository,
        user_repo: UserRepository,
        submission_repo: SubmissionRepository,
        pause_repo: PauseRepository,
        audit_repo: AuditRepository,
        weekly_limit: int,
        timezone: str,
    ) -> None:
        self.admin_repo = admin_repo
        self.user_repo = user_repo
        self.submission_repo = submission_repo
        self.pause_repo = pause_repo
        self.audit_repo = audit_repo
        self.weekly_limit = max(1, weekly_limit)
        self.timezone = timezone

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
        scope = (scope or "").lower()
        if scope not in {PauseScope.DAY.value, PauseScope.WEEK.value}:
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
