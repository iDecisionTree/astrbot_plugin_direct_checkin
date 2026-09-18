"""打卡主流程编排。

严格顺序见需求文档 §10.2：绑定 -> 暂停 -> 引用文件 -> 安全保存 -> 建 PENDING
-> 查重 -> 提取 -> 计次状态 -> AI 审查 -> 事务定稿 -> 回复。
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from ..models.entities import Submission, User
from ..models.enums import (
    CheckinOutcome,
    DuplicateOutcome,
    ErrorCode,
    PauseScope,
    SubmissionStatus,
)
from ..repositories.admin_repo import AdminRepository
from ..repositories.audit_repo import AuditRepository
from ..repositories.pause_repo import PauseRepository
from ..repositories.submission_repo import SubmissionRepository
from ..repositories.user_repo import UserRepository
from ..utils import response_templates as T
from ..utils.time_utils import now_utc, parse_iso, to_beijing, utc_iso, week_key_of
from .ai_review_service import AIReviewOutcome, AIReviewService
from .docx_parser import DocxParseError, evidence_text, extract_content, text_for_ai
from .duplicate_service import DuplicateResult, DuplicateService
from .file_service import FileService, FileServiceError, StoredFile, locate_replied_file


@dataclass(slots=True)
class CheckinResult:
    outcome: str
    text: str = ""
    status: str | None = None
    counted_total: int | None = None

    @property
    def is_error(self) -> bool:
        return self.outcome in {
            CheckinOutcome.FILE_ERROR,
            CheckinOutcome.AI_ERROR,
        }


class CheckinService:
    def __init__(
        self,
        *,
        submission_repo: SubmissionRepository,
        user_repo: UserRepository,
        pause_repo: PauseRepository,
        admin_repo: AdminRepository,
        audit_repo: AuditRepository,
        file_service: FileService,
        duplicate_service: DuplicateService,
        ai_service: AIReviewService,
        weekly_limit: int,
        timezone: str,
        ai_max_input_chars: int,
        history_compare_weeks: int,
        logger: Any,
    ) -> None:
        self.submission_repo = submission_repo
        self.user_repo = user_repo
        self.pause_repo = pause_repo
        self.admin_repo = admin_repo
        self.audit_repo = audit_repo
        self.file_service = file_service
        self.duplicate_service = duplicate_service
        self.ai_service = ai_service
        self.weekly_limit = max(1, weekly_limit)
        self.timezone = timezone
        self.ai_max_input_chars = ai_max_input_chars
        self.history_compare_weeks = history_compare_weeks
        self.logger = logger
        self._locks: dict[int, asyncio.Lock] = {}

    def _lock_for(self, user_id: int) -> asyncio.Lock:
        lock = self._locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[user_id] = lock
        return lock

    async def handle(self, event: Any, user: User) -> CheckinResult:
        now = now_utc()
        now_iso = utc_iso(now)
        week_key = week_key_of(now, self.timezone)

        pause = await self.pause_repo.active(now_iso)
        if pause is not None:
            if pause.scope == PauseScope.DAY.value:
                return CheckinResult(CheckinOutcome.PAUSED, T.paused_day(pause.reason))
            if pause.scope == PauseScope.WEEK.value:
                return CheckinResult(CheckinOutcome.PAUSED, T.paused_week(pause.reason))

        message_id = str(getattr(event.message_obj, "message_id", "") or "")
        if message_id:
            existing = await self.submission_repo.get_by_message_id(message_id)
            if existing is not None:
                return CheckinResult(CheckinOutcome.ALREADY_PROCESSED, T.already_processed())

        lock = self._lock_for(user.id)
        async with lock:
            if message_id:
                existing = await self.submission_repo.get_by_message_id(message_id)
                if existing is not None:
                    return CheckinResult(CheckinOutcome.ALREADY_PROCESSED, T.already_processed())
            return await self._process(event, user, now_iso, week_key)

    async def _process(self, event: Any, user: User, now_iso: str, week_key: str) -> CheckinResult:
        components = getattr(event.message_obj, "message", None) or []
        replied = locate_replied_file(components)
        if replied is None:
            return CheckinResult(CheckinOutcome.FILE_ERROR, T.no_reply_file())

        submission_id = uuid.uuid4().hex
        beijing = to_beijing(now_utc(), self.timezone)
        label = beijing.strftime("%Y-%m-%d_%H-%M-%S")

        try:
            stored = await self.file_service.prepare(replied, user, submission_id, label)
        except FileServiceError as exc:
            return CheckinResult(CheckinOutcome.FILE_ERROR, self._file_error_text(exc.code))

        assert stored is not None
        try:
            submission = await self.submission_repo.create_pending(
                submission_id=submission_id,
                user_id=user.id,
                qq_id=user.qq_id,
                student_id=user.student_id,
                name=user.name,
                submitted_at=now_iso,
                week_key=week_key,
                qq_group_id=(str(event.get_group_id()) if event.get_group_id() else None),
                qq_message_id=str(getattr(event.message_obj, "message_id", "") or ""),
                quoted_message_id=replied.quoted_message_id,
                original_filename=stored.original_filename,
                stored_path=str(stored.path),
                file_size=stored.size,
                sha256=stored.sha256,
                now=now_iso,
            )
        except Exception:
            self.logger.exception("创建 PENDING 提交记录失败")
            await self.file_service.cleanup_temp(stored)
            return CheckinResult(CheckinOutcome.FILE_ERROR, T.processing_error())

        try:
            return await self._finalize_process(event, user, submission, stored, now_iso, week_key)
        except Exception:
            self.logger.exception("打卡处理异常 submission=%s", submission_id)
            try:
                await self.submission_repo.update(
                    submission_id,
                    status=SubmissionStatus.PROCESSING_ERROR.value,
                    error_code=ErrorCode.DB_ERROR,
                    error_message="处理异常",
                    updated_at=now_iso,
                )
            except Exception:  # noqa: BLE001
                self.logger.exception("回写异常状态失败")
            return CheckinResult(
                CheckinOutcome.FILE_ERROR,
                T.processing_error(),
                SubmissionStatus.PROCESSING_ERROR.value,
            )
        finally:
            await self.file_service.cleanup_temp(stored)

    async def _finalize_process(
        self,
        event: Any,
        user: User,
        submission: Submission,
        stored: StoredFile,
        now_iso: str,
        week_key: str,
    ) -> CheckinResult:
        # 1) 查重
        since = utc_iso(
            (parse_iso(now_iso) or now_utc()) - timedelta(weeks=self.history_compare_weeks)
        )
        duplicate = await self.duplicate_service.check(
            user, stored.sha256, "", since, previous_text_loader=self._load_previous_text
        )
        if duplicate.outcome == DuplicateOutcome.HARD:
            await self.submission_repo.update(
                submission.id,
                status=SubmissionStatus.REJECTED_DUPLICATE.value,
                duplicate_type=duplicate.duplicate_type.value,
                duplicate_of=duplicate.duplicate_of,
                similarity_score=duplicate.similarity,
                updated_at=now_iso,
            )
            await self._audit(user, "checkin", submission.id, "duplicate", now_iso)
            return CheckinResult(
                CheckinOutcome.DUPLICATE,
                T.duplicate_hard(),
                SubmissionStatus.REJECTED_DUPLICATE.value,
            )

        # 2) 提取正文与证据
        try:
            content = await asyncio.to_thread(extract_content, Path(stored.path))
        except DocxParseError as exc:
            await self.submission_repo.update(
                submission.id,
                status=SubmissionStatus.REJECTED_FILE.value,
                error_code=ErrorCode.DOCX_PARSE_FAILED,
                error_message=str(exc),
                updated_at=now_iso,
            )
            await self._audit(user, "checkin", submission.id, "docx_parse_failed", now_iso)
            return CheckinResult(
                CheckinOutcome.FILE_ERROR,
                T.docx_parse_failed(),
                SubmissionStatus.REJECTED_FILE.value,
            )
        await self.submission_repo.update(
            submission.id,
            normalized_text_sha256=self._text_hash(content.text),
            extracted_char_count=content.char_count,
            table_count=content.table_count,
            image_count=content.image_count,
            link_count=content.link_count,
            updated_at=now_iso,
        )

        # 3) 再次查重（此时已有文本，可做文本/相似度判定）
        duplicate = await self.duplicate_service.check(
            user,
            stored.sha256,
            content.text,
            since,
            previous_text_loader=self._load_previous_text,
        )
        if duplicate.outcome == DuplicateOutcome.HARD:
            await self.submission_repo.update(
                submission.id,
                status=SubmissionStatus.REJECTED_DUPLICATE.value,
                duplicate_type=duplicate.duplicate_type.value,
                duplicate_of=duplicate.duplicate_of,
                similarity_score=duplicate.similarity,
                updated_at=now_iso,
            )
            await self._audit(user, "checkin", submission.id, "duplicate", now_iso)
            return CheckinResult(
                CheckinOutcome.DUPLICATE,
                T.duplicate_hard(),
                SubmissionStatus.REJECTED_DUPLICATE.value,
            )

        await self.submission_repo.update(
            submission.id,
            duplicate_type=duplicate.duplicate_type.value,
            duplicate_of=duplicate.duplicate_of,
            similarity_score=duplicate.similarity,
            updated_at=now_iso,
        )

        # 4) AI 审查
        history_note = self._history_note(duplicate)
        ai_outcome = await self.ai_service.review(
            event.unified_msg_origin,
            text_for_ai(content, self.ai_max_input_chars),
            evidence_text(content),
            history_note,
        )
        if ai_outcome.error_code:
            await self.submission_repo.update(
                submission.id,
                status=SubmissionStatus.AI_ERROR.value,
                ai_provider_id=ai_outcome.provider_id,
                ai_latency_ms=ai_outcome.latency_ms,
                error_code=ai_outcome.error_code,
                error_message=ai_outcome.error_message,
                updated_at=now_iso,
            )
            await self._audit(user, "checkin", submission.id, "ai_error", now_iso)
            return CheckinResult(
                CheckinOutcome.AI_ERROR, T.ai_error(), SubmissionStatus.AI_ERROR.value
            )

        ai_fields = self._ai_fields(ai_outcome, now_iso)
        if not ai_outcome.passed:
            await self.submission_repo.update(
                submission.id, status=SubmissionStatus.REJECTED_AI.value, **ai_fields
            )
            await self._audit(user, "checkin", submission.id, "rejected_ai", now_iso)
            return CheckinResult(
                CheckinOutcome.REJECTED_AI,
                T.ai_rejected(ai_outcome.brief_feedback),
                SubmissionStatus.REJECTED_AI.value,
            )

        status, _slot, auto_total = await self.submission_repo.finalize_counted(
            submission.id, user.id, week_key, self.weekly_limit, ai_fields
        )
        await self.user_repo.touch_last_submission(user.id, now_iso)
        await self._audit(user, "checkin", submission.id, status, now_iso)

        adjustment = await self.admin_repo.sum_adjustments(user.id, week_key)
        effective = max(0, auto_total + adjustment)
        if status == SubmissionStatus.VALID_COUNTED.value:
            if auto_total <= 1:
                text = T.checkin_success_first(ai_outcome.brief_feedback)
            else:
                text = T.checkin_success_complete(ai_outcome.brief_feedback)
            return CheckinResult(CheckinOutcome.COUNTED, text, status, effective)
        return CheckinResult(CheckinOutcome.EXTRA, T.checkin_extra(), status, effective)

    async def _load_previous_text(self, submission: Submission) -> str:
        if not submission.stored_path:
            return ""
        path = Path(submission.stored_path)
        if not path.is_file():
            return ""
        try:
            content = await asyncio.to_thread(extract_content, path)
        except Exception:  # noqa: BLE001
            return ""
        return content.text

    @staticmethod
    def _text_hash(text: str) -> str:
        from ..utils.text_utils import text_sha256

        return text_sha256(text) if text else ""

    @staticmethod
    def _history_note(duplicate: DuplicateResult) -> str:
        if duplicate.note:
            return duplicate.note
        if duplicate.previous_summary:
            return (
                "该用户近期的相似材料摘要（仅供参考，判断本次是否有新进展）：\n"
                f"{duplicate.previous_summary}"
            )
        if duplicate.similarity is not None:
            return f"与历史材料相似度约为 {duplicate.similarity:.2f}。"
        return ""

    @staticmethod
    def _ai_fields(outcome: AIReviewOutcome, now_iso: str) -> dict[str, Any]:
        return {
            "ai_decision": outcome.decision,
            "ai_progress": outcome.progress,
            "ai_confidence": outcome.confidence,
            "ai_feedback": outcome.brief_feedback,
            "ai_result_json": outcome.raw_json,
            "ai_provider_id": outcome.provider_id,
            "ai_latency_ms": outcome.latency_ms,
            "updated_at": now_iso,
        }

    @staticmethod
    def _file_error_text(code: str) -> str:
        if code == ErrorCode.UNSUPPORTED_WORD_FORMAT:
            return T.unsupported_format()
        if code == ErrorCode.FILE_TOO_LARGE:
            return T.file_too_large()
        if code == ErrorCode.NAS_WRITE_FAILED:
            return T.nas_write_failed()
        if code == ErrorCode.DOCX_PARSE_FAILED:
            return T.docx_parse_failed()
        if code == ErrorCode.FILE_DOWNLOAD_FAILED:
            return T.download_failed()
        return T.processing_error()

    async def _audit(
        self, user: User, action: str, target_id: str, result: str, now_iso: str
    ) -> None:
        try:
            await self.audit_repo.write(
                actor_qq=user.qq_id,
                actor_role="user",
                action=action,
                target_type="submission",
                target_id=target_id,
                result=result,
                now=now_iso,
            )
        except Exception:  # noqa: BLE001
            self.logger.warning("写审计日志失败 action=%s", action)
