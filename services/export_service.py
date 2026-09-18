"""Excel 导出服务。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..models.enums import SubmissionStatus
from ..repositories.admin_repo import AdminRepository
from ..repositories.pause_repo import PauseRepository
from ..repositories.submission_repo import SubmissionRepository
from ..repositories.user_repo import UserRepository
from ..utils.time_utils import (
    format_beijing,
    now_utc,
    parse_iso,
    to_beijing,
    week_key_now,
)

_COUNTED = {SubmissionStatus.VALID_COUNTED.value}
_EXTRA = {SubmissionStatus.VALID_EXTRA.value}
_INVALID = {
    SubmissionStatus.REJECTED_AI.value,
    SubmissionStatus.REJECTED_DUPLICATE.value,
    SubmissionStatus.REJECTED_FILE.value,
    SubmissionStatus.AI_ERROR.value,
    SubmissionStatus.PROCESSING_ERROR.value,
}

_USER_HEADERS = [
    "QQ号",
    "学号",
    "姓名",
    "方向（预留）",
    "绑定时间",
    "当前周自动计分次数",
    "当前周人工调整",
    "当前周有效计次",
    "当前周应打卡次数",
    "当前周是否完成",
    "累计自动有效计分次数",
    "累计额外有效提交数",
    "累计无效提交数",
    "最后打卡时间",
]

_DETAIL_HEADERS = [
    "submission_id",
    "QQ号",
    "学号",
    "姓名",
    "提交时间（北京时间）",
    "周起始日期",
    "QQ 群/会话 ID",
    "原消息 ID",
    "引用消息 ID",
    "原文件名",
    "NAS 保存路径",
    "文件大小",
    "SHA256",
    "文本哈希",
    "相似度",
    "重复检测类型",
    "状态",
    "是否计次",
    "AI 判定",
    "AI 置信度",
    "AI 简评",
    "AI 模型/Provider ID",
    "AI 耗时(ms)",
    "错误信息",
]

_ADJUST_HEADERS = ["时间", "周次", "目标 QQ", "学号", "姓名", "delta", "操作管理员 QQ"]

_PAUSE_HEADERS = [
    "scope",
    "开始时间",
    "计划结束时间",
    "实际恢复时间",
    "原因",
    "管理员 QQ",
    "是否豁免本周",
]


@dataclass(slots=True)
class ExportBundle:
    path: Path | None
    filename: str
    empty: bool = False


class ExportService:
    def __init__(
        self,
        *,
        user_repo: UserRepository,
        submission_repo: SubmissionRepository,
        admin_repo: AdminRepository,
        pause_repo: PauseRepository,
        export_dir: Path,
        weekly_limit: int,
        timezone: str,
    ) -> None:
        self.user_repo = user_repo
        self.submission_repo = submission_repo
        self.admin_repo = admin_repo
        self.pause_repo = pause_repo
        self.export_dir = Path(export_dir)
        self.weekly_limit = max(1, weekly_limit)
        self.timezone = timezone

    async def export(self, user_id: int | None = None) -> ExportBundle:
        users = await self.user_repo.list_users()
        if user_id is not None:
            users = [user for user in users if user.id == user_id]
        if not users:
            return ExportBundle(path=None, filename="", empty=True)

        submissions = await self.submission_repo.list_submissions(user_id)
        adjustments = await self.admin_repo.list_adjustments(user_id)
        pauses = await self.pause_repo.list_pauses()

        week_key = week_key_now(self.timezone)
        auto_by_user = await self.submission_repo.count_auto_counted_by_week(week_key)
        adjust_by_user = await self.admin_repo.sum_adjustments_by_week(week_key)
        exemption = await self.pause_repo.exempted_week(week_key)
        week_paused = exemption is not None
        required = 0 if week_paused else self.weekly_limit

        user_by_id = {user.id: user for user in users}
        submissions_by_user: dict[int, list] = {user.id: [] for user in users}
        for item in submissions:
            submissions_by_user.setdefault(item.user_id, []).append(item)

        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("服务端缺少 openpyxl 依赖") from exc

        workbook = Workbook()

        summary = workbook.active
        summary.title = "用户汇总"
        summary.append(_USER_HEADERS)
        for user in users:
            auto = auto_by_user.get(user.id, 0)
            adjust = adjust_by_user.get(user.id, 0)
            effective = max(0, auto + adjust)
            records = submissions_by_user.get(user.id, [])
            counted_total = sum(1 for r in records if r.status in _COUNTED)
            extra_total = sum(1 for r in records if r.status in _EXTRA)
            invalid_total = sum(1 for r in records if r.status in _INVALID)
            if week_paused:
                completed = "暂停周"
            else:
                completed = "是" if effective >= self.weekly_limit else "否"
            summary.append(
                [
                    user.qq_id,
                    user.student_id,
                    user.name,
                    user.direction or "",
                    format_beijing(parse_iso(user.bound_at), tz_name=self.timezone),
                    auto,
                    adjust,
                    effective,
                    required,
                    completed,
                    counted_total,
                    extra_total,
                    invalid_total,
                    format_beijing(parse_iso(user.last_submission_at), tz_name=self.timezone),
                ]
            )

        detail = workbook.create_sheet("打卡明细")
        detail.append(_DETAIL_HEADERS)
        for item in submissions:
            detail.append(
                [
                    item.id,
                    item.qq_id_snapshot,
                    item.student_id_snapshot,
                    item.name_snapshot,
                    format_beijing(parse_iso(item.submitted_at), tz_name=self.timezone),
                    item.week_key,
                    item.qq_group_id or "",
                    item.qq_message_id,
                    item.quoted_message_id,
                    item.original_filename,
                    item.stored_path,
                    item.file_size,
                    item.sha256,
                    item.normalized_text_sha256 or "",
                    item.similarity_score,
                    item.duplicate_type,
                    item.status,
                    "是" if item.counted else "否",
                    item.ai_decision or "",
                    item.ai_confidence,
                    item.ai_feedback or "",
                    item.ai_provider_id or "",
                    item.ai_latency_ms,
                    item.error_message or "",
                ]
            )

        adjust_sheet = workbook.create_sheet("人工调整")
        adjust_sheet.append(_ADJUST_HEADERS)
        for item in adjustments:
            user = user_by_id.get(item.user_id)
            adjust_sheet.append(
                [
                    format_beijing(parse_iso(item.created_at), tz_name=self.timezone),
                    item.week_key,
                    user.qq_id if user else "",
                    user.student_id if user else "",
                    user.name if user else "",
                    item.delta,
                    item.admin_qq,
                ]
            )

        pause_sheet = workbook.create_sheet("暂停记录")
        pause_sheet.append(_PAUSE_HEADERS)
        for item in pauses:
            pause_sheet.append(
                [
                    item.scope,
                    format_beijing(parse_iso(item.start_at), tz_name=self.timezone),
                    format_beijing(parse_iso(item.scheduled_end_at), tz_name=self.timezone),
                    format_beijing(parse_iso(item.resumed_at), tz_name=self.timezone),
                    item.reason,
                    item.created_by_qq,
                    item.exemption_week_key or "",
                ]
            )

        for sheet in workbook.worksheets:
            self._style_sheet(sheet, Font)

        filename = (
            f"直属队打卡_{to_beijing(now_utc(), self.timezone).strftime('%Y%m%d_%H%M%S')}.xlsx"
        )
        self.export_dir.mkdir(parents=True, exist_ok=True)
        path = self.export_dir / filename
        workbook.save(str(path))
        return ExportBundle(path=path, filename=filename, empty=False)

    @staticmethod
    def _style_sheet(sheet, font_cls) -> None:
        sheet.freeze_panes = "A2"
        if sheet.max_row >= 1 and sheet.max_column >= 1:
            sheet.auto_filter.ref = sheet.dimensions
        for cell in sheet[1]:
            cell.font = font_cls(bold=True)
        for column_cells in sheet.columns:
            length = max(
                (len(str(cell.value)) if cell.value is not None else 0) for cell in column_cells
            )
            letter = column_cells[0].column_letter
            sheet.column_dimensions[letter].width = min(max(length + 2, 10), 60)
