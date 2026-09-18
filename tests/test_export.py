import asyncio
import re
from pathlib import Path

from astrbot_plugin_direct_checkin.models.enums import SubmissionStatus
from astrbot_plugin_direct_checkin.repositories.admin_repo import AdminRepository
from astrbot_plugin_direct_checkin.repositories.db import Database
from astrbot_plugin_direct_checkin.repositories.pause_repo import PauseRepository
from astrbot_plugin_direct_checkin.repositories.submission_repo import SubmissionRepository
from astrbot_plugin_direct_checkin.repositories.user_repo import UserRepository
from astrbot_plugin_direct_checkin.services.export_service import ExportService
from astrbot_plugin_direct_checkin.utils.time_utils import week_key_now
from openpyxl import load_workbook

NOW = "2026-01-05T02:00:00+00:00"


def test_export_workbook_structure(tmp_path: Path):
    async def scenario() -> None:
        db = Database(tmp_path / "checkin.db")
        await db.initialize()
        users = UserRepository(db)
        submissions = SubmissionRepository(db)
        admins = AdminRepository(db)
        pauses = PauseRepository(db)

        week_key = week_key_now("Asia/Shanghai")
        user = await users.create("1001", "2026123456", "张三", None, NOW)
        await submissions.create_pending(
            submission_id="s1",
            user_id=user.id,
            qq_id=user.qq_id,
            student_id=user.student_id,
            name=user.name,
            submitted_at=NOW,
            week_key=week_key,
            qq_group_id="999",
            qq_message_id="msg-1",
            quoted_message_id="q1",
            original_filename="week.docx",
            stored_path="/mnt/nas/direct_checkin/2026123456/week.docx",
            file_size=1234,
            sha256="deadbeef",
            now=NOW,
        )
        await submissions.update(
            "s1",
            status=SubmissionStatus.VALID_COUNTED.value,
            counted=1,
            counted_slot=1,
            ai_decision="pass",
            updated_at=NOW,
        )
        await admins.add_adjustment(user.id, week_key, 1, "2747344390", NOW)

        service = ExportService(
            user_repo=users,
            submission_repo=submissions,
            admin_repo=admins,
            pause_repo=pauses,
            export_dir=tmp_path / "exports",
            weekly_limit=2,
            timezone="Asia/Shanghai",
        )
        bundle = await service.export()
        assert bundle.empty is False
        assert bundle.path is not None and bundle.path.exists()

        workbook = load_workbook(bundle.path)
        assert workbook.sheetnames == ["用户汇总", "打卡明细", "人工调整", "暂停记录"]

        summary = workbook["用户汇总"]
        assert summary["A1"].value == "QQ号"
        # QQ号/学号/姓名
        assert summary["A2"].value == "1001"
        assert summary["B2"].value == "2026123456"
        # 绑定时间必须是北京时间字符串，而不是时区名。
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", str(summary["E2"].value))
        # 当前周自动计分次数=1，人工调整=1，有效计次=2，是否完成=是
        assert summary["F2"].value == 1
        assert summary["G2"].value == 1
        assert summary["H2"].value == 2
        assert summary["J2"].value == "是"
        # 累计完成周数：本周有效计次 2，计 1 周。
        assert summary["N1"].value == "累计完成周数"
        assert summary["N2"].value == 1

        detail = workbook["打卡明细"]
        assert detail.max_row == 2
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", str(detail["E2"].value))
        adjust = workbook["人工调整"]
        assert adjust.max_row == 2

    asyncio.run(scenario())
