"""提交记录表访问。"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..models.entities import Submission
from ..models.enums import SubmissionStatus
from .db import Database

_UPDATABLE_COLUMNS = frozenset(
    {
        "normalized_text_sha256",
        "extracted_char_count",
        "table_count",
        "image_count",
        "link_count",
        "duplicate_type",
        "duplicate_of",
        "similarity_score",
        "status",
        "counted",
        "counted_slot",
        "ai_decision",
        "ai_progress",
        "ai_confidence",
        "ai_feedback",
        "ai_result_json",
        "ai_provider_id",
        "ai_latency_ms",
        "error_code",
        "error_message",
        "updated_at",
    }
)


def _build_update(conn: sqlite3.Connection, submission_id: str, fields: dict[str, Any]) -> None:
    if not fields:
        return
    invalid = set(fields) - _UPDATABLE_COLUMNS
    if invalid:
        raise ValueError(f"unsupported submission columns: {invalid}")
    assignments = ", ".join(f"{key} = ?" for key in fields)
    values = [*fields.values(), submission_id]
    conn.execute(f"UPDATE submissions SET {assignments} WHERE id = ?", values)


class SubmissionRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create_pending(
        self,
        submission_id: str,
        user_id: int,
        qq_id: str,
        student_id: str,
        name: str,
        submitted_at: str,
        week_key: str,
        qq_group_id: str | None,
        qq_message_id: str,
        quoted_message_id: str,
        original_filename: str,
        stored_path: str,
        file_size: int,
        sha256: str,
        now: str,
    ) -> Submission:
        def work(conn: sqlite3.Connection) -> Submission:
            conn.execute(
                """
                INSERT INTO submissions (
                    id, user_id, qq_id_snapshot, student_id_snapshot, name_snapshot,
                    submitted_at, week_key, qq_group_id, qq_message_id, quoted_message_id,
                    original_filename, stored_path, file_size, sha256,
                    status, counted, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    submission_id,
                    user_id,
                    qq_id,
                    student_id,
                    name,
                    submitted_at,
                    week_key,
                    qq_group_id,
                    qq_message_id,
                    quoted_message_id,
                    original_filename,
                    stored_path,
                    file_size,
                    sha256,
                    SubmissionStatus.PENDING.value,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM submissions WHERE id = ?", (submission_id,)
            ).fetchone()
            return Submission.from_row(row)

        return await self.db.run(work)

    async def get_by_id(self, submission_id: str) -> Submission | None:
        def work(conn: sqlite3.Connection) -> Submission | None:
            row = conn.execute(
                "SELECT * FROM submissions WHERE id = ?", (submission_id,)
            ).fetchone()
            return Submission.from_row(row)

        return await self.db.fetch(work)

    async def get_by_message_id(self, message_id: str) -> Submission | None:
        def work(conn: sqlite3.Connection) -> Submission | None:
            row = conn.execute(
                "SELECT * FROM submissions WHERE qq_message_id = ?", (message_id,)
            ).fetchone()
            return Submission.from_row(row)

        return await self.db.fetch(work)

    async def update(self, submission_id: str, **fields: Any) -> None:
        def work(conn: sqlite3.Connection) -> None:
            _build_update(conn, submission_id, fields)

        await self.db.run(work)

    async def finalize_counted(
        self,
        submission_id: str,
        user_id: int,
        week_key: str,
        weekly_limit: int,
        fields: dict[str, Any],
    ) -> tuple[str, int | None, int]:
        """在事务中确定计分槽并写入最终状态。

        返回 (status, counted_slot, 本周自动计分总数)。
        """

        def work(conn: sqlite3.Connection) -> tuple[str, int | None, int]:
            row = conn.execute(
                """
                SELECT COUNT(*) AS c FROM submissions
                WHERE user_id = ? AND week_key = ? AND status = ?
                """,
                (user_id, week_key, SubmissionStatus.VALID_COUNTED.value),
            ).fetchone()
            counted = int(row["c"]) if row else 0
            if counted < weekly_limit:
                status = SubmissionStatus.VALID_COUNTED.value
                slot: int | None = counted + 1
                counted_flag = 1
                total = counted + 1
            else:
                status = SubmissionStatus.VALID_EXTRA.value
                slot = None
                counted_flag = 0
                total = counted
            payload = dict(fields)
            payload.update(
                {
                    "status": status,
                    "counted": counted_flag,
                    "counted_slot": slot,
                }
            )
            _build_update(conn, submission_id, payload)
            return status, slot, total

        # immediate=True 防止同用户并发写覆盖计分槽。
        return await self.db.transaction(work, immediate=True)

    async def find_final_by_sha(
        self, user_id: int, sha256: str, final_statuses: set[str]
    ) -> Submission | None:
        if not sha256:
            return None
        placeholders = ", ".join("?" for _ in final_statuses)
        params = [user_id, sha256, *final_statuses]

        def work(conn: sqlite3.Connection) -> Submission | None:
            row = conn.execute(
                f"""
                SELECT * FROM submissions
                WHERE user_id = ? AND sha256 = ? AND status IN ({placeholders})
                ORDER BY submitted_at DESC LIMIT 1
                """,
                params,
            ).fetchone()
            return Submission.from_row(row)

        return await self.db.fetch(work)

    async def find_final_by_text_hash(
        self, user_id: int, text_hash: str, final_statuses: set[str]
    ) -> Submission | None:
        if not text_hash:
            return None
        placeholders = ", ".join("?" for _ in final_statuses)
        params = [user_id, text_hash, *final_statuses]

        def work(conn: sqlite3.Connection) -> Submission | None:
            row = conn.execute(
                f"""
                SELECT * FROM submissions
                WHERE user_id = ? AND normalized_text_sha256 = ?
                  AND status IN ({placeholders})
                ORDER BY submitted_at DESC LIMIT 1
                """,
                params,
            ).fetchone()
            return Submission.from_row(row)

        return await self.db.fetch(work)

    async def find_cross_user_by_sha(
        self, sha256: str, exclude_user_id: int, final_statuses: set[str]
    ) -> Submission | None:
        if not sha256:
            return None
        placeholders = ", ".join("?" for _ in final_statuses)
        params = [sha256, exclude_user_id, *final_statuses]

        def work(conn: sqlite3.Connection) -> Submission | None:
            row = conn.execute(
                f"""
                SELECT * FROM submissions
                WHERE sha256 = ? AND user_id != ? AND status IN ({placeholders})
                ORDER BY submitted_at DESC LIMIT 1
                """,
                params,
            ).fetchone()
            return Submission.from_row(row)

        return await self.db.fetch(work)

    async def recent_final_submissions(
        self, user_id: int, since: str, final_statuses: set[str], limit: int
    ) -> list[Submission]:
        placeholders = ", ".join("?" for _ in final_statuses)
        params = [user_id, since, *final_statuses, limit]

        def work(conn: sqlite3.Connection) -> list[Submission]:
            rows = conn.execute(
                f"""
                SELECT * FROM submissions
                WHERE user_id = ? AND submitted_at >= ? AND status IN ({placeholders})
                ORDER BY submitted_at DESC LIMIT ?
                """,
                params,
            ).fetchall()
            return [item for item in (Submission.from_row(row) for row in rows) if item]

        return await self.db.fetch(work)

    async def count_auto_counted(self, user_id: int, week_key: str) -> int:
        def work(conn: sqlite3.Connection) -> int:
            row = conn.execute(
                """
                SELECT COUNT(*) AS c FROM submissions
                WHERE user_id = ? AND week_key = ? AND status = ?
                """,
                (user_id, week_key, SubmissionStatus.VALID_COUNTED.value),
            ).fetchone()
            return int(row["c"]) if row else 0

        return await self.db.fetch(work)

    async def count_auto_counted_by_week(self, week_key: str) -> dict[int, int]:
        def work(conn: sqlite3.Connection) -> dict[int, int]:
            rows = conn.execute(
                """
                SELECT user_id, COUNT(*) AS c FROM submissions
                WHERE week_key = ? AND status = ?
                GROUP BY user_id
                """,
                (week_key, SubmissionStatus.VALID_COUNTED.value),
            ).fetchall()
            return {int(row["user_id"]): int(row["c"]) for row in rows}

        return await self.db.fetch(work)

    async def list_by_status(self, status: str) -> list[Submission]:
        def work(conn: sqlite3.Connection) -> list[Submission]:
            rows = conn.execute(
                "SELECT * FROM submissions WHERE status = ? ORDER BY submitted_at ASC",
                (status,),
            ).fetchall()
            return [item for item in (Submission.from_row(row) for row in rows) if item]

        return await self.db.fetch(work)

    async def list_submissions(self, user_id: int | None = None) -> list[Submission]:
        def work(conn: sqlite3.Connection) -> list[Submission]:
            sql = "SELECT * FROM submissions"
            params: tuple[Any, ...] = ()
            if user_id is not None:
                sql += " WHERE user_id = ?"
                params = (user_id,)
            sql += " ORDER BY submitted_at ASC"
            rows = conn.execute(sql, params).fetchall()
            return [item for item in (Submission.from_row(row) for row in rows) if item]

        return await self.db.fetch(work)
