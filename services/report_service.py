"""统计卡片和 Excel 共用的单事务快照与指标定义。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..models.entities import CountAdjustment, PausePeriod, Submission, User
from ..utils.request_context import received_at
from ..utils.scoring import week_counted
from ..utils.time_utils import now_utc, week_key_of


@dataclass(slots=True)
class UserReport:
    user: User
    current: int
    automatic: int
    manual_counted: int
    manual_extra: int
    deductions: int
    cumulative_counted: int
    materials: int
    extra_materials: int
    rejected: int
    errors: int
    completed_weeks: int
    exempt_weeks: int
    adjustments_net: int
    all_manual_counted: int
    all_manual_extra: int
    all_deductions: int


@dataclass(slots=True)
class ReportSnapshot:
    week_key: str
    target: int
    generated_at: datetime
    paused: bool
    reason: str
    users: list[UserReport]
    submissions: list[Submission]
    adjustments: list[CountAdjustment]
    pauses: list[PausePeriod]
    personal: bool = False

    @property
    def population(self):
        return [row for row in self.users if row.user.status == "active"]

    @property
    def counts(self):
        rows = self.population
        return (
            sum(r.current >= self.target for r in rows),
            sum(0 < r.current < self.target for r in rows),
            sum(r.current == 0 for r in rows),
        )

    @property
    def total(self):
        return len(self.population)


class ReportService:
    def __init__(self, db, weekly_limit, timezone):
        self.db, self.weekly_limit, self.timezone = db, weekly_limit, timezone

    async def snapshot(self, user_id=None):
        now = received_at.get() or now_utc()
        week = week_key_of(now, self.timezone)
        await self.db.weekly_target(week, self.weekly_limit)

        def read(conn):
            target = conn.execute(
                "SELECT weekly_limit FROM week_policies WHERE week_key=?", (week,)
            ).fetchone()[0]
            where, params = (" WHERE id=?", (user_id,)) if user_id is not None else ("", ())
            users = [
                User.from_row(r)
                for r in conn.execute(
                    "SELECT * FROM users" + where + " ORDER BY student_id", params
                )
            ]
            where = " WHERE user_id=?" if user_id is not None else ""
            submissions = [
                Submission.from_row(r)
                for r in conn.execute(
                    "SELECT * FROM submissions" + where + " ORDER BY submitted_at", params
                )
            ]
            adjustments = [
                CountAdjustment.from_row(r)
                for r in conn.execute(
                    "SELECT * FROM count_adjustments" + where + " ORDER BY created_at, id", params
                )
            ]
            pauses = [
                PausePeriod.from_row(r)
                for r in conn.execute("SELECT * FROM pause_periods ORDER BY id")
            ]
            policies = dict(
                conn.execute("SELECT week_key, weekly_limit FROM week_policies").fetchall()
            )
            return target, users, submissions, adjustments, pauses, policies

        target, users, submissions, adjustments, pauses, policies = await self.db.transaction(
            read, immediate=False
        )
        exempt = {p.exemption_week_key for p in pauses if p.exemption_week_key and not p.resumed_at}
        current_pause = next(
            (p for p in reversed(pauses) if p.exemption_week_key == week and not p.resumed_at), None
        )
        records = {u.id: [] for u in users}
        adjustments_by_user = {u.id: [] for u in users}
        for item in submissions:
            records[item.user_id].append(item)
        for item in adjustments:
            adjustments_by_user[item.user_id].append(item)
        rows = []
        for user in users:
            docs, manual = records[user.id], adjustments_by_user[user.id]
            auto, counted, negative, extra = {}, {}, {}, {}
            for item in docs:
                if item.status == "VALID_COUNTED":
                    auto[item.week_key] = auto.get(item.week_key, 0) + 1
            for item in manual:
                bucket = negative if item.delta < 0 else counted if item.counted else extra
                bucket[item.week_key] = bucket.get(item.week_key, 0) + abs(item.delta)
            weeks = set(auto) | set(counted) | set(negative)
            totals = {
                w: week_counted(
                    auto.get(w, 0), counted.get(w, 0), negative.get(w, 0), policies.get(w, 2)
                )
                for w in weeks
            }
            bound_week = week_key_of(datetime.fromisoformat(user.bound_at), self.timezone)
            rows.append(
                UserReport(
                    user=user,
                    current=totals.get(week, 0),
                    automatic=auto.get(week, 0),
                    manual_counted=counted.get(week, 0),
                    manual_extra=extra.get(week, 0),
                    deductions=negative.get(week, 0),
                    cumulative_counted=sum(totals.values()),
                    materials=sum(s.status in {"VALID_COUNTED", "VALID_EXTRA"} for s in docs),
                    extra_materials=sum(s.status == "VALID_EXTRA" for s in docs),
                    rejected=sum(
                        s.status in {"REJECTED_AI", "REJECTED_DUPLICATE", "REJECTED_FILE"}
                        for s in docs
                    ),
                    errors=sum(s.status in {"AI_ERROR", "PROCESSING_ERROR"} for s in docs),
                    completed_weeks=sum(
                        w not in exempt and n >= policies.get(w, 2) for w, n in totals.items()
                    ),
                    exempt_weeks=sum(bound_week <= w <= week for w in exempt),
                    adjustments_net=sum(a.delta for a in manual),
                    all_manual_counted=sum(counted.values()),
                    all_manual_extra=sum(extra.values()),
                    all_deductions=sum(negative.values()),
                )
            )
        return ReportSnapshot(
            week,
            target,
            now,
            current_pause is not None,
            current_pause.reason if current_pause else "",
            rows,
            submissions,
            adjustments,
            pauses,
            user_id is not None,
        )
