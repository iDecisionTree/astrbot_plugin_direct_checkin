import asyncio
from pathlib import Path

from astrbot_plugin_direct_checkin.repositories.db import Database
from astrbot_plugin_direct_checkin.repositories.user_repo import UserRepository
from astrbot_plugin_direct_checkin.services.binding_service import (
    BindingService,
    BindStatus,
)

NOW = "2026-01-01T00:00:00+00:00"


def test_bind_flow(tmp_path: Path):
    async def scenario() -> None:
        db = Database(tmp_path / "checkin.db")
        await db.initialize()
        users = UserRepository(db)
        service = BindingService(users, r"^\d{6,20}$", 30)

        first = await service.bind("1001", "2026123456", "张三", "999", NOW)
        assert first.status == BindStatus.OK
        assert first.user is not None and first.user.student_id == "2026123456"

        same = await service.bind("1001", "2026123456", "张三", None, NOW)
        assert same.status == BindStatus.ALREADY_SAME

        other_id = await service.bind("1001", "9999999999", "李四", None, NOW)
        assert other_id.status == BindStatus.ALREADY_OTHER

        conflict = await service.bind("1002", "2026123456", "王五", None, NOW)
        assert conflict.status == BindStatus.STUDENT_CONFLICT

        invalid = await service.bind("1003", "abc", "赵六", None, NOW)
        assert invalid.status == BindStatus.INVALID

        blank_name = await service.bind("1004", "2026111111", "", None, NOW)
        assert blank_name.status == BindStatus.INVALID

    asyncio.run(scenario())
