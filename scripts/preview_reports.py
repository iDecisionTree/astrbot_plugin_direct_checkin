"""用虚构记录生成真实服务的视觉验收样例，不访问部署数据库。

运行：python scripts/preview_reports.py --output outputs/review
"""

from __future__ import annotations

# 独立脚本先将插件的父目录加入搜索路径，再导入插件模块。
# ruff: noqa: E402
import argparse
import asyncio
import logging
import shutil
import sys
import tempfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_direct_checkin.repositories.admin_repo import AdminRepository
from astrbot_plugin_direct_checkin.repositories.db import Database
from astrbot_plugin_direct_checkin.repositories.pause_repo import PauseRepository
from astrbot_plugin_direct_checkin.repositories.submission_repo import SubmissionRepository
from astrbot_plugin_direct_checkin.repositories.user_repo import UserRepository
from astrbot_plugin_direct_checkin.services.export_service import ExportService
from astrbot_plugin_direct_checkin.services.report_service import ReportService
from astrbot_plugin_direct_checkin.services.stat_service import StatService


async def generate(output):
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="checkin_preview_") as directory:
        root = Path(directory)
        db = Database(root / "demo.db")
        await db.initialize()
        users, submissions, admins, pauses = (
            UserRepository(db),
            SubmissionRepository(db),
            AdminRepository(db),
            PauseRepository(db),
        )
        now = "2026-09-19T08:30:00+00:00"
        names = [
            "示例林同学",
            "示例陈同学",
            "用于检查中文长姓名换行的虚构学习记录示例同学",
            "示例王同学",
            "示例赵同学",
            "示例吴同学",
            "示例郑同学",
            "示例许同学",
            "示例何同学",
            "示例孙同学",
            "示例李同学",
            "这是一位用于检查待完成名单换行的虚构同学",
        ]
        for index, name in enumerate(names):
            user = await users.create(
                f"100000{index:04}",
                f"002026{index:04}",
                name,
                "demo-group",
                "2026-09-01T00:00:00+00:00",
            )
            count = 2 if index < 6 else 1 if index < 10 else 0
            for n in range(count):
                sid = f"demo-{index}-{n}"
                day = f"2026-09-{15 + n}"
                await submissions.create_pending(
                    submission_id=sid,
                    user_id=user.id,
                    qq_id=user.qq_id,
                    student_id=user.student_id,
                    name=name,
                    submitted_at=day + "T10:00:00+00:00",
                    week_key="2026-09-14",
                    beijing_date=day,
                    qq_group_id="demo-group",
                    qq_message_id=sid,
                    quoted_message_id="quoted-" + sid,
                    original_filename="学习记录与接口测试.docx",
                    stored_path="/mnt/nas/direct_checkin/示例路径.docx",
                    file_size=258048,
                    sha256="a" * 64,
                    now=now,
                )
                await submissions.finalize_counted(
                    sid,
                    user.id,
                    "2026-09-14",
                    day,
                    2,
                    {
                        "ai_decision": "pass",
                        "ai_confidence": 0.92,
                        "ai_feedback": "实现了上传接口并补充异常用例，测试过程清楚。",
                        "updated_at": now,
                    },
                )
        await admins.allocate_positive_adjustment(
            1, "2026-09-14", "2026-09-19", 2, "9000000000", now, reason="额外学习记录"
        )
        await pauses.create(
            "day",
            "2026-09-17T00:00:00+00:00",
            "2026-09-18T00:00:00+00:00",
            "服务器维护",
            "9000000000",
            now,
        )
        repositories = dict(
            user_repo=users,
            submission_repo=submissions,
            admin_repo=admins,
            pause_repo=pauses,
            weekly_limit=2,
            timezone="Asia/Shanghai",
        )
        with patch(
            "astrbot_plugin_direct_checkin.services.report_service.now_utc",
            return_value=datetime.fromisoformat(now),
        ):
            snapshot = await ReportService(db, 2, "Asia/Shanghai").snapshot()
        exporter = ExportService(**repositories, export_dir=root / "exports")
        stat = StatService(
            **repositories, image_dir=root / "images", logger=logging.getLogger("preview")
        )
        bundle = exporter.render(snapshot)
        shutil.copy2(bundle.path, output / "report-demo.xlsx")
        for filename, data in [
            ("stat-demo.png", snapshot),
            (
                "stat-paused.png",
                replace(snapshot, paused=True, reason="考试周暂停考核，已有学习记录继续保留。"),
            ),
            ("stat-empty.png", replace(snapshot, users=[])),
        ]:
            shutil.copy2(stat.render(data), output / filename)
        print("仅使用虚构数据，已生成样例：", output.resolve())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "outputs" / "review")
    asyncio.run(generate(parser.parse_args().output))
