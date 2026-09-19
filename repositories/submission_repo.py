"""提交记录表访问。"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..models.entities import Submission
from ..models.enums import ErrorCode, SubmissionStatus
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


def _count_counted_submissions(conn: sqlite3.Connection, user_id: int, week_key: str) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS c FROM submissions
        WHERE user_id = ? AND week_key = ? AND status = ?
        """,
        (user_id, week_key, SubmissionStatus.VALID_COUNTED.value),
    ).fetchone()
    return int(row["c"]) if row else 0


def _count_counted_adjustments(conn: sqlite3.Connection, user_id: int, week_key: str) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS c FROM count_adjustments
        WHERE user_id = ? AND week_key = ? AND counted = 1
        """,
        (user_id, week_key),
    ).fetchone()
    return int(row["c"]) if row else 0


def _count_manual_negatives(conn: sqlite3.Connection, user_id: int, week_key: str) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS c FROM count_adjustments
        WHERE user_id = ? AND week_key = ? AND delta < 0
        """,
        (user_id, week_key),
    ).fetchone()
    return int(row["c"]) if row else 0


def _count_counted_submissions_on_date(
    conn: sqlite3.Connection, user_id: int, beijing_date: str
) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS c FROM submissions
        WHERE user_id = ? AND beijing_date = ? AND counted = 1
        """,
        (user_id, beijing_date),
    ).fetchone()
    return int(row["c"]) if row else 0


def _has_counted_on_date(conn: sqlite3.Connection, user_id: int, beijing_date: str) -> bool:
    return _count_counted_submissions_on_date(conn, user_id, beijing_date) > 0


def next_sequence(conn: sqlite3.Connection, user_id: int, week_key: str) -> int:
    row = conn.execute(
        """SELECT MAX(counted_slot) AS seq FROM (
        SELECT counted_slot FROM submissions WHERE user_id=? AND week_key=?
        UNION ALL SELECT counted_slot FROM count_adjustments WHERE user_id=? AND week_key=?)""",
        (user_id, week_key, user_id, week_key),
    ).fetchone()
    return int(row["seq"] or 0) + 1


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
        beijing_date: str,
        qq_group_id: str | None,
        qq_message_id: str,
        quoted_message_id: str,
        original_filename: str,
        stored_path: str,
        file_size: int,
        sha256: str,
        now: str,
        event_key: str | None = None,
    ) -> Submission:
        def work(conn: sqlite3.Connection) -> Submission:
            conn.execute(
                """
                INSERT INTO submissions (
                    id, user_id, qq_id_snapshot, student_id_snapshot, name_snapshot,
                    submitted_at, week_key, beijing_date, qq_group_id, qq_message_id,
                    quoted_message_id, original_filename, stored_path, file_size, sha256,
                    status, counted, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    submission_id,
                    user_id,
                    qq_id,
                    student_id,
                    name,
                    submitted_at,
                    week_key,
                    beijing_date,
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
            if event_key:
                conn.execute(
                    "UPDATE submissions SET event_key=? WHERE id=?", (event_key, submission_id)
                )
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

    async def daily_participant_position(self, submission_id: str) -> int | None:
        """全队当天首次材料入库的用户序号，不是审核通过排名。

        用 SQLite 插入顺序而非审核完成时间排序；同一用户重试或追加材料
        沿用当日首次序号。一个查询取得一致快照，不做有竞争的 count + 1。
        """

        def work(conn):
            row = conn.execute(
                """WITH first_visits AS (
                    SELECT user_id, MIN(rowid) AS first_row
                    FROM submissions
                    WHERE beijing_date=(SELECT beijing_date FROM submissions WHERE id=?)
                    GROUP BY user_id
                )
                SELECT COUNT(*) FROM first_visits WHERE first_row <= (
                    SELECT first_row FROM first_visits
                    WHERE user_id=(SELECT user_id FROM submissions WHERE id=?)
                )""",
                (submission_id, submission_id),
            ).fetchone()
            return int(row[0]) or None

        return await self.db.fetch(work)

    async def get_by_event(
        self, event_key: str, message_id: str, user_id: int, group_id: str | None
    ) -> Submission | None:
        def work(conn):
            row = conn.execute(
                """SELECT * FROM submissions WHERE event_key=? OR
                (event_key IS NULL AND qq_message_id=? AND user_id=? AND COALESCE(qq_group_id,'')=?)
                ORDER BY submitted_at DESC LIMIT 1""",
                (event_key, message_id, user_id, group_id or ""),
            ).fetchone()
            return Submission.from_row(row)

        return await self.db.fetch(work)

    async def update(self, submission_id: str, **fields: Any) -> None:
        def work(conn: sqlite3.Connection) -> None:
            _build_update(conn, submission_id, fields)

        await self.db.run(work)

    async def fail_pending(self, submission_id: str, now: str) -> str | None:
        """只将未定稿记录标为技术失败，不覆盖已提交的计次。"""

        def work(conn):
            conn.execute(
                "UPDATE submissions SET status='PROCESSING_ERROR', error_code=?, "
                "error_message='处理异常', updated_at=? WHERE id=? AND status='PENDING'",
                (ErrorCode.DB_ERROR, now, submission_id),
            )
            row = conn.execute(
                "SELECT status FROM submissions WHERE id=?", (submission_id,)
            ).fetchone()
            return row[0] if row else None

        return await self.db.transaction(work)

    async def finalize_counted(
        self,
        submission_id: str,
        user_id: int,
        week_key: str,
        beijing_date: str,
        weekly_limit: int,
        fields: dict[str, Any],
    ) -> tuple[str, int | None, int, str]:
        """在事务中确定计分槽并写入最终状态。

        计次规则（自动打卡）：
        - 与人工 +1 共享每周 ``weekly_limit`` 个计分槽；
        - 同一北京自然日最多计一次，当天第二次及以上有效打卡记为额外；
        - 人工 -1 降低当前净次数，释放本周额度，但不复用历史序号。

        返回 (status, counted_slot, 本周有效计次, reason)，
        reason 取值：``counted`` / ``same_day`` / ``week_full``。
        """

        def work(conn: sqlite3.Connection) -> tuple[str, int | None, int, str]:
            existing = conn.execute(
                "SELECT status FROM submissions WHERE id=? AND user_id=? AND week_key=?",
                (submission_id, user_id, week_key),
            ).fetchone()
            if existing is None or existing["status"] != SubmissionStatus.PENDING.value:
                raise ValueError("只能定稿存在且仍待处理的提交")
            auto_counted = _count_counted_submissions(conn, user_id, week_key)
            manual_counted = _count_counted_adjustments(conn, user_id, week_key)
            manual_negatives = _count_manual_negatives(conn, user_id, week_key)
            current = max(0, auto_counted + manual_counted - manual_negatives)
            same_day = _has_counted_on_date(conn, user_id, beijing_date)

            if same_day:
                status = SubmissionStatus.VALID_EXTRA.value
                slot: int | None = None
                counted_flag = 0
                total = current
                reason = "same_day"
            elif current < weekly_limit:
                status = SubmissionStatus.VALID_COUNTED.value
                slot = next_sequence(conn, user_id, week_key)
                counted_flag = 1
                total = current + 1
                reason = "counted"
            else:
                status = SubmissionStatus.VALID_EXTRA.value
                slot = None
                counted_flag = 0
                total = current
                reason = "week_full"

            payload = dict(fields)
            payload.update(
                {
                    "status": status,
                    "counted": counted_flag,
                    "counted_slot": slot,
                }
            )
            _build_update(conn, submission_id, payload)
            conn.execute(
                "UPDATE users SET last_submission_at=MAX(COALESCE(last_submission_at,''), "
                "(SELECT submitted_at FROM submissions WHERE id=?)), updated_at=? WHERE id=?",
                (submission_id, fields.get("updated_at", ""), user_id),
            )
            return status, slot, total, reason

        # immediate=True 防止同用户并发写覆盖计分槽。
        return await self.db.transaction(work, immediate=True)

    async def count_counted_on_date(self, user_id: int, beijing_date: str) -> int:
        def work(conn: sqlite3.Connection) -> int:
            return _count_counted_submissions_on_date(conn, user_id, beijing_date)

        return await self.db.fetch(work)

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
