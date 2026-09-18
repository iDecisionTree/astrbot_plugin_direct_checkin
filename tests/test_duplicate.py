import asyncio
from pathlib import Path

from astrbot_plugin_direct_checkin.models.enums import (
    DuplicateOutcome,
    DuplicateType,
    SubmissionStatus,
)
from astrbot_plugin_direct_checkin.repositories.db import Database
from astrbot_plugin_direct_checkin.repositories.submission_repo import SubmissionRepository
from astrbot_plugin_direct_checkin.repositories.user_repo import UserRepository
from astrbot_plugin_direct_checkin.services.duplicate_service import DuplicateService
from astrbot_plugin_direct_checkin.utils.text_utils import text_sha256

NOW = "2026-01-01T00:00:00+00:00"


async def _setup(tmp_path: Path):
    db = Database(tmp_path / "checkin.db")
    await db.initialize()
    users = UserRepository(db)
    submissions = SubmissionRepository(db)
    service = DuplicateService(submissions, 0.85, 0.97, 8, 20000, True)
    user = await users.create("1001", "2026123456", "张三", None, NOW)
    return users, submissions, service, user


async def _finalize(submissions, user, submission_id, sha, text):
    await submissions.create_pending(
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
        stored_path="",
        file_size=1,
        sha256=sha,
        now=NOW,
    )
    await submissions.update(
        submission_id,
        status=SubmissionStatus.VALID_COUNTED.value,
        normalized_text_sha256=text_sha256(text),
        updated_at=NOW,
    )


def test_exact_duplicate_by_sha(tmp_path: Path):
    async def scenario() -> None:
        _, submissions, service, user = await _setup(tmp_path)
        await _finalize(submissions, user, "s1", "sha-1", "实现了上传接口")
        result = await service.check(user, "sha-1", "", NOW)
        assert result.outcome == DuplicateOutcome.HARD
        assert result.duplicate_type == DuplicateType.EXACT

    asyncio.run(scenario())


def test_text_duplicate_by_hash(tmp_path: Path):
    async def scenario() -> None:
        _, submissions, service, user = await _setup(tmp_path)
        await _finalize(submissions, user, "s1", "sha-1", "实现了上传接口")
        result = await service.check(user, "sha-2", "实现了上传接口", NOW)
        assert result.outcome == DuplicateOutcome.HARD
        assert result.duplicate_type == DuplicateType.TEXT

    asyncio.run(scenario())


def test_cross_user_duplicate_is_suspicious(tmp_path: Path):
    async def scenario() -> None:
        users, submissions, service, user = await _setup(tmp_path)
        other = await users.create("1002", "2026000001", "李四", None, NOW)
        await _finalize(submissions, other, "s-other", "sha-shared", "别人提交的材料")
        result = await service.check(user, "sha-shared", "", NOW)
        assert result.outcome == DuplicateOutcome.SUSPICIOUS
        assert result.duplicate_type == DuplicateType.CROSS_USER
        assert result.previous_summary == ""

    asyncio.run(scenario())


def test_new_submission_is_ok(tmp_path: Path):
    async def scenario() -> None:
        _, submissions, service, user = await _setup(tmp_path)
        await _finalize(submissions, user, "s1", "sha-1", "昨天实现了上传接口")
        result = await service.check(
            user, "sha-2", "今天学习了依赖注入并重构了鉴权逻辑，补充了测试", NOW
        )
        assert result.outcome == DuplicateOutcome.OK
        assert result.duplicate_type == DuplicateType.NONE

    asyncio.run(scenario())
