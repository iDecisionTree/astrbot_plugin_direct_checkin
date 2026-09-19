"""SQLite 访问层。

使用标准库 sqlite3，并通过 ``asyncio.to_thread`` 避免阻塞事件循环。
每次操作使用独立短连接，事务通过 ``transaction()`` 保证。
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from contextvars import ContextVar
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")
_command_connection: ContextVar[sqlite3.Connection | None] = ContextVar(
    "command_connection", default=None
)
SCHEMA_VERSION = 2

SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        qq_id TEXT NOT NULL UNIQUE,
        student_id TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        direction TEXT,
        status TEXT NOT NULL DEFAULT 'active',
        bound_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_submission_at TEXT,
        created_from_group TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS admins (
        qq_id TEXT PRIMARY KEY,
        added_by TEXT NOT NULL DEFAULT '',
        added_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS submissions (
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        qq_id_snapshot TEXT NOT NULL DEFAULT '',
        student_id_snapshot TEXT NOT NULL DEFAULT '',
        name_snapshot TEXT NOT NULL DEFAULT '',
        submitted_at TEXT NOT NULL,
        week_key TEXT NOT NULL,
        beijing_date TEXT,
        qq_group_id TEXT,
        qq_message_id TEXT NOT NULL,
        quoted_message_id TEXT NOT NULL DEFAULT '',
        original_filename TEXT NOT NULL DEFAULT '',
        stored_path TEXT NOT NULL DEFAULT '',
        file_size INTEGER NOT NULL DEFAULT 0,
        sha256 TEXT NOT NULL DEFAULT '',
        normalized_text_sha256 TEXT,
        extracted_char_count INTEGER NOT NULL DEFAULT 0,
        table_count INTEGER NOT NULL DEFAULT 0,
        image_count INTEGER NOT NULL DEFAULT 0,
        link_count INTEGER NOT NULL DEFAULT 0,
        duplicate_type TEXT NOT NULL DEFAULT 'none',
        duplicate_of TEXT,
        similarity_score REAL,
        status TEXT NOT NULL DEFAULT 'PENDING',
        counted INTEGER NOT NULL DEFAULT 0,
        counted_slot INTEGER,
        ai_decision TEXT,
        ai_progress TEXT,
        ai_confidence REAL,
        ai_feedback TEXT,
        ai_result_json TEXT,
        ai_provider_id TEXT,
        ai_latency_ms INTEGER,
        error_code TEXT,
        error_message TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users (id)
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_submissions_message ON submissions (qq_message_id)",
    "CREATE INDEX IF NOT EXISTS idx_submissions_sha ON submissions (sha256)",
    "CREATE INDEX IF NOT EXISTS idx_submissions_texthash ON submissions (normalized_text_sha256)",
    "CREATE INDEX IF NOT EXISTS idx_submissions_user_time ON submissions (user_id, submitted_at)",
    "CREATE INDEX IF NOT EXISTS idx_submissions_week ON submissions (week_key)",
    "CREATE INDEX IF NOT EXISTS idx_submissions_user_date ON submissions (user_id, beijing_date)",
    "CREATE INDEX IF NOT EXISTS idx_submissions_status ON submissions (status)",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_submissions_slot
    ON submissions (user_id, week_key, counted_slot)
    """,
    """
    CREATE TABLE IF NOT EXISTS count_adjustments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        week_key TEXT NOT NULL,
        delta INTEGER NOT NULL,
        admin_qq TEXT NOT NULL,
        reason TEXT,
        created_at TEXT NOT NULL,
        beijing_date TEXT,
        counted INTEGER NOT NULL DEFAULT 0,
        counted_slot INTEGER,
        FOREIGN KEY (user_id) REFERENCES users (id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_adjustments_user_week ON count_adjustments (user_id, week_key)",
    """
    CREATE TABLE IF NOT EXISTS pause_periods (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scope TEXT NOT NULL,
        start_at TEXT NOT NULL,
        scheduled_end_at TEXT NOT NULL,
        resumed_at TEXT,
        reason TEXT NOT NULL DEFAULT '',
        created_by_qq TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        exemption_week_key TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_pause_scope ON pause_periods (scope, scheduled_end_at)",
    """
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        actor_qq TEXT NOT NULL DEFAULT '',
        actor_role TEXT NOT NULL DEFAULT '',
        action TEXT NOT NULL DEFAULT '',
        target_type TEXT NOT NULL DEFAULT '',
        target_id TEXT NOT NULL DEFAULT '',
        request_message_id TEXT NOT NULL DEFAULT '',
        result TEXT NOT NULL DEFAULT '',
        details_json TEXT,
        created_at TEXT NOT NULL
    )
    """,
)


class Database:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _connect(self, *, command: bool = False) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=30.0, check_same_thread=not command)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _create_schema(self) -> None:
        conn = self._connect()
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError("数据库版本高于插件版本，请勿降级运行")
            existing = conn.execute("SELECT 1 FROM sqlite_master WHERE name='users'").fetchone()
            if existing and version < SCHEMA_VERSION:
                backup = self.path.with_suffix(f".before-v{SCHEMA_VERSION}.db")
                if not backup.exists():
                    temporary = backup.with_suffix(".db.part")
                    target = sqlite3.connect(temporary)
                    try:
                        conn.backup(target)
                    finally:
                        target.close()
                    temporary.replace(backup)
            conn.execute("BEGIN IMMEDIATE")
            for statement in SCHEMA_STATEMENTS:
                if "CREATE TABLE" in statement:
                    conn.execute(statement)
            if version < SCHEMA_VERSION:
                self._migrate(conn)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS week_policies (week_key TEXT PRIMARY KEY, weekly_limit INTEGER NOT NULL CHECK(weekly_limit > 0))"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS command_receipts (event_key TEXT PRIMARY KEY, response TEXT, created_at TEXT NOT NULL)"
            )
            conn.execute("CREATE TABLE IF NOT EXISTS reset_commits (operation_id TEXT PRIMARY KEY)")
            if version < SCHEMA_VERSION:
                conn.execute(
                    "INSERT OR IGNORE INTO week_policies SELECT week_key, 2 FROM submissions UNION SELECT week_key, 2 FROM count_adjustments"
                )
            for statement in SCHEMA_STATEMENTS:
                if "CREATE TABLE" not in statement and "idx_submissions_message" not in statement:
                    conn.execute(statement)
            conn.execute("DROP INDEX IF EXISTS idx_submissions_message")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_submission_event ON submissions(event_key) WHERE event_key IS NOT NULL"
            )
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """为旧库补齐新增列，并回填北京时间日期。"""

        old_adjustments = {
            row["name"] for row in conn.execute("PRAGMA table_info(count_adjustments)")
        }
        additions = (
            ("submissions", "event_key", "TEXT"),
            ("submissions", "beijing_date", "TEXT"),
            ("count_adjustments", "beijing_date", "TEXT"),
            ("count_adjustments", "counted", "INTEGER NOT NULL DEFAULT 0"),
            ("count_adjustments", "counted_slot", "INTEGER"),
        )
        for table, column, decl in additions:
            columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            if column not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

        # 回填 beijing_date，便于按北京日期做“每日一次”判定。
        from ..utils.time_utils import parse_iso, to_beijing

        for table, time_column in (
            ("submissions", "submitted_at"),
            ("count_adjustments", "created_at"),
        ):
            rows = conn.execute(
                f"SELECT id, {time_column} AS ts FROM {table} WHERE beijing_date IS NULL"
            ).fetchall()
            for row in rows:
                moment = parse_iso(row["ts"])
                if moment is None:
                    continue
                conn.execute(
                    f"UPDATE {table} SET beijing_date = ? WHERE id = ?",
                    (to_beijing(moment).strftime("%Y-%m-%d"), row["id"]),
                )
        if "counted" not in old_adjustments:
            events = conn.execute("""SELECT user_id, week_key, submitted_at AS ts, id, 1 AS delta, 'auto' AS kind
                FROM submissions WHERE status='VALID_COUNTED'
                UNION ALL SELECT user_id, week_key, created_at, id, delta, 'manual' FROM count_adjustments
                ORDER BY ts, kind, id""").fetchall()
            counts: dict[tuple, int] = {}
            sequences: dict[tuple, int] = {}
            for row in events:
                key = (row["user_id"], row["week_key"])
                current = counts.get(key, 0)
                counted = row["kind"] == "auto" or (row["delta"] > 0 and current < 2)
                if counted:
                    sequences[key] = sequences.get(key, 0) + 1
                counts[key] = max(0, current + (1 if counted else min(0, row["delta"])))
                if row["kind"] == "manual":
                    conn.execute(
                        "UPDATE count_adjustments SET counted=?, counted_slot=? WHERE id=?",
                        (int(counted), sequences.get(key) if counted else None, row["id"]),
                    )

    async def initialize(self) -> None:
        await asyncio.to_thread(self._create_schema)

    async def fetch(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """只读操作。"""
        if (conn := _command_connection.get()) is not None:
            return fn(conn)

        def work() -> T:
            conn = self._connect()
            try:
                return fn(conn)
            finally:
                conn.close()

        return await asyncio.to_thread(work)

    async def run(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """单次写操作，自动提交。"""
        if (conn := _command_connection.get()) is not None:
            return fn(conn)

        def work() -> T:
            conn = self._connect()
            try:
                result = fn(conn)
                conn.commit()
                return result
            except BaseException:
                conn.rollback()
                raise
            finally:
                conn.close()

        return await asyncio.to_thread(work)

    async def reset_checkin_data(self, operation_id: str | None = None) -> dict[str, int]:
        """清空全部打卡相关数据，但保留管理员表与 NAS 文件。

        按外键依赖顺序删除；同时重置自增序列。
        返回各表删除的行数。
        """

        tables = (
            "submissions",
            "count_adjustments",
            "pause_periods",
            "audit_log",
            "users",
            "week_policies",
        )

        def work(conn: sqlite3.Connection) -> dict[str, int]:
            if operation_id:
                conn.execute("INSERT INTO reset_commits VALUES (?)", (operation_id,))
            result: dict[str, int] = {}
            for table in tables:
                cursor = conn.execute(f"DELETE FROM {table}")  # noqa: S608 - 表名为固定白名单
                result[table] = cursor.rowcount
            try:
                placeholders = ", ".join("?" for _ in tables)
                conn.execute(
                    f"DELETE FROM sqlite_sequence WHERE name IN ({placeholders})",
                    tables,
                )
            except sqlite3.OperationalError:
                # 尚未产生自增记录时 sqlite_sequence 可能不存在。
                pass
            return result

        return await self.transaction(work, immediate=True)

    async def transaction(
        self,
        fn: Callable[[sqlite3.Connection], T],
        immediate: bool = True,
    ) -> T:
        """显式事务。immediate=True 时使用 BEGIN IMMEDIATE 防止写竞争。"""
        if (conn := _command_connection.get()) is not None:
            return fn(conn)

        def work() -> T:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
                result = fn(conn)
                conn.commit()
                return result
            except BaseException:
                conn.rollback()
                raise
            finally:
                conn.close()

        return await asyncio.to_thread(work)

    async def weekly_target(self, week_key: str, configured: int) -> int:
        def work(conn):
            conn.execute(
                "INSERT OR IGNORE INTO week_policies VALUES (?, ?)", (week_key, max(1, configured))
            )
            return conn.execute(
                "SELECT weekly_limit FROM week_policies WHERE week_key=?", (week_key,)
            ).fetchone()[0]

        return await self.transaction(work)

    async def execute_command(self, key: str, now: str, callback) -> str:
        """短管理员操作与回复回执在同一事务提交；不得在 callback 中调用模型或下载。"""

        def begin():
            connection = self._connect(command=True)
            try:
                connection.execute("BEGIN IMMEDIATE")
                return connection
            except BaseException:
                connection.close()
                raise

        # 等待其他写事务释放 SQLite 锁时不占用事件循环。连接移交后仅由本协程使用。
        opening = asyncio.create_task(asyncio.to_thread(begin))
        try:
            conn = await asyncio.shield(opening)
        except asyncio.CancelledError:
            connection = await opening
            connection.close()
            raise
        token = None
        try:
            row = conn.execute(
                "SELECT response FROM command_receipts WHERE event_key=?", (key,)
            ).fetchone()
            if row:
                return row["response"] or "词九已经接收过这条消息，请使用新消息重试。"
            token = _command_connection.set(conn)
            response = await callback()
            conn.execute("INSERT INTO command_receipts VALUES (?, ?, ?)", (key, response, now))
            conn.commit()
            return response
        except BaseException:
            conn.rollback()
            raise
        finally:
            if token is not None:
                _command_connection.reset(token)
            conn.close()


def row_to_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    return dict(row)


def rows_to_dicts(rows: Any) -> list[dict[str, Any]]:
    return [row_to_dict(row) for row in (rows or [])]
