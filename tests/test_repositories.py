import asyncio
from pathlib import Path

from astrbot_plugin_direct_checkin.models.enums import SubmissionStatus
from astrbot_plugin_direct_checkin.repositories.admin_repo import AdminRepository
from astrbot_plugin_direct_checkin.repositories.db import Database
from astrbot_plugin_direct_checkin.repositories.pause_repo import PauseRepository
from astrbot_plugin_direct_checkin.repositories.submission_repo import SubmissionRepository
from astrbot_plugin_direct_checkin.repositories.user_repo import UserRepository

NOW = "2026-01-01T00:00:00+00:00"


async def _make_pending(submissions, user, submission_id: str, beijing_date: str = "2025-12-29"):
    return await submissions.create_pending(
        submission_id=submission_id,
        user_id=user.id,
        qq_id=user.qq_id,
        student_id=user.student_id,
        name=user.name,
        submitted_at=NOW,
        week_key="2025-12-29",
        beijing_date=beijing_date,
        qq_group_id=None,
        qq_message_id=f"msg-{submission_id}",
        quoted_message_id="q",
        original_filename="a.docx",
        stored_path="x.docx",
        file_size=1,
        sha256=f"sha-{submission_id}",
        now=NOW,
    )


def test_counted_slots_respect_weekly_and_daily_limits(tmp_path: Path):
    async def scenario() -> None:
        db = Database(tmp_path / "checkin.db")
        await db.initialize()
        users = UserRepository(db)
        submissions = SubmissionRepository(db)
        user = await users.create("1001", "2026123456", "张三", None, NOW)

        await _make_pending(submissions, user, "s1", "2025-12-29")
        status1, slot1, total1, reason1 = await submissions.finalize_counted(
            "s1", user.id, "2025-12-29", "2025-12-29", 2, {}
        )
        assert (status1, slot1, total1, reason1) == (
            SubmissionStatus.VALID_COUNTED.value,
            1,
            1,
            "counted",
        )

        # 同一天第二份：每日只能计一次，记为额外。
        await _make_pending(submissions, user, "s2", "2025-12-29")
        status2, slot2, total2, reason2 = await submissions.finalize_counted(
            "s2", user.id, "2025-12-29", "2025-12-29", 2, {}
        )
        assert status2 == SubmissionStatus.VALID_EXTRA.value
        assert slot2 is None and total2 == 1 and reason2 == "same_day"

        # 换一天：占用第 2 槽。
        await _make_pending(submissions, user, "s3", "2025-12-30")
        status3, slot3, total3, reason3 = await submissions.finalize_counted(
            "s3", user.id, "2025-12-29", "2025-12-30", 2, {}
        )
        assert (status3, slot3, total3, reason3) == (
            SubmissionStatus.VALID_COUNTED.value,
            2,
            2,
            "counted",
        )

        # 第 3 个不同日：本周已满，额外。
        await _make_pending(submissions, user, "s4", "2025-12-31")
        status4, slot4, total4, reason4 = await submissions.finalize_counted(
            "s4", user.id, "2025-12-29", "2025-12-31", 2, {}
        )
        assert status4 == SubmissionStatus.VALID_EXTRA.value
        assert slot4 is None and total4 == 2 and reason4 == "week_full"

        assert await submissions.count_auto_counted(user.id, "2025-12-29") == 2

    asyncio.run(scenario())


def test_manual_adjustment_shares_weekly_slots(tmp_path: Path):
    async def scenario() -> None:
        db = Database(tmp_path / "checkin.db")
        await db.initialize()
        users = UserRepository(db)
        submissions = SubmissionRepository(db)
        admins = AdminRepository(db)
        user = await users.create("1001", "2026123456", "张三", None, NOW)

        # 人工先占第 1 槽。
        _adj, counted1, total1 = await admins.allocate_positive_adjustment(
            user.id, "2025-12-29", "2025-12-29", 2, "2747344390", NOW
        )
        assert counted1 is True and total1 == 1

        # 自动打卡占第 2 槽。
        await _make_pending(submissions, user, "s1", "2025-12-30")
        status, slot, total, reason = await submissions.finalize_counted(
            "s1", user.id, "2025-12-29", "2025-12-30", 2, {}
        )
        assert (status, slot, total, reason) == (
            SubmissionStatus.VALID_COUNTED.value,
            2,
            2,
            "counted",
        )

        # 再来人工 +1，本周已满，记为额外。
        _adj2, counted2, total2 = await admins.allocate_positive_adjustment(
            user.id, "2025-12-29", "2025-12-31", 2, "2747344390", NOW
        )
        assert counted2 is False and total2 == 2

    asyncio.run(scenario())


def test_message_id_is_idempotent(tmp_path: Path):
    async def scenario() -> None:
        db = Database(tmp_path / "checkin.db")
        await db.initialize()
        users = UserRepository(db)
        submissions = SubmissionRepository(db)
        user = await users.create("1001", "2026123456", "张三", None, NOW)

        await _make_pending(submissions, user, "s1")
        existing = await submissions.get_by_message_id("msg-s1")
        assert existing is not None
        assert existing.id == "s1"

    asyncio.run(scenario())


def test_adjustments_sum_per_week(tmp_path: Path):
    async def scenario() -> None:
        db = Database(tmp_path / "checkin.db")
        await db.initialize()
        users = UserRepository(db)
        admins = AdminRepository(db)
        user = await users.create("1001", "2026123456", "张三", None, NOW)
        await admins.add_adjustment(user.id, "2025-12-29", 1, "2747344390", NOW, "2025-12-29")
        await admins.add_adjustment(user.id, "2025-12-29", -1, "2747344390", NOW, "2025-12-29")
        await admins.add_adjustment(user.id, "2025-12-29", 1, "2747344390", NOW, "2025-12-29")
        assert await admins.sum_adjustments(user.id, "2025-12-29") == 1
        by_week = await admins.sum_adjustments_by_week("2025-12-29")
        assert by_week[user.id] == 1

    asyncio.run(scenario())


def test_pause_active_resume_and_week_exemption(tmp_path: Path):
    async def scenario() -> None:
        db = Database(tmp_path / "checkin.db")
        await db.initialize()
        pauses = PauseRepository(db)

        started = "2026-01-01T00:00:00+00:00"
        ended = "2026-01-07T16:00:00+00:00"
        await pauses.create("week", started, ended, "期中考试", "2747344390", started, "2025-12-29")

        active = await pauses.active("2026-01-02T00:00:00+00:00")
        assert active is not None and active.scope == "week"

        exempt = await pauses.exempted_week("2025-12-29")
        assert exempt is not None and exempt.reason == "期中考试"

        rowcount = await pauses.resume_active("2026-01-02T00:00:00+00:00")
        assert rowcount == 1
        assert await pauses.active("2026-01-02T00:00:01+00:00") is None
        # resume 后本周不再豁免，重新要求打卡。
        assert await pauses.exempted_week("2025-12-29") is None

    asyncio.run(scenario())


def test_reset_clears_checkin_data_but_keeps_admins(tmp_path: Path):
    async def scenario() -> None:
        db = Database(tmp_path / "checkin.db")
        await db.initialize()
        users = UserRepository(db)
        submissions = SubmissionRepository(db)
        admins = AdminRepository(db)
        pauses = PauseRepository(db)

        await admins.add("2747344390", "bootstrap", NOW)
        user = await users.create("1001", "2026123456", "张三", None, NOW)
        await _make_pending(submissions, user, "s1")
        await admins.add_adjustment(user.id, "2025-12-29", 1, "2747344390", NOW, "2025-12-29")
        await pauses.create("day", NOW, NOW, "维护", "2747344390", NOW)

        counts = await db.reset_checkin_data()
        assert counts["users"] == 1
        assert counts["submissions"] == 1
        assert counts["count_adjustments"] == 1
        assert counts["pause_periods"] == 1

        assert await users.list_users() == []
        assert await submissions.list_submissions() == []
        assert await admins.list_adjustments() == []
        assert await pauses.list_pauses() == []
        # 管理员表保留，避免重置后插件失管。
        assert await admins.count() == 1
        assert await admins.is_admin("2747344390") is True

    asyncio.run(scenario())


def test_daily_participant_order_is_unique_stable_and_per_day(tmp_path):
    async def run():
        db = Database(tmp_path / "daily.db")
        await db.initialize()
        users, repo = UserRepository(db), SubmissionRepository(db)
        people = [
            await users.create(str(i), f"2026000{i}", f"同学{i}", None, NOW) for i in range(6)
        ]
        await asyncio.gather(
            *(
                _make_pending(repo, user, f"first-{i}", "2026-09-20")
                for i, user in enumerate(people)
            )
        )
        positions = [await repo.daily_participant_position(f"first-{i}") for i in range(6)]
        assert sorted(positions) == list(range(1, 7))
        # 技术失败和同日重试不让已分配的序号移动或给同一用户另占一个号。
        await repo.update("first-0", status="AI_ERROR")
        await _make_pending(repo, people[0], "retry", "2026-09-20")
        assert await repo.daily_participant_position("retry") == positions[0]
        assert [await repo.daily_participant_position(f"first-{i}") for i in range(6)] == positions
        # 重启保持序号；即使审核完成顺序相反，也不依赖完成时间。
        await repo.update("first-5", status="VALID_EXTRA")
        await db.initialize()
        assert await SubmissionRepository(db).daily_participant_position("first-5") == positions[5]
        await _make_pending(repo, people[0], "tomorrow", "2026-09-21")
        assert await repo.daily_participant_position("tomorrow") == 1
        assert await repo.daily_participant_position("missing") is None

    asyncio.run(run())
