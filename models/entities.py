"""数据库行对应的实体对象。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _data(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return row
    return dict(row)


@dataclass(slots=True)
class User:
    id: int
    qq_id: str
    student_id: str
    name: str
    direction: str | None = None
    status: str = "active"
    bound_at: str = ""
    updated_at: str = ""
    last_submission_at: str | None = None
    created_from_group: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> User | None:
        d = _data(row)
        if not d:
            return None
        return cls(
            id=int(d["id"]),
            qq_id=str(d["qq_id"]),
            student_id=str(d["student_id"]),
            name=str(d["name"]),
            direction=d.get("direction"),
            status=str(d.get("status") or "active"),
            bound_at=str(d.get("bound_at") or ""),
            updated_at=str(d.get("updated_at") or ""),
            last_submission_at=d.get("last_submission_at"),
            created_from_group=d.get("created_from_group"),
        )


@dataclass(slots=True)
class Admin:
    qq_id: str
    added_by: str = ""
    added_at: str = ""

    @classmethod
    def from_row(cls, row: Any) -> Admin | None:
        d = _data(row)
        if not d:
            return None
        return cls(
            qq_id=str(d["qq_id"]),
            added_by=str(d.get("added_by") or ""),
            added_at=str(d.get("added_at") or ""),
        )


@dataclass(slots=True)
class Submission:
    id: str
    user_id: int
    qq_id_snapshot: str = ""
    student_id_snapshot: str = ""
    name_snapshot: str = ""
    submitted_at: str = ""
    week_key: str = ""
    beijing_date: str | None = None
    qq_group_id: str | None = None
    qq_message_id: str = ""
    quoted_message_id: str = ""
    original_filename: str = ""
    stored_path: str = ""
    file_size: int = 0
    sha256: str = ""
    normalized_text_sha256: str | None = None
    extracted_char_count: int = 0
    table_count: int = 0
    image_count: int = 0
    link_count: int = 0
    duplicate_type: str = "none"
    duplicate_of: str | None = None
    similarity_score: float | None = None
    status: str = "PENDING"
    counted: int = 0
    counted_slot: int | None = None
    ai_decision: str | None = None
    ai_progress: str | None = None
    ai_confidence: float | None = None
    ai_feedback: str | None = None
    ai_result_json: str | None = None
    ai_provider_id: str | None = None
    ai_latency_ms: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_row(cls, row: Any) -> Submission | None:
        d = _data(row)
        if not d:
            return None

        def _int(key: str) -> int:
            value = d.get(key)
            return int(value) if value is not None else 0

        def _float(key: str) -> float | None:
            value = d.get(key)
            return float(value) if value is not None else None

        return cls(
            id=str(d["id"]),
            user_id=int(d["user_id"]),
            qq_id_snapshot=str(d.get("qq_id_snapshot") or ""),
            student_id_snapshot=str(d.get("student_id_snapshot") or ""),
            name_snapshot=str(d.get("name_snapshot") or ""),
            submitted_at=str(d.get("submitted_at") or ""),
            week_key=str(d.get("week_key") or ""),
            beijing_date=d.get("beijing_date"),
            qq_group_id=d.get("qq_group_id"),
            qq_message_id=str(d.get("qq_message_id") or ""),
            quoted_message_id=str(d.get("quoted_message_id") or ""),
            original_filename=str(d.get("original_filename") or ""),
            stored_path=str(d.get("stored_path") or ""),
            file_size=_int("file_size"),
            sha256=str(d.get("sha256") or ""),
            normalized_text_sha256=d.get("normalized_text_sha256"),
            extracted_char_count=_int("extracted_char_count"),
            table_count=_int("table_count"),
            image_count=_int("image_count"),
            link_count=_int("link_count"),
            duplicate_type=str(d.get("duplicate_type") or "none"),
            duplicate_of=d.get("duplicate_of"),
            similarity_score=_float("similarity_score"),
            status=str(d.get("status") or "PENDING"),
            counted=_int("counted"),
            counted_slot=(int(d["counted_slot"]) if d.get("counted_slot") is not None else None),
            ai_decision=d.get("ai_decision"),
            ai_progress=d.get("ai_progress"),
            ai_confidence=_float("ai_confidence"),
            ai_feedback=d.get("ai_feedback"),
            ai_result_json=d.get("ai_result_json"),
            ai_provider_id=d.get("ai_provider_id"),
            ai_latency_ms=(int(d["ai_latency_ms"]) if d.get("ai_latency_ms") is not None else None),
            error_code=d.get("error_code"),
            error_message=d.get("error_message"),
            created_at=str(d.get("created_at") or ""),
            updated_at=str(d.get("updated_at") or ""),
        )


@dataclass(slots=True)
class CountAdjustment:
    id: int
    user_id: int
    week_key: str
    delta: int
    admin_qq: str
    reason: str | None = None
    created_at: str = ""
    beijing_date: str | None = None
    counted: int = 0
    counted_slot: int | None = None

    @classmethod
    def from_row(cls, row: Any) -> CountAdjustment | None:
        d = _data(row)
        if not d:
            return None
        return cls(
            id=int(d["id"]),
            user_id=int(d["user_id"]),
            week_key=str(d.get("week_key") or ""),
            delta=int(d.get("delta") or 0),
            admin_qq=str(d.get("admin_qq") or ""),
            reason=d.get("reason"),
            created_at=str(d.get("created_at") or ""),
            beijing_date=d.get("beijing_date"),
            counted=int(d.get("counted") or 0),
            counted_slot=(int(d["counted_slot"]) if d.get("counted_slot") is not None else None),
        )


@dataclass(slots=True)
class PausePeriod:
    id: int
    scope: str
    start_at: str
    scheduled_end_at: str
    resumed_at: str | None = None
    reason: str = ""
    created_by_qq: str = ""
    created_at: str = ""
    exemption_week_key: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> PausePeriod | None:
        d = _data(row)
        if not d:
            return None
        return cls(
            id=int(d["id"]),
            scope=str(d.get("scope") or ""),
            start_at=str(d.get("start_at") or ""),
            scheduled_end_at=str(d.get("scheduled_end_at") or ""),
            resumed_at=d.get("resumed_at"),
            reason=str(d.get("reason") or ""),
            created_by_qq=str(d.get("created_by_qq") or ""),
            created_at=str(d.get("created_at") or ""),
            exemption_week_key=d.get("exemption_week_key"),
        )


@dataclass(slots=True)
class AuditEntry:
    id: int
    actor_qq: str
    actor_role: str
    action: str
    target_type: str = ""
    target_id: str = ""
    request_message_id: str = ""
    result: str = ""
    details_json: str | None = None
    created_at: str = ""

    @classmethod
    def from_row(cls, row: Any) -> AuditEntry | None:
        d = _data(row)
        if not d:
            return None
        return cls(
            id=int(d["id"]),
            actor_qq=str(d.get("actor_qq") or ""),
            actor_role=str(d.get("actor_role") or ""),
            action=str(d.get("action") or ""),
            target_type=str(d.get("target_type") or ""),
            target_id=str(d.get("target_id") or ""),
            request_message_id=str(d.get("request_message_id") or ""),
            result=str(d.get("result") or ""),
            details_json=d.get("details_json"),
            created_at=str(d.get("created_at") or ""),
        )
