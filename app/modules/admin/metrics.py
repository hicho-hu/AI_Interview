"""Bounded UTC windows with explicit CNY and local-day cohort definitions."""

from datetime import datetime, time, timedelta, timezone

from sqlalchemy import distinct, func, select, text

from app.core.errors import require
from app.core.security import utcnow
from app.db.admin_repository import count, total
from app.db.models import AICallLog, Interview, MediaAsset, Order, Report, Task, User


class Metrics:
    def __init__(self, db, start=None, end=None, zone="Asia/Shanghai"):
        require(zone in ("Asia/Shanghai", "UTC"), "INVALID_TIMEZONE", "支持 Asia/Shanghai 或 UTC")
        self.db, self.zone = db, zone
        self.tz = timezone(timedelta(hours=8 if zone == "Asia/Shanghai" else 0))
        today = utcnow().astimezone(self.tz).date()
        self.first = start or today - timedelta(days=6)
        self.last = end or today
        require(
            self.first <= self.last and (self.last - self.first).days <= 92,
            "INVALID_DATE_RANGE",
            "统计范围需为 1 至 93 天",
        )
        self.start = datetime.combine(self.first, time.min, self.tz).astimezone(timezone.utc)
        self.end = datetime.combine(self.last + timedelta(days=1), time.min, self.tz).astimezone(timezone.utc)

    def window(self, column):
        return (column >= self.start, column < self.end)

    def meta(self):
        return {
            "timezone": self.zone,
            "start_at": self.start,
            "end_at": self.end,
            "currency": "CNY",
            "metric_version": "1.0",
            "updated_at": utcnow(),
            "definition": "面试指标按创建日期分组；支付与退款按实际发生时间。净实收不等于财务确认收入。",
        }

    def finance(self):
        paid = total(self.db, Order.amount_minor, *self.window(Order.paid_at))
        refunds = total(
            self.db, Order.amount_minor, Order.status == "REFUNDED", *self.window(Order.refunded_at)
        )
        orders = count(self.db, Order, *self.window(Order.paid_at))
        payers = (
            self.db.scalar(select(func.count(distinct(Order.user_id))).where(*self.window(Order.paid_at)))
            or 0
        )
        return {
            "paid_minor": paid,
            "refund_minor": refunds,
            "net_minor": paid - refunds,
            "paid_orders": orders,
            "paying_users": payers,
            "arppu_minor": round(paid / payers) if payers else 0,
            **self.meta(),
        }

    def overview(self):
        cohort = (*self.window(Interview.created_at), Interview.deleted_at.is_(None))
        created = count(self.db, Interview, *cohort)
        started = count(self.db, Interview, *cohort, Interview.started_at.is_not(None))
        completed = count(self.db, Interview, *cohort, Interview.status == "COMPLETED")
        expected = count(
            self.db, Interview, *cohort, Interview.status.in_(["FINISHING", "REPORT_FAILED", "COMPLETED"])
        )
        reported = (
            self.db.scalar(select(func.count(distinct(Report.interview_id))).join(Interview).where(*cohort))
            or 0
        )
        active = self.db.scalar(select(func.count(distinct(Interview.user_id))).where(*cohort)) or 0
        cost = total(self.db, AICallLog.cost_microunits, *self.window(AICallLog.created_at))
        modes = self.db.execute(
            select(Interview.mode, func.count()).where(*cohort).group_by(Interview.mode)
        ).all()
        return {
            **self.finance(),
            "new_users": count(self.db, User, *self.window(User.created_at)),
            "active_users": active,
            "created_interviews": created,
            "started_interviews": started,
            "completed_interviews": completed,
            "report_success": reported,
            "completion_rate": round(completed / started * 100, 1) if started else 0,
            "report_success_rate": round(reported / expected * 100, 1) if expected else 0,
            "ai_cost_microunits": cost,
            "modes": [{"name": k, "value": v} for k, v in modes],
            "funnel": [
                {"name": k, "value": v}
                for k, v in [
                    ("创建面试", created),
                    ("开始面试", started),
                    ("完成面试", completed),
                    ("生成报告", reported),
                ]
            ],
            "alerts": [
                {"name": "失败任务", "value": count(self.db, Task, Task.status == "FAILED")},
                {"name": "等待处理", "value": count(self.db, Task, Task.status == "PENDING")},
                {"name": "报告异常", "value": count(self.db, Interview, Interview.status == "REPORT_FAILED")},
                {
                    "name": "待上传媒体",
                    "value": count(self.db, MediaAsset, MediaAsset.status == "PENDING_UPLOAD"),
                },
            ],
        }

    def trends(self):
        days = {}
        for n in range((self.last - self.first).days + 1):
            day = str(self.first + timedelta(days=n))
            days[day] = {"date": day, "users": 0, "interviews": 0, "completed": 0, "paid_minor": 0}
        for model, column, key, conditions, value in [
            (User, User.created_at, "users", (), func.count()),
            (Interview, Interview.created_at, "interviews", (Interview.deleted_at.is_(None),), func.count()),
            (
                Interview,
                Interview.created_at,
                "completed",
                (Interview.status == "COMPLETED", Interview.deleted_at.is_(None)),
                func.count(),
            ),
            (Order, Order.paid_at, "paid_minor", (), func.sum(Order.amount_minor)),
        ]:
            shifted = column
            if self.zone != "UTC":
                shifted = (
                    func.datetime(column, "+8 hours")
                    if self.db.bind.dialect.name == "sqlite"
                    else func.timestampadd(text("HOUR"), 8, column)
                )
            day = func.date(shifted)
            for when, amount in self.db.execute(
                select(day, value).select_from(model).where(*self.window(column), *conditions).group_by(day)
            ):
                if str(when) in days:
                    days[str(when)][key] = amount or 0
        return {"items": list(days.values()), **self.meta()}

    def ai(self, settings):
        conditions = self.window(AICallLog.created_at)
        rows = self.db.execute(
            select(
                AICallLog.provider,
                AICallLog.model,
                AICallLog.call_type,
                func.count(),
                func.sum(AICallLog.input_tokens),
                func.sum(AICallLog.output_tokens),
                func.avg(AICallLog.latency_ms),
                func.sum(AICallLog.cost_microunits),
            )
            .where(*conditions)
            .group_by(AICallLog.provider, AICallLog.model, AICallLog.call_type)
        ).all()
        calls = count(self.db, AICallLog, *conditions)
        failed = count(self.db, AICallLog, *conditions, AICallLog.status != "SUCCEEDED")
        return {
            "items": [
                dict(
                    zip(
                        [
                            "provider",
                            "model",
                            "call_type",
                            "calls",
                            "input_tokens",
                            "output_tokens",
                            "latency_ms",
                            "cost_microunits",
                        ],
                        r,
                    )
                )
                for r in rows
            ],
            "calls": calls,
            "failed": failed,
            "failure_rate": round(failed / calls * 100, 1) if calls else 0,
            "routing": {
                "provider_mode": settings.provider_mode,
                "llm": settings.provider_model,
                "asr": settings.asr_model,
                "tts": settings.tts_model,
                "timeout_seconds": settings.provider_timeout_seconds,
                "retry_count": settings.provider_retry_count,
            },
            **self.meta(),
        }
