"""管理员与人工计次修正表访问。"""

from __future__ import annotations

import sqlite3

from ..models.entities import Admin, CountAdjustment
from .db import Database


class AdminRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def is_admin(self, qq_id: str) -> bool:
        def work(conn: sqlite3.Connection) -> bool:
            row = conn.execute("SELECT 1 FROM admins WHERE qq_id = ?", (qq_id,)).fetchone()
            return row is not None

        return await self.db.fetch(work)

    async def count(self) -> int:
        def work(conn: sqlite3.Connection) -> int:
            row = conn.execute("SELECT COUNT(*) AS c FROM admins").fetchone()
            return int(row["c"]) if row else 0

        return await self.db.fetch(work)

    async def add(self, qq_id: str, added_by: str, now: str) -> bool:
        def work(conn: sqlite3.Connection) -> bool:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO admins (qq_id, added_by, added_at) VALUES (?, ?, ?)",
                (qq_id, added_by, now),
            )
            return cursor.rowcount > 0

        return await self.db.run(work)

    async def remove(self, qq_id: str) -> bool:
        def work(conn: sqlite3.Connection) -> bool:
            cursor = conn.execute("DELETE FROM admins WHERE qq_id = ?", (qq_id,))
            return cursor.rowcount > 0

        return await self.db.run(work)

    async def list_admins(self) -> list[Admin]:
        def work(conn: sqlite3.Connection) -> list[Admin]:
            rows = conn.execute("SELECT * FROM admins ORDER BY added_at ASC").fetchall()
            return [admin for admin in (Admin.from_row(row) for row in rows) if admin]

        return await self.db.fetch(work)

    async def add_adjustment(
        self,
        user_id: int,
        week_key: str,
        delta: int,
        admin_qq: str,
        now: str,
        beijing_date: str,
        reason: str | None = None,
        counted: int = 0,
        counted_slot: int | None = None,
    ) -> CountAdjustment:
        def work(conn: sqlite3.Connection) -> CountAdjustment:
            cursor = conn.execute(
                """
                INSERT INTO count_adjustments
                    (user_id, week_key, delta, admin_qq, reason, created_at,
                     beijing_date, counted, counted_slot)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    week_key,
                    delta,
                    admin_qq,
                    reason,
                    now,
                    beijing_date,
                    counted,
                    counted_slot,
                ),
            )
            row = conn.execute(
                "SELECT * FROM count_adjustments WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
            return CountAdjustment.from_row(row)

        return await self.db.run(work)

    async def allocate_positive_adjustment(
        self,
        user_id: int,
        week_key: str,
        beijing_date: str,
        weekly_limit: int,
        admin_qq: str,
        now: str,
        reason: str | None = None,
    ) -> tuple[CountAdjustment, bool, int]:
        """人工 +1：与自动打卡共享每周槽位。

        本周仍有空槽则 ``counted=1``，否则记为额外。返回
        (adjustment, counted, 本周有效计次)。
        """

        from .submission_repo import (
            _count_counted_adjustments,
            _count_counted_submissions,
            _count_manual_negatives,
            next_sequence,
        )

        def work(conn: sqlite3.Connection) -> tuple[CountAdjustment, bool, int]:
            auto_counted = _count_counted_submissions(conn, user_id, week_key)
            manual_counted = _count_counted_adjustments(conn, user_id, week_key)
            manual_negatives = _count_manual_negatives(conn, user_id, week_key)
            current = max(0, auto_counted + manual_counted - manual_negatives)
            if current < weekly_limit:
                counted = 1
                slot: int | None = next_sequence(conn, user_id, week_key)
                total = current + 1
            else:
                counted = 0
                slot = None
                total = current
            cursor = conn.execute(
                """
                INSERT INTO count_adjustments
                    (user_id, week_key, delta, admin_qq, reason, created_at,
                     beijing_date, counted, counted_slot)
                VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    week_key,
                    admin_qq,
                    reason,
                    now,
                    beijing_date,
                    counted,
                    slot,
                ),
            )
            row = conn.execute(
                "SELECT * FROM count_adjustments WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
            return CountAdjustment.from_row(row), bool(counted), total

        return await self.db.transaction(work, immediate=True)

    async def deduct(
        self, user_id: int, week_key: str, date: str, limit: int, actor: str, now: str
    ) -> int | None:
        from .submission_repo import (
            _count_counted_adjustments,
            _count_counted_submissions,
            _count_manual_negatives,
        )

        def work(conn):
            current = max(
                0,
                _count_counted_submissions(conn, user_id, week_key)
                + _count_counted_adjustments(conn, user_id, week_key)
                - _count_manual_negatives(conn, user_id, week_key),
            )
            if current <= 0:
                return None
            conn.execute(
                "INSERT INTO count_adjustments (user_id,week_key,delta,admin_qq,created_at,beijing_date) VALUES (?,?,-1,?,?,?)",
                (user_id, week_key, actor, now, date),
            )
            return min(limit, current - 1)

        return await self.db.transaction(work)

    async def remove_protected(self, qq_id: str, super_admin: str) -> str:
        def work(conn):
            if not conn.execute("SELECT 1 FROM admins WHERE qq_id=?", (qq_id,)).fetchone():
                return "missing"
            if qq_id == super_admin:
                return "protected"
            if conn.execute("SELECT COUNT(*) FROM admins").fetchone()[0] <= 1:
                return "last"
            conn.execute("DELETE FROM admins WHERE qq_id=?", (qq_id,))
            return "removed"

        return await self.db.transaction(work)

    async def count_counted_in_week(self, user_id: int, week_key: str) -> int:
        def work(conn: sqlite3.Connection) -> int:
            row = conn.execute(
                """
                SELECT COUNT(*) AS c FROM count_adjustments
                WHERE user_id = ? AND week_key = ? AND counted = 1
                """,
                (user_id, week_key),
            ).fetchone()
            return int(row["c"]) if row else 0

        return await self.db.fetch(work)

    async def count_negatives_in_week(self, user_id: int, week_key: str) -> int:
        def work(conn: sqlite3.Connection) -> int:
            row = conn.execute(
                """
                SELECT COUNT(*) AS c FROM count_adjustments
                WHERE user_id = ? AND week_key = ? AND delta < 0
                """,
                (user_id, week_key),
            ).fetchone()
            return int(row["c"]) if row else 0

        return await self.db.fetch(work)

    async def sum_all_deltas(self, user_id: int) -> int:
        def work(conn: sqlite3.Connection) -> int:
            row = conn.execute(
                "SELECT COALESCE(SUM(delta), 0) AS total FROM count_adjustments WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            return int(row["total"]) if row else 0

        return await self.db.fetch(work)

    async def sum_adjustments(self, user_id: int, week_key: str) -> int:
        def work(conn: sqlite3.Connection) -> int:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(delta), 0) AS total
                FROM count_adjustments
                WHERE user_id = ? AND week_key = ?
                """,
                (user_id, week_key),
            ).fetchone()
            return int(row["total"]) if row else 0

        return await self.db.fetch(work)

    async def sum_adjustments_by_week(self, week_key: str) -> dict[int, int]:
        def work(conn: sqlite3.Connection) -> dict[int, int]:
            rows = conn.execute(
                """
                SELECT user_id, COALESCE(SUM(delta), 0) AS total
                FROM count_adjustments
                WHERE week_key = ?
                GROUP BY user_id
                """,
                (week_key,),
            ).fetchall()
            return {int(row["user_id"]): int(row["total"]) for row in rows}

        return await self.db.fetch(work)

    async def count_counted_by_week(self, week_key: str) -> dict[int, int]:
        def work(conn: sqlite3.Connection) -> dict[int, int]:
            rows = conn.execute(
                """
                SELECT user_id, COUNT(*) AS c FROM count_adjustments
                WHERE week_key = ? AND counted = 1
                GROUP BY user_id
                """,
                (week_key,),
            ).fetchall()
            return {int(row["user_id"]): int(row["c"]) for row in rows}

        return await self.db.fetch(work)

    async def count_negatives_by_week(self, week_key: str) -> dict[int, int]:
        def work(conn: sqlite3.Connection) -> dict[int, int]:
            rows = conn.execute(
                """
                SELECT user_id, COUNT(*) AS c FROM count_adjustments
                WHERE week_key = ? AND delta < 0
                GROUP BY user_id
                """,
                (week_key,),
            ).fetchall()
            return {int(row["user_id"]): int(row["c"]) for row in rows}

        return await self.db.fetch(work)

    async def list_adjustments(self, user_id: int | None = None) -> list[CountAdjustment]:
        def work(conn: sqlite3.Connection) -> list[CountAdjustment]:
            sql = "SELECT * FROM count_adjustments"
            params: tuple[object, ...] = ()
            if user_id is not None:
                sql += " WHERE user_id = ?"
                params = (user_id,)
            sql += " ORDER BY id ASC"
            rows = conn.execute(sql, params).fetchall()
            return [item for item in (CountAdjustment.from_row(row) for row in rows) if item]

        return await self.db.fetch(work)
