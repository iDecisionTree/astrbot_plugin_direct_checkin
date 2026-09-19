"""用户绑定服务。"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from ..models.entities import User
from ..repositories.user_repo import UserRepository

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


class BindStatus:
    OK = "ok"
    ALREADY_SAME = "already_same"
    ALREADY_OTHER = "already_other"
    STUDENT_CONFLICT = "student_conflict"
    INVALID = "invalid"


@dataclass(slots=True)
class BindResult:
    status: str
    user: User | None = None
    reason: str = ""


class BindingService:
    def __init__(
        self, user_repo: UserRepository, student_id_regex: str, name_max_length: int
    ) -> None:
        self.user_repo = user_repo
        try:
            self.student_id_pattern = re.compile(student_id_regex)
        except re.error:
            self.student_id_pattern = re.compile(r"^\d{6,20}$")
        self.name_max_length = max(1, name_max_length)

    def validate(self, student_id: str, name: str) -> str:
        """返回错误原因，合法时返回空字符串。"""

        if not student_id or not self.student_id_pattern.fullmatch(student_id):
            return "学号格式不符合要求"
        if not name:
            return "姓名不能为空"
        if len(name) > self.name_max_length:
            return f"姓名不能超过 {self.name_max_length} 个字符"
        if _CONTROL_CHARS.search(name):
            return "姓名包含非法字符"
        return ""

    async def bind(
        self,
        qq_id: str,
        student_id: str,
        name: str,
        group_id: str | None,
        now: str,
    ) -> BindResult:
        student_id = (student_id or "").strip()
        name = (name or "").strip()
        reason = self.validate(student_id, name)
        if reason:
            return BindResult(BindStatus.INVALID, reason=reason)

        existing = await self.user_repo.get_by_qq(qq_id)
        if existing:
            if existing.student_id == student_id and existing.name == name:
                return BindResult(BindStatus.ALREADY_SAME, user=existing)
            return BindResult(BindStatus.ALREADY_OTHER, user=existing)

        owner = await self.user_repo.get_by_student_id(student_id)
        if owner:
            return BindResult(BindStatus.STUDENT_CONFLICT, user=owner)

        try:
            user = await self.user_repo.create(qq_id, student_id, name, group_id, now)
        except sqlite3.IntegrityError:
            existing = await self.user_repo.get_by_qq(qq_id)
            if existing:
                same = existing.student_id == student_id and existing.name == name
                return BindResult(
                    BindStatus.ALREADY_SAME if same else BindStatus.ALREADY_OTHER, user=existing
                )
            owner = await self.user_repo.get_by_student_id(student_id)
            if owner:
                return BindResult(BindStatus.STUDENT_CONFLICT, user=owner)
            raise
        return BindResult(BindStatus.OK, user=user)
