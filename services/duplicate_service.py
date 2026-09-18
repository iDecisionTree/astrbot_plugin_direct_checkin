"""重复检测服务。

顺序：文件哈希 -> 规范化文本哈希 -> 跨用户完全相同 -> 历史高相似。
必须在 AI 审查之前执行，以节省模型调用。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ..models.entities import Submission, User
from ..models.enums import FINAL_STATUSES, DuplicateOutcome, DuplicateType
from ..repositories.submission_repo import SubmissionRepository
from ..utils.text_utils import text_sha256, truncate

PreviousTextLoader = Callable[[Submission], Awaitable[str]]

_PREVIOUS_SUMMARY_CHARS = 500


@dataclass(slots=True)
class DuplicateResult:
    outcome: DuplicateOutcome
    duplicate_type: DuplicateType = DuplicateType.NONE
    duplicate_of: str | None = None
    similarity: float | None = None
    previous_summary: str = ""
    note: str = ""


class DuplicateService:
    def __init__(
        self,
        submission_repo: SubmissionRepository,
        warn_threshold: float,
        high_threshold: float,
        history_weeks: int,
        max_chars: int,
        enable_cross_user: bool,
    ) -> None:
        self.submission_repo = submission_repo
        self.warn_threshold = warn_threshold
        self.high_threshold = high_threshold
        self.history_weeks = max(1, history_weeks)
        self.max_chars = max(1000, max_chars)
        self.enable_cross_user = enable_cross_user

    async def check(
        self,
        user: User,
        sha256: str,
        normalized_text: str,
        since_iso: str,
        previous_text_loader: PreviousTextLoader | None = None,
    ) -> DuplicateResult:
        text_hash = text_sha256(normalized_text) if normalized_text else ""

        exact = await self.submission_repo.find_final_by_sha(user.id, sha256, set(FINAL_STATUSES))
        if exact:
            return DuplicateResult(
                outcome=DuplicateOutcome.HARD,
                duplicate_type=DuplicateType.EXACT,
                duplicate_of=exact.id,
                similarity=1.0,
            )

        if text_hash:
            text_dup = await self.submission_repo.find_final_by_text_hash(
                user.id, text_hash, set(FINAL_STATUSES)
            )
            if text_dup:
                return DuplicateResult(
                    outcome=DuplicateOutcome.HARD,
                    duplicate_type=DuplicateType.TEXT,
                    duplicate_of=text_dup.id,
                    similarity=1.0,
                )

        cross = None
        if self.enable_cross_user and sha256:
            cross = await self.submission_repo.find_cross_user_by_sha(
                sha256, user.id, set(FINAL_STATUSES)
            )

        best_similarity = -1.0
        best_submission: Submission | None = None
        best_previous_text = ""
        if previous_text_loader and normalized_text:
            candidates = await self.submission_repo.recent_final_submissions(
                user.id, since_iso, set(FINAL_STATUSES), limit=20
            )
            from ..utils.text_utils import similarity

            for candidate in candidates:
                try:
                    previous_text = await previous_text_loader(candidate)
                except Exception:  # noqa: BLE001 - 单个历史材料解析失败不影响整体
                    continue
                if not previous_text:
                    continue
                score = similarity(normalized_text, previous_text, max_chars=self.max_chars)
                if score > best_similarity:
                    best_similarity = score
                    best_submission = candidate
                    best_previous_text = previous_text

        if best_submission and best_similarity >= self.high_threshold:
            return DuplicateResult(
                outcome=DuplicateOutcome.HARD,
                duplicate_type=DuplicateType.HIGH_SIMILARITY,
                duplicate_of=best_submission.id,
                similarity=best_similarity,
            )

        if cross:
            return DuplicateResult(
                outcome=DuplicateOutcome.SUSPICIOUS,
                duplicate_type=DuplicateType.CROSS_USER,
                duplicate_of=cross.id,
                similarity=1.0,
                note=(
                    "系统检测到存在与其他用户提交完全相同的文件。"
                    "请严格核验当前材料本身是否体现本人真实的学习与实践，"
                    "不要因为文件名或来源而直接通过。"
                ),
            )

        if best_submission and best_similarity >= self.warn_threshold:
            return DuplicateResult(
                outcome=DuplicateOutcome.OK,
                duplicate_type=DuplicateType.NONE,
                duplicate_of=best_submission.id,
                similarity=best_similarity,
                previous_summary=truncate(best_previous_text, _PREVIOUS_SUMMARY_CHARS),
            )

        return DuplicateResult(outcome=DuplicateOutcome.OK, duplicate_type=DuplicateType.NONE)
