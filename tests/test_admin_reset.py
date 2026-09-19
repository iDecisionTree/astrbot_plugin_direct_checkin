import asyncio
from pathlib import Path

from astrbot_plugin_direct_checkin.repositories.admin_repo import AdminRepository
from astrbot_plugin_direct_checkin.repositories.audit_repo import AuditRepository
from astrbot_plugin_direct_checkin.repositories.db import Database
from astrbot_plugin_direct_checkin.repositories.pause_repo import PauseRepository
from astrbot_plugin_direct_checkin.repositories.submission_repo import SubmissionRepository
from astrbot_plugin_direct_checkin.repositories.user_repo import UserRepository
from astrbot_plugin_direct_checkin.services.admin_service import AdminService
from astrbot_plugin_direct_checkin.services.export_service import ExportService
from astrbot_plugin_direct_checkin.services.file_service import FileService
from astrbot_plugin_direct_checkin.utils.time_utils import now_utc, utc_iso
from openpyxl import load_workbook

NOW = "2026-01-01T00:00:00+00:00"
SUPER = "2747344390"


async def _make_service(tmp_path: Path, nas: Path):
    db = Database(tmp_path / "checkin.db")
    await db.initialize()
    repos = {
        "admin_repo": AdminRepository(db),
        "user_repo": UserRepository(db),
        "submission_repo": SubmissionRepository(db),
        "pause_repo": PauseRepository(db),
        "audit_repo": AuditRepository(db),
    }
    service = AdminService(
        **repos,
        db=db,
        file_service=FileService(str(nas), 20, None),
        weekly_limit=2,
        timezone="Asia/Shanghai",
        super_admin_qq=SUPER,
    )
    return db, repos, service


def test_super_admin_cannot_be_removed(tmp_path: Path):
    async def scenario() -> None:
        nas = tmp_path / "nas"
        nas.mkdir()
        _, repos, service = await _make_service(tmp_path, nas)
        admins = repos["admin_repo"]
        await admins.add(SUPER, "bootstrap", NOW)
        await admins.add("111111111", "add", NOW)

        message = await service.remove_admin("111111111", SUPER, "m1")
        assert "超级管理员" in message
        assert await admins.is_admin(SUPER) is True
        assert await admins.count() == 2

        removed = await service.remove_admin(SUPER, "111111111", "m2")
        assert "移除好啦" in removed
        assert await admins.is_admin("111111111") is False

    asyncio.run(scenario())


def test_skip_accepts_d_and_w_scope(tmp_path: Path):
    async def scenario() -> None:
        nas = tmp_path / "nas"
        nas.mkdir()
        _, repos, service = await _make_service(tmp_path, nas)
        admins = repos["admin_repo"]
        pauses = repos["pause_repo"]
        await admins.add(SUPER, "bootstrap", NOW)

        message_d = await service.skip(SUPER, "d", "系统维护", "m1")
        assert "暂停" in message_d
        active_day = await pauses.active(utc_iso(now_utc()))
        assert active_day is not None and active_day.scope == "day"

        await service.resume(SUPER, "m2")
        message_w = await service.skip(SUPER, "w", "期中考试", "m3")
        assert "暂停" in message_w
        active_week = await pauses.active(utc_iso(now_utc()))
        assert active_week is not None and active_week.scope == "week"

        invalid = await service.skip(SUPER, "x", "", "m4")
        assert "没改成功" in invalid

    asyncio.run(scenario())


def test_adjust_shares_weekly_cap(tmp_path: Path):
    async def scenario() -> None:
        nas = tmp_path / "nas"
        nas.mkdir()
        _, repos, service = await _make_service(tmp_path, nas)
        await repos["admin_repo"].add(SUPER, "bootstrap", NOW)
        await repos["user_repo"].create("1001", "2026000001", "张三", None, NOW)

        first = await service.adjust(SUPER, "2026000001", 1, "m1")
        assert "1/2" in first
        second = await service.adjust(SUPER, "2026000001", 1, "m2")
        assert "2/2" in second
        third = await service.adjust(SUPER, "2026000001", 1, "m3")
        assert "额外" in third
        reduced = await service.adjust(SUPER, "2026000001", -1, "m4")
        assert "1/2" in reduced

    asyncio.run(scenario())


def test_export_not_paused_after_resume(tmp_path: Path):
    async def scenario() -> None:
        nas = tmp_path / "nas"
        nas.mkdir()
        _, repos, service = await _make_service(tmp_path, nas)
        await repos["admin_repo"].add(SUPER, "bootstrap", NOW)
        await repos["user_repo"].create("1001", "2026000001", "张三", None, NOW)

        await service.skip(SUPER, "w", "期中考试", "m1")
        await service.resume(SUPER, "m2")

        export = ExportService(
            user_repo=repos["user_repo"],
            submission_repo=repos["submission_repo"],
            admin_repo=repos["admin_repo"],
            pause_repo=repos["pause_repo"],
            export_dir=tmp_path / "exports",
            weekly_limit=2,
            timezone="Asia/Shanghai",
        )
        bundle = await export.export()
        workbook = load_workbook(bundle.path)
        summary = workbook["用户汇总"]
        headers = [cell.value for cell in summary[1]]
        row = [cell.value for cell in summary[2]]
        data = dict(zip(headers, row, strict=True))
        assert data["本周状态"] != "暂停周"
        assert data["本周目标"] == 2

    asyncio.run(scenario())


def test_reset_requires_two_confirms_and_purges_nas(tmp_path: Path):
    async def scenario() -> None:
        nas = tmp_path / "nas"
        student_dir = nas / "2026000001"
        student_dir.mkdir(parents=True)
        (student_dir / "a.docx").write_bytes(b"docx")
        (nas / "loose.docx").write_bytes(b"docx")

        _, repos, service = await _make_service(tmp_path, nas)
        admins = repos["admin_repo"]
        users = repos["user_repo"]
        await admins.add(SUPER, "bootstrap", NOW)
        await users.create("1001", "2026000001", "张三", None, NOW)

        first = await service.reset(SUPER, False, "m1")
        assert "确认" in first
        await service.reset(SUPER, True, "m2")  # 第一次 confirm，仅再确认
        # 数据与文件仍在
        assert len(await users.list_users()) == 1
        assert (student_dir / "a.docx").exists()

        done = await service.reset(SUPER, True, "m3")  # 第二次 confirm，执行
        assert "重置完成" in done

        assert await users.list_users() == []
        assert not (student_dir / "a.docx").exists()
        assert not (nas / "loose.docx").exists()
        assert nas.is_dir()  # 归档根目录保留
        assert await admins.is_admin(SUPER) is True

    asyncio.run(scenario())
