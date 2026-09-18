"""用户表访问。"""

from __future__ import annotations

import sqlite3

from ..models.entities import User
from .db import Database


class UserRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(
        self,
        qq_id: str,
        student_id: str,
        name: str,
        group_id: str | None,
        now: str,
    ) -> User:
        def work(conn: sqlite3.Connection) -> User:
            cursor = conn.execute(
                """
                INSERT INTO users
                    (qq_id, student_id, name, status, bound_at, updated_at, created_from_group)
                VALUES (?, ?, ?, 'active', ?, ?, ?)
                """,
                (qq_id, student_id, name, now, now, group_id),
            )
            row = conn.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return User.from_row(row)

        return await self.db.run(work)

    async def get_by_qq(self, qq_id: str) -> User | None:
        def work(conn: sqlite3.Connection) -> User | None:
            row = conn.execute("SELECT * FROM users WHERE qq_id = ?", (qq_id,)).fetchone()
            return User.from_row(row)

        return await self.db.fetch(work)

    async def get_by_student_id(self, student_id: str) -> User | None:
        def work(conn: sqlite3.Connection) -> User | None:
            row = conn.execute("SELECT * FROM users WHERE student_id = ?", (student_id,)).fetchone()
            return User.from_row(row)

        return await self.db.fetch(work)

    async def get_by_id(self, user_id: int) -> User | None:
        def work(conn: sqlite3.Connection) -> User | None:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            return User.from_row(row)

        return await self.db.fetch(work)

    async def resolve_identifier(self, value: str) -> tuple[User | None, bool]:
        """按 学号 -> QQ 解析目标用户。

        返回 (user, conflict)。当同一数字同时命中不同用户的学号与 QQ 时 conflict=True。
        """

        def work(conn: sqlite3.Connection) -> tuple[User | None, bool]:
            by_student = conn.execute(
                "SELECT * FROM users WHERE student_id = ?", (value,)
            ).fetchone()
            by_qq = conn.execute("SELECT * FROM users WHERE qq_id = ?", (value,)).fetchone()
            student_user = User.from_row(by_student)
            qq_user = User.from_row(by_qq)
            if student_user and qq_user and student_user.id != qq_user.id:
                return None, True
            return student_user or qq_user, False

        return await self.db.fetch(work)

    async def list_users(self, active_only: bool = False) -> list[User]:
        def work(conn: sqlite3.Connection) -> list[User]:
            sql = "SELECT * FROM users"
            if active_only:
                sql += " WHERE status = 'active'"
            sql += " ORDER BY student_id ASC"
            rows = conn.execute(sql).fetchall()
            return [user for user in (User.from_row(row) for row in rows) if user]

        return await self.db.fetch(work)

    async def touch_last_submission(self, user_id: int, now: str) -> None:
        def work(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE users SET last_submission_at = ?, updated_at = ? WHERE id = ?",
                (now, now, user_id),
            )

        await self.db.run(work)
