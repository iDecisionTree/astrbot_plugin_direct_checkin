"""审计日志表访问。"""

from __future__ import annotations

import sqlite3

from ..models.entities import AuditEntry
from .db import Database


class AuditRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def write(
        self,
        actor_qq: str,
        actor_role: str,
        action: str,
        now: str,
        target_type: str = "",
        target_id: str = "",
        request_message_id: str = "",
        result: str = "",
        details_json: str | None = None,
    ) -> None:
        def work(conn: sqlite3.Connection) -> None:
            conn.execute(
                """
                INSERT INTO audit_log
                    (actor_qq, actor_role, action, target_type, target_id,
                     request_message_id, result, details_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    actor_qq,
                    actor_role,
                    action,
                    target_type,
                    target_id,
                    request_message_id,
                    result,
                    details_json,
                    now,
                ),
            )

        await self.db.run(work)

    async def list_entries(self) -> list[AuditEntry]:
        def work(conn: sqlite3.Connection) -> list[AuditEntry]:
            rows = conn.execute("SELECT * FROM audit_log ORDER BY id ASC").fetchall()
            return [item for item in (AuditEntry.from_row(row) for row in rows) if item]

        return await self.db.fetch(work)
