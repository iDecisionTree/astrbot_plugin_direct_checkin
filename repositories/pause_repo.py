"""暂停记录表访问。"""

from __future__ import annotations

import sqlite3

from ..models.entities import PausePeriod
from .db import Database


class PauseRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(
        self,
        scope: str,
        start_at: str,
        scheduled_end_at: str,
        reason: str,
        created_by_qq: str,
        now: str,
        exemption_week_key: str | None = None,
    ) -> PausePeriod:
        def work(conn: sqlite3.Connection) -> PausePeriod:
            cursor = conn.execute(
                """
                INSERT INTO pause_periods
                    (scope, start_at, scheduled_end_at, reason, created_by_qq, created_at, exemption_week_key)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scope,
                    start_at,
                    scheduled_end_at,
                    reason,
                    created_by_qq,
                    now,
                    exemption_week_key,
                ),
            )
            row = conn.execute(
                "SELECT * FROM pause_periods WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
            return PausePeriod.from_row(row)

        return await self.db.run(work)

    async def active(self, now: str) -> PausePeriod | None:
        def work(conn: sqlite3.Connection) -> PausePeriod | None:
            row = conn.execute(
                """
                SELECT * FROM pause_periods
                WHERE resumed_at IS NULL
                  AND start_at <= ?
                  AND scheduled_end_at >= ?
                ORDER BY start_at DESC, id DESC
                LIMIT 1
                """,
                (now, now),
            ).fetchone()
            return PausePeriod.from_row(row)

        return await self.db.fetch(work)

    async def resume_active(self, now: str) -> int:
        def work(conn: sqlite3.Connection) -> int:
            cursor = conn.execute(
                """
                UPDATE pause_periods
                SET resumed_at = ?
                WHERE resumed_at IS NULL
                  AND start_at <= ?
                  AND scheduled_end_at >= ?
                """,
                (now, now, now),
            )
            return cursor.rowcount

        return await self.db.run(work)

    async def exempted_week(self, week_key: str) -> PausePeriod | None:
        """返回该周仍处于生效中的暂停周记录。

        `/d resume` 会写入 ``resumed_at``，恢复后不再豁免，本周重新要求打卡。
        """

        def work(conn: sqlite3.Connection) -> PausePeriod | None:
            row = conn.execute(
                """
                SELECT * FROM pause_periods
                WHERE exemption_week_key = ?
                  AND resumed_at IS NULL
                ORDER BY id DESC
                LIMIT 1
                """,
                (week_key,),
            ).fetchone()
            return PausePeriod.from_row(row)

        return await self.db.fetch(work)

    async def list_pauses(self) -> list[PausePeriod]:
        def work(conn: sqlite3.Connection) -> list[PausePeriod]:
            rows = conn.execute("SELECT * FROM pause_periods ORDER BY id ASC").fetchall()
            return [item for item in (PausePeriod.from_row(row) for row in rows) if item]

        return await self.db.fetch(work)
