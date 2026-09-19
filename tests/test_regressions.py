import asyncio
import sqlite3
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot_plugin_direct_checkin.repositories.admin_repo import AdminRepository
from astrbot_plugin_direct_checkin.repositories.db import SCHEMA_STATEMENTS, Database
from astrbot_plugin_direct_checkin.repositories.pause_repo import PauseRepository
from astrbot_plugin_direct_checkin.repositories.submission_repo import SubmissionRepository
from astrbot_plugin_direct_checkin.repositories.user_repo import UserRepository
from astrbot_plugin_direct_checkin.services.ai_review_service import AIReviewService
from astrbot_plugin_direct_checkin.services.binding_service import BindingService
from astrbot_plugin_direct_checkin.services.command_router import CommandName, parse_command
from astrbot_plugin_direct_checkin.services.operation_gate import MaintenanceError, OperationGate
from astrbot_plugin_direct_checkin.services.report_service import ReportService
from astrbot_plugin_direct_checkin.tests.test_admin_reset import SUPER, _make_service
from astrbot_plugin_direct_checkin.tests.test_duplicate import _finalize, _setup
from astrbot_plugin_direct_checkin.tests.test_repositories import NOW, _make_pending
from astrbot_plugin_direct_checkin.utils.request_context import event_key


def test_deduct_then_replenish_uses_new_sequence(tmp_path):
    async def run():
        db = Database(tmp_path / "db")
        await db.initialize()
        users, submissions, admins = (
            UserRepository(db),
            SubmissionRepository(db),
            AdminRepository(db),
        )
        user = await users.create("1", "20260001", "A", None, NOW)
        await _make_pending(submissions, user, "s1", "2025-12-29")
        await submissions.finalize_counted(
            "s1", user.id, "2025-12-29", "2025-12-29", 2, {"updated_at": NOW}
        )
        assert await admins.deduct(user.id, "2025-12-29", "2025-12-30", 2, "9", NOW) == 0
        await _make_pending(submissions, user, "s2", "2025-12-30")
        assert await submissions.finalize_counted(
            "s2", user.id, "2025-12-29", "2025-12-30", 2, {"updated_at": NOW}
        ) == ("VALID_COUNTED", 2, 1, "counted")
        assert (await users.get_by_id(user.id)).last_submission_at == NOW
        with pytest.raises(ValueError):
            await submissions.finalize_counted("s2", user.id, "2025-12-29", "2025-12-30", 2, {})
        assert (await submissions.get_by_id("s2")).status == "VALID_COUNTED"

    asyncio.run(run())


def test_concurrent_deduction_and_last_admin(tmp_path):
    async def run():
        db = Database(tmp_path / "db")
        await db.initialize()
        users, admins = UserRepository(db), AdminRepository(db)
        user = await users.create("1", "20260001", "A", None, NOW)
        await admins.allocate_positive_adjustment(user.id, "2026-01-05", "2026-01-05", 2, "9", NOW)
        results = await asyncio.gather(
            *(admins.deduct(user.id, "2026-01-05", "2026-01-05", 2, "9", NOW) for _ in range(8))
        )
        assert results.count(0) == 1 and results.count(None) == 7
        assert await admins.count_negatives_in_week(user.id, "2026-01-05") == 1
        await admins.add("1", "9", NOW)
        await admins.add("2", "9", NOW)
        results = await asyncio.gather(
            admins.remove_protected("1", ""), admins.remove_protected("2", "")
        )
        assert sorted(results) == ["last", "removed"] and await admins.count() == 1

    asyncio.run(run())


def test_legacy_migration_and_backup_are_idempotent(tmp_path):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    for statement in SCHEMA_STATEMENTS:
        if "CREATE TABLE" not in statement:
            continue
        statement = statement.replace("        beijing_date TEXT,\n", "")
        if "CREATE TABLE IF NOT EXISTS count_adjustments" in statement:
            statement = statement.replace(
                "        counted INTEGER NOT NULL DEFAULT 0,\n", ""
            ).replace("        counted_slot INTEGER,\n", "")
        conn.execute(statement)
    conn.execute(
        "INSERT INTO users (qq_id,student_id,name,bound_at,updated_at) VALUES ('1','20260001','A',?,?)",
        (NOW, NOW),
    )
    for delta in (1, 1, 1, -1, 1):
        conn.execute(
            "INSERT INTO count_adjustments(user_id,week_key,delta,admin_qq,created_at) VALUES (1,'2025-12-29',?,'9',?)",
            (delta, NOW),
        )
    conn.commit()
    conn.close()

    async def run():
        db = Database(path)
        await db.initialize()
        first = await AdminRepository(db).list_adjustments()
        assert [r.counted for r in first] == [1, 1, 0, 0, 1]
        assert all(r.beijing_date == "2026-01-01" for r in first)
        assert await db.weekly_target("2025-12-29", 3) == 2
        backup = path.with_suffix(".before-v2.db")
        assert backup.exists()
        modified = backup.stat().st_mtime_ns
        await db.initialize()
        assert await AdminRepository(db).list_adjustments() == first
        assert backup.stat().st_mtime_ns == modified

    asyncio.run(run())


def test_week_target_freezes_and_reports_arbitrary_limit(tmp_path, monkeypatch):
    async def run():
        db = Database(tmp_path / "db")
        await db.initialize()
        user = await UserRepository(db).create("1", "20260001", "A", None, NOW)
        now = datetime.fromisoformat("2026-01-05T02:00:00+00:00")
        monkeypatch.setattr(
            "astrbot_plugin_direct_checkin.services.report_service.now_utc", lambda: now
        )
        assert await db.weekly_target("2026-01-05", 3) == 3
        assert await db.weekly_target("2026-01-05", 1) == 3
        for _ in range(2):
            await AdminRepository(db).allocate_positive_adjustment(
                user.id, "2026-01-05", "2026-01-05", 3, "9", NOW
            )
        report = await ReportService(db, 3, "Asia/Shanghai").snapshot()
        assert report.counts == (0, 1, 0)
        assert report.users[0].cumulative_counted == 2
        assert report.users[0].materials == 0
        assert await db.weekly_target("2026-01-12", 1) == 1

    asyncio.run(run())


def test_command_transaction_rolls_back_and_replays(tmp_path):
    async def run():
        db = Database(tmp_path / "db")
        await db.initialize()
        admin = AdminRepository(db)

        async def bad():
            await admin.add("1", "9", NOW)
            raise RuntimeError("failure")

        with pytest.raises(RuntimeError):
            await db.execute_command("key", NOW, bad)
        assert await admin.count() == 0

        async def good():
            await admin.add("1", "9", NOW)
            return "done"

        assert await db.execute_command("key", NOW, good) == "done"
        assert await db.execute_command("key", NOW, bad) == "done"
        assert await admin.count() == 1

    asyncio.run(run())


def test_binding_conflicts_and_explicit_identifiers(tmp_path):
    async def run():
        db = Database(tmp_path / "db")
        await db.initialize()
        users = UserRepository(db)
        binding = BindingService(users, r"\d{6,20}", 30)
        results = await asyncio.gather(
            *(binding.bind(str(i), "20260001", "A", None, NOW) for i in range(5))
        )
        assert sum(r.status == "ok" for r in results) == 1
        assert sum(r.status == "student_conflict" for r in results) == 4
        owner = await users.get_by_student_id("20260001")
        other = await users.create("20260001", "20260002", "B", None, NOW)
        assert await users.resolve_identifier("20260001") == (None, True)
        assert (await users.resolve_identifier("sid:20260001"))[0] == owner
        assert (await users.resolve_identifier("qq:20260001"))[0] == other
        assert binding.validate("20260001bad", "A")

    asyncio.run(run())


@pytest.mark.parametrize(
    "text",
    [
        "/d add 123 extra",
        "/d reset confirm now",
        "/d stat extra",
        "/d get bad",
        "/d admin add",
        "/d resume now",
    ],
)
def test_invalid_command_has_no_mutation(text):
    assert parse_command(text).name == CommandName.UNKNOWN


def test_event_identity_scopes_and_missing_fields():
    def event(bot="1", origin="group:1", msg="123"):
        return SimpleNamespace(
            message_obj=SimpleNamespace(self_id=bot, message_id=msg), unified_msg_origin=origin
        )

    assert event_key(event()) == event_key(event())
    assert (
        len({event_key(event()), event_key(event(bot="2")), event_key(event(origin="private:1"))})
        == 3
    )
    assert event_key(event(bot="")) is None


def test_exclusive_gate_drains_and_rejects_new_requests():
    async def run():
        gate = OperationGate()
        running = asyncio.Event()
        release = asyncio.Event()
        exclusive = asyncio.Event()

        async def reader():
            async with gate.enter():
                running.set()
                await release.wait()

        async def writer():
            async with gate.enter(True):
                exclusive.set()

        task = asyncio.create_task(reader())
        await running.wait()
        reset = asyncio.create_task(writer())
        await asyncio.sleep(0)
        assert not exclusive.is_set()
        with pytest.raises(MaintenanceError):
            async with gate.enter():
                pass
        release.set()
        await asyncio.gather(task, reset)
        assert exclusive.is_set() and not gate.maintenance

    asyncio.run(run())


def test_reset_rolls_back_files_on_db_failure(tmp_path, monkeypatch):
    async def run():
        nas = tmp_path / "nas"
        nas.mkdir()
        (nas / "keep.docx").write_bytes(b"data")
        db, repos, service = await _make_service(tmp_path, nas)
        await repos["user_repo"].create("1", "20260001", "A", None, NOW)
        monkeypatch.setattr(db, "reset_checkin_data", AsyncMock(side_effect=RuntimeError("db")))
        counts, result = await service.reset_service.execute()
        assert counts is None and "未清空" in result
        assert (nas / "keep.docx").read_bytes() == b"data"
        assert len(await repos["user_repo"].list_users()) == 1
        assert not service.reset_service.journal.exists()

    asyncio.run(run())


def test_reset_committed_cleanup_failure_recovers(tmp_path, monkeypatch):
    async def run():
        nas = tmp_path / "nas"
        nas.mkdir()
        (nas / "a.docx").write_bytes(b"data")
        _, repos, service = await _make_service(tmp_path, nas)
        await repos["user_repo"].create("1", "20260001", "A", None, NOW)
        reset = service.reset_service
        original = reset._purge
        monkeypatch.setattr(reset, "_purge", lambda data: (_ for _ in ()).throw(OSError("denied")))
        counts, result = await reset.execute()
        assert counts is None and "数据已重置" in result
        assert await repos["user_repo"].list_users() == [] and reset.journal.exists()
        monkeypatch.setattr(reset, "_purge", original)
        await reset.recover()
        assert list(nas.iterdir()) == [] and not reset.journal.exists()

    asyncio.run(run())


def test_reset_crash_during_staging_restores_on_restart(tmp_path):
    async def run():
        nas = tmp_path / "nas"
        nas.mkdir()
        (nas / "a.docx").write_bytes(b"data")
        _, _, service = await _make_service(tmp_path, nas)
        service.reset_service._stage()
        assert not (nas / "a.docx").exists()
        await service.reset_service.recover()
        assert (nas / "a.docx").read_bytes() == b"data"

    asyncio.run(run())


def test_duplicate_reset_confirm_is_not_second_confirmation(tmp_path):
    async def run():
        nas = tmp_path / "nas"
        nas.mkdir()
        (nas / "a.docx").write_bytes(b"data")
        _, _, service = await _make_service(tmp_path, nas)
        first = await service.reset(SUPER, True, "same")
        assert await service.reset(SUPER, True, "same") == first
        assert (nas / "a.docx").exists()

    asyncio.run(run())


def test_pause_ends_exactly_at_exclusive_boundary(tmp_path):
    async def run():
        db = Database(tmp_path / "db")
        await db.initialize()
        pause = PauseRepository(db)
        end = "2026-01-04T16:00:00+00:00"
        await pause.create("week", NOW, end, "", "9", NOW, "2025-12-29")
        assert await pause.active(end) is None

    asyncio.run(run())


def test_high_similarity_with_new_progress_is_reviewed(tmp_path):
    async def run():
        _, repo, service, user = await _setup(tmp_path)
        old = "实现了上传接口并完成测试。\n" * 100
        new = old + "新增：修复空文件校验并新增集成测试。"
        await _finalize(repo, user, "s1", "old", old)
        outcome = await service.check(user, "new", new, NOW, AsyncMock(return_value=old))
        assert outcome.outcome.value == "suspicious"
        assert outcome.duplicate_type.value == "high_similarity"
        assert "新增：修复空文件校验" in outcome.note

    asyncio.run(run())


def test_ai_retry_and_separate_system_prompt():
    async def run():
        context = SimpleNamespace(
            get_current_chat_provider_id=AsyncMock(return_value="test"),
            llm_generate=AsyncMock(
                side_effect=[
                    SimpleNamespace(completion_text="bad"),
                    SimpleNamespace(
                        completion_text='{"decision":"pass","brief_feedback":"有进展"}'
                    ),
                ]
            ),
        )
        logger = SimpleNamespace(warning=lambda *a: None, error=lambda *a: None)
        ai = AIReviewService(context, "固定审核规则\n========\n__DOCUMENT__", 5, 1, logger)
        result = await ai.review("umo", "__HISTORY__ 文档内容", "evidence", "history")
        assert result.passed and context.llm_generate.await_count == 2
        call = context.llm_generate.call_args.kwargs
        assert call["system_prompt"] == "固定审核规则\n"
        assert "__HISTORY__ 文档内容" in call["prompt"]

    asyncio.run(run())


def test_partial_stage_failure_restores_without_clearing(tmp_path, monkeypatch):
    async def run():
        from pathlib import Path

        nas = tmp_path / "nas"
        nas.mkdir()
        (nas / "a").write_bytes(b"a")
        (nas / "b").write_bytes(b"b")
        _, repos, service = await _make_service(tmp_path, nas)
        await repos["user_repo"].create("1", "20260001", "A", None, NOW)
        original = Path.rename

        def fail_second(source, target):
            if source == nas / "b":
                raise OSError("staging denied")
            return original(source, target)

        monkeypatch.setattr(Path, "rename", fail_second)
        counts, message = await service.reset_service.execute()
        assert counts is None and "已恢复" in message
        assert (nas / "a").read_bytes() == b"a" and (nas / "b").read_bytes() == b"b"
        assert len(await repos["user_repo"].list_users()) == 1
        assert not service.reset_service.journal.exists()

    asyncio.run(run())


def test_recovery_refuses_traversal_and_same_name_overwrite(tmp_path):
    async def run():
        import json

        nas = tmp_path / "nas"
        nas.mkdir()
        (nas / "a").write_bytes(b"original")
        _, _, service = await _make_service(tmp_path, nas)
        reset = service.reset_service
        data = reset._stage()
        (nas / "a").write_bytes(b"new")
        with pytest.raises(RuntimeError, match="已存在"):
            await reset.recover()
        assert reset.journal.exists() and (nas / "a").read_bytes() == b"new"
        data["entries"] = ["../outside"]
        reset.journal.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ValueError, match="路径无效"):
            await reset.recover()
        assert reset.journal.exists()

    asyncio.run(run())


@pytest.mark.parametrize("value", [True, 0, -1, 101, 1.5, "bad", float("inf")])
def test_invalid_weekly_configuration_uses_default(value):
    import logging

    from astrbot_plugin_direct_checkin.utils.config import validate

    assert validate({"weekly_limit": value}, logging.getLogger("test"))["weekly_limit"] == 2


def test_short_command_waits_for_write_lock_without_blocking_event_loop(tmp_path):
    async def run():
        import threading

        db = Database(tmp_path / "db")
        await db.initialize()
        held, release = threading.Event(), threading.Event()

        def hold(conn):
            held.set()
            assert release.wait(3)

        writer = asyncio.create_task(db.transaction(hold))
        await asyncio.to_thread(held.wait)

        async def response():
            return "ok"

        command = asyncio.create_task(db.execute_command("key", NOW, response))
        await asyncio.sleep(0.05)
        assert not command.done()
        release.set()
        await writer
        assert await command == "ok"

    asyncio.run(run())


def test_migration_failure_rolls_back_schema_and_preserves_backup(tmp_path, monkeypatch):
    async def run():
        db = Database(tmp_path / "legacy.db")
        await db.initialize()
        await UserRepository(db).create("1", "20260001", "A", None, NOW)
        await db.run(lambda conn: conn.execute("PRAGMA user_version=0"))
        original = db._migrate

        def fail(conn):
            conn.execute("ALTER TABLE users ADD COLUMN should_rollback TEXT")
            raise RuntimeError("migration interrupted")

        monkeypatch.setattr(db, "_migrate", fail)
        with pytest.raises(RuntimeError, match="interrupted"):
            await db.initialize()
        columns, version = await db.fetch(
            lambda conn: (
                [r["name"] for r in conn.execute("PRAGMA table_info(users)")],
                conn.execute("PRAGMA user_version").fetchone()[0],
            )
        )
        assert "should_rollback" not in columns and version == 0
        assert len(await UserRepository(db).list_users()) == 1
        assert db.path.with_suffix(".before-v2.db").is_file()
        monkeypatch.setattr(db, "_migrate", original)
        await db.initialize()
        assert len(await UserRepository(db).list_users()) == 1

    asyncio.run(run())
