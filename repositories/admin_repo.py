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
        reason: str | None = None,
    ) -> CountAdjustment:
        def work(conn: sqlite3.Connection) -> CountAdjustment:
            cursor = conn.execute(
                """
                INSERT INTO count_adjustments
                    (user_id, week_key, delta, admin_qq, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, week_key, delta, admin_qq, reason, now),
            )
            row = conn.execute(
                "SELECT * FROM count_adjustments WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
            return CountAdjustment.from_row(row)

        return await self.db.run(work)

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
