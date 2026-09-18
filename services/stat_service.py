"""本周统计与饼图生成。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..repositories.admin_repo import AdminRepository
from ..repositories.pause_repo import PauseRepository
from ..repositories.submission_repo import SubmissionRepository
from ..repositories.user_repo import UserRepository
from ..utils import response_templates as T
from ..utils.time_utils import week_key_now

_CJK_FONT_CANDIDATES = (
    "Microsoft YaHei",
    "SimHei",
    "Noto Sans CJK SC",
    "Noto Sans CJK JP",
    "Source Han Sans SC",
    "WenQuanYi Zen Hei",
    "PingFang SC",
    "Arial Unicode MS",
)


@dataclass(slots=True)
class StatBundle:
    text: str
    image_path: Path | None = None
    paused: bool = False


class StatService:
    def __init__(
        self,
        *,
        user_repo: UserRepository,
        submission_repo: SubmissionRepository,
        admin_repo: AdminRepository,
        pause_repo: PauseRepository,
        image_dir: Path,
        weekly_limit: int,
        timezone: str,
        logger: Any,
    ) -> None:
        self.user_repo = user_repo
        self.submission_repo = submission_repo
        self.admin_repo = admin_repo
        self.pause_repo = pause_repo
        self.image_dir = Path(image_dir)
        self.weekly_limit = max(1, weekly_limit)
        self.timezone = timezone
        self.logger = logger

    async def stat(self) -> StatBundle:
        week_key = week_key_now(self.timezone)
        users = await self.user_repo.list_users(active_only=True)
        total = len(users)

        exemption = await self.pause_repo.exempted_week(week_key)
        if exemption is not None:
            return StatBundle(
                text=T.stat_paused(total, exemption.reason), image_path=None, paused=True
            )
        if total == 0:
            return StatBundle(text=T.stat_normal(0, 0, 0, 0), image_path=None)

        auto_by_user = await self.submission_repo.count_auto_counted_by_week(week_key)
        adjust_by_user = await self.admin_repo.sum_adjustments_by_week(week_key)

        completed = one = zero = 0
        for user in users:
            effective = max(0, auto_by_user.get(user.id, 0) + adjust_by_user.get(user.id, 0))
            if effective >= self.weekly_limit:
                completed += 1
            elif effective == 1:
                one += 1
            else:
                zero += 1

        text = T.stat_normal(total, completed, one, zero)
        image_path = await self._render_pie(completed, one, zero)
        return StatBundle(text=text, image_path=image_path)

    async def _render_pie(self, completed: int, one: int, zero: int) -> Path | None:
        try:
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("matplotlib 不可用，跳过饼图: %s", exc)
            return None

        font_name = self._pick_cjk_font(matplotlib)
        if font_name:
            plt.rcParams["font.sans-serif"] = [font_name, "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False

        labels = ["0 次", "1 次", "2 次及以上"]
        sizes = [zero, one, completed]
        filtered = [(label, size) for label, size in zip(labels, sizes, strict=True) if size > 0]
        if not filtered:
            return None
        chart_labels = [item[0] for item in filtered]
        chart_sizes = [item[1] for item in filtered]

        try:
            figure, axis = plt.subplots(figsize=(4.2, 4.2), dpi=150)
            axis.pie(
                chart_sizes,
                labels=chart_labels,
                autopct=lambda value: f"{value:.0f}%",
                startangle=90,
            )
            axis.axis("equal")
            self.image_dir.mkdir(parents=True, exist_ok=True)
            path = self.image_dir / f"week_stat_{week_key_now(self.timezone)}.png"
            figure.savefig(path, bbox_inches="tight")
            plt.close(figure)
            return path
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("生成饼图失败: %s", exc)
            return None

    @staticmethod
    def _pick_cjk_font(matplotlib_module: Any) -> str | None:
        try:
            from matplotlib import font_manager

            available = {font.name for font in font_manager.fontManager.ttflist}
        except Exception:  # noqa: BLE001
            return None
        for candidate in _CJK_FONT_CANDIDATES:
            if candidate in available:
                return candidate
        return None
