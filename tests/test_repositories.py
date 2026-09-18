import asyncio
from pathlib import Path

from astrbot_plugin_direct_checkin.models.enums import SubmissionStatus
from astrbot_plugin_direct_checkin.repositories.admin_repo import AdminRepository
from astrbot_plugin_direct_checkin.repositories.db import Database
from astrbot_plugin_direct_checkin.repositories.pause_repo import PauseRepository
from astrbot_plugin_direct_checkin.repositories.submission_repo import SubmissionRepository
from astrbot_plugin_direct_checkin.repositories.user_repo import UserRepository

NOW = "2026-01-01T00:00:00+00:00"


async def _make_pending(submissions, user, submission_id: str):
    return await submissions.create_pending(
        submission_id=submission_id,
        user_id=user.id,
        qq_id=user.qq_id,
        student_id=user.student_id,
        name=user.name,
        submitted_at=NOW,
        week_key="2025-12-29",
        qq_group_id=None,
        qq_message_id=f"msg-{submission_id}",
        quoted_message_id="q",
        original_filename="a.docx",
        stored_path="x.docx",
        file_size=1,
        sha256=f"sha-{submission_id}",
        now=NOW,
    )


def test_counted_slots_respect_weekly_limit(tmp_path: Path):
    async def scenario() -> None:
        db = Database(tmp_path / "checkin.db")
        await db.initialize()
        users = UserRepository(db)
        submissions = SubmissionRepository(db)
        user = await users.create("1001", "2026123456", "张三", None, NOW)

        await _make_pending(submissions, user, "s1")
        status1, slot1, total1 = await submissions.finalize_counted(
            "s1", user.id, "2025-12-29", 2, {}
        )
        assert (status1, slot1, total1) == (SubmissionStatus.VALID_COUNTED.value, 1, 1)

        await _make_pending(submissions, user, "s2")
        status2, slot2, total2 = await submissions.finalize_counted(
            "s2", user.id, "2025-12-29", 2, {}
        )
        assert (status2, slot2, total2) == (SubmissionStatus.VALID_COUNTED.value, 2, 2)

        await _make_pending(submissions, user, "s3")
        status3, slot3, total3 = await submissions.finalize_counted(
            "s3", user.id, "2025-12-29", 2, {}
        )
        assert status3 == SubmissionStatus.VALID_EXTRA.value
        assert slot3 is None
        assert total3 == 2

        assert await submissions.count_auto_counted(user.id, "2025-12-29") == 2

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
        await admins.add_adjustment(user.id, "2025-12-29", 1, "2747344390", NOW)
        await admins.add_adjustment(user.id, "2025-12-29", -1, "2747344390", NOW)
        await admins.add_adjustment(user.id, "2025-12-29", 1, "2747344390", NOW)
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

        rowcount = await pauses.resume_active("2026-01-02T00:00:00+00:00")
        assert rowcount == 1
        assert await pauses.active("2026-01-02T00:00:01+00:00") is None

        exempt = await pauses.exempted_week("2025-12-29")
        assert exempt is not None and exempt.reason == "期中考试"

    asyncio.run(scenario())
