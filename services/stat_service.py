"""本周统计卡片：服务器端无界面绘制，失败回退文字。"""

from __future__ import annotations

import asyncio
import os
import textwrap
import threading
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from ..utils import response_templates as T
from ..utils import visual_style as V
from ..utils.time_utils import parse_week_key, to_beijing
from .report_service import ReportService, ReportSnapshot

_RENDER_LOCK = threading.Lock()


@dataclass(slots=True)
class StatBundle:
    text: str
    image_path: Path | None = None
    paused: bool = False


class StatService:
    def __init__(
        self,
        *,
        user_repo,
        submission_repo,
        admin_repo,
        pause_repo,
        image_dir,
        weekly_limit,
        timezone,
        logger,
    ):
        self.report = ReportService(user_repo.db, weekly_limit, timezone)
        self.image_dir, self.timezone, self.logger = Path(image_dir), timezone, logger
        self._semaphore = asyncio.Semaphore(1)

    async def stat(self) -> StatBundle:
        snapshot = await self.report.snapshot()
        if snapshot.paused:
            text = T.stat_paused(snapshot.total, snapshot.reason)
        else:
            text = T.stat_normal(snapshot.total, *snapshot.counts)
        text += f"\n考核周：{snapshot.week_key} 起，每人目标 {snapshot.target} 次。"
        try:
            async with self._semaphore:
                path = await asyncio.to_thread(self.render, snapshot)
        except Exception:
            self.logger.exception("统计卡片生成失败，保留文字统计")
            path = None
        return StatBundle(text, path, snapshot.paused)

    def render(self, snapshot: ReportSnapshot) -> Path:
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
        from matplotlib.font_manager import FontProperties
        from matplotlib.patches import FancyBboxPatch

        with _RENDER_LOCK:
            font = FontProperties(fname=str(V.FONT_PATH))
            figure = Figure(figsize=(10, 7), dpi=150, facecolor=V.PALE)
            FigureCanvasAgg(figure)
            panel = figure.add_axes([0, 0, 1, 1])
            panel.set_axis_off()

            def box(x, y, width, height, color="white"):
                panel.add_patch(
                    FancyBboxPatch(
                        (x, y),
                        width,
                        height,
                        boxstyle="round,pad=0.008,rounding_size=0.022",
                        linewidth=0,
                        facecolor=color,
                        transform=panel.transAxes,
                    )
                )

            def text(x, y, value, size=12, color=V.NAVY, **kwargs):
                figure.text(
                    x,
                    y,
                    str(value),
                    fontproperties=font,
                    fontsize=size,
                    color=color,
                    va="center",
                    **kwargs,
                )

            monday = parse_week_key(snapshot.week_key)
            end = monday + timedelta(days=6)
            text(0.055, 0.925, "直属队 · 本周学习打卡", 24)
            text(0.055, 0.862, f"{monday:%Y.%m.%d} — {end:%m.%d}", 12, V.MUTED)
            text(0.94, 0.866, f"每人目标 {snapshot.target} 次", 12, V.BLUE, ha="right")
            completed, ongoing, zero = snapshot.counts
            box(0.05, 0.635, 0.9, 0.165)
            kpis = [
                ("参与人数", f"{snapshot.total}", "人"),
                (
                    "本周状态" if snapshot.paused else "完成率",
                    "暂停"
                    if snapshot.paused
                    else f"{completed / snapshot.total:.0%}"
                    if snapshot.total
                    else "—",
                    "",
                ),
                (
                    "考核要求",
                    "豁免" if snapshot.paused else str(snapshot.target),
                    "" if snapshot.paused else "次 / 人",
                ),
            ]
            for x, (label, value, unit) in zip((0.085, 0.385, 0.685), kpis, strict=True):
                text(x, 0.756, label, 11, V.MUTED)
                text(x, 0.685, value, 27, V.TEAL if label == "完成率" else V.NAVY)
                text(x + 0.14, 0.678, unit, 10, V.MUTED)
            box(0.05, 0.115, 0.9, 0.465)
            if snapshot.paused or snapshot.total == 0:
                text(
                    0.5,
                    0.43,
                    "本周暂停考核" if snapshot.paused else "等待第一位同学加入",
                    23,
                    V.BLUE,
                    ha="center",
                )
                message = snapshot.reason or (
                    "本周不按缺卡处理，已有学习记录保留。"
                    if snapshot.paused
                    else "同学绑定后，词九会在这里展示本周进度。"
                )
                wrapped = textwrap.wrap(message, width=36)
                if len(wrapped) > 3:
                    wrapped = wrapped[:3]
                    wrapped[-1] += "…"
                text(0.5, 0.295, "\n".join(wrapped), 13, V.MUTED, ha="center", linespacing=1.6)
            else:
                axis = figure.add_axes([0.085, 0.18, 0.34, 0.34])
                values = [completed, ongoing, zero]
                axis.pie(
                    values,
                    colors=[V.TEAL, V.BLUE, V.ZERO],
                    startangle=90,
                    counterclock=False,
                    wedgeprops={"width": 0.19, "edgecolor": "white", "linewidth": 3},
                )
                axis.set_aspect("equal")
                text(0.255, 0.365, f"{completed / snapshot.total:.0%}", 29, V.TEAL, ha="center")
                text(0.255, 0.307, "本周已完成", 11, V.MUTED, ha="center")
                for y, label, count, color in zip(
                    (0.46, 0.34, 0.22),
                    ("已完成", "进行中", "未开始"),
                    values,
                    (V.TEAL, V.BLUE, V.ZERO),
                    strict=True,
                ):
                    panel.plot(
                        [0.504],
                        [y],
                        marker="o",
                        markersize=9,
                        color=color,
                        transform=panel.transAxes,
                    )
                    text(0.53, y, label, 15)
                    text(
                        0.785,
                        y,
                        f"{count} 人",
                        18,
                        V.MUTED if label == "未开始" else color,
                        ha="right",
                    )
                    text(0.905, y, f"{count / snapshot.total:.1%}", 11, V.MUTED, ha="right")
            text(0.055, 0.055, "词九的学习记录  /  额外材料不抵扣其他周缺卡", 9, V.MUTED)
            text(
                0.945,
                0.055,
                to_beijing(snapshot.generated_at, self.timezone).strftime("%m-%d %H:%M"),
                9,
                V.MUTED,
                ha="right",
            )
            self.image_dir.mkdir(parents=True, exist_ok=True)
            path = self.image_dir / f"week_stat_{snapshot.week_key}_{uuid.uuid4().hex}.png"
            partial = path.with_suffix(".part")
            try:
                figure.savefig(partial, format="png", facecolor=figure.get_facecolor())
                os.replace(partial, path)
            finally:
                partial.unlink(missing_ok=True)
                figure.clear()
            return path
