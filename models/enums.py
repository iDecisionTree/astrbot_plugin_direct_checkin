"""插件使用的枚举与错误码。"""

from __future__ import annotations

from enum import Enum


class SubmissionStatus(str, Enum):
    """提交记录的最终可审计状态。"""

    PENDING = "PENDING"
    REJECTED_FILE = "REJECTED_FILE"
    REJECTED_DUPLICATE = "REJECTED_DUPLICATE"
    REJECTED_AI = "REJECTED_AI"
    VALID_COUNTED = "VALID_COUNTED"
    VALID_EXTRA = "VALID_EXTRA"
    AI_ERROR = "AI_ERROR"
    PROCESSING_ERROR = "PROCESSING_ERROR"


#: 已进入最终状态、可作为重复判定依据的状态。
FINAL_STATUSES: frozenset[str] = frozenset(
    {
        SubmissionStatus.VALID_COUNTED.value,
        SubmissionStatus.VALID_EXTRA.value,
        SubmissionStatus.REJECTED_AI.value,
        SubmissionStatus.REJECTED_DUPLICATE.value,
    }
)

#: 技术性失败/未完成状态，不阻塞同一文件重试。
RETRYABLE_STATUSES: frozenset[str] = frozenset(
    {
        SubmissionStatus.AI_ERROR.value,
        SubmissionStatus.PENDING.value,
        SubmissionStatus.PROCESSING_ERROR.value,
        SubmissionStatus.REJECTED_FILE.value,
    }
)


class DuplicateType(str, Enum):
    NONE = "none"
    EXACT = "exact"
    TEXT = "text"
    HIGH_SIMILARITY = "high_similarity"
    CROSS_USER = "cross_user"


class DuplicateOutcome(str, Enum):
    """重复检测的结论。"""

    OK = "ok"
    HARD = "hard"  # 硬重复，直接拒绝
    SUSPICIOUS = "suspicious"  # 可疑，交给 AI 进一步判断


class AIDecision(str, Enum):
    PASS = "pass"
    FAIL = "fail"


class AIProgress(str, Enum):
    SUBSTANTIAL = "substantial"
    SOME = "some"
    NONE = "none"


class DuplicateAssessment(str, Enum):
    NEW = "new"
    ITERATIVE = "iterative"
    DUPLICATE = "duplicate"
    UNCERTAIN = "uncertain"


class UserStatus(str, Enum):
    ACTIVE = "active"
    DISABLED = "disabled"


class PauseScope(str, Enum):
    DAY = "day"
    WEEK = "week"


class CheckinOutcome(str, Enum):
    """打卡处理的业务结论，用于选择回复模板。"""

    COUNTED = "counted"
    EXTRA = "extra"
    REJECTED_AI = "rejected_ai"
    DUPLICATE = "duplicate"
    AI_ERROR = "ai_error"
    FILE_ERROR = "file_error"
    PAUSED = "paused"
    ALREADY_PROCESSED = "already_processed"


class ErrorCode:
    """错误码。对用户只发送友好文本，错误码写入日志/数据库。"""

    NOT_BOUND = "E_NOT_BOUND"
    ALREADY_BOUND = "E_ALREADY_BOUND"
    STUDENT_ID_CONFLICT = "E_STUDENT_ID_CONFLICT"
    PERMISSION_DENIED = "E_PERMISSION_DENIED"
    PAUSED_DAY = "E_PAUSED_DAY"
    PAUSED_WEEK = "E_PAUSED_WEEK"
    NO_REPLY = "E_NO_REPLY"
    NO_WORD_FILE = "E_NO_WORD_FILE"
    UNSUPPORTED_WORD_FORMAT = "E_UNSUPPORTED_WORD_FORMAT"
    FILE_TOO_LARGE = "E_FILE_TOO_LARGE"
    FILE_DOWNLOAD_FAILED = "E_FILE_DOWNLOAD_FAILED"
    NAS_WRITE_FAILED = "E_NAS_WRITE_FAILED"
    EXACT_DUPLICATE = "E_EXACT_DUPLICATE"
    DOCX_PARSE_FAILED = "E_DOCX_PARSE_FAILED"
    AI_TIMEOUT = "E_AI_TIMEOUT"
    AI_BAD_RESPONSE = "E_AI_BAD_RESPONSE"
    DB_ERROR = "E_DB_ERROR"
    USER_NOT_FOUND = "E_USER_NOT_FOUND"
    ADMIN_LAST_ONE = "E_ADMIN_LAST_ONE"
    INVALID_ARGUMENT = "E_INVALID_ARGUMENT"
    ALREADY_PROCESSED = "E_ALREADY_PROCESSED"
