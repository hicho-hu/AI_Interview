"""Frozen schema for per-mode balances and the source of each ledger movement."""

import sqlalchemy as sa

from app.core.security import new_id, utcnow
from app.db.types import MYSQL_TABLE_OPTIONS, UTCDateTime


def product_credits_column():
    return sa.Column(
        "interview_credits",
        sa.JSON,
        nullable=True,
        comment="按面试类型发放的次数：TEXT=文字、VOICE=语音、VIDEO=视频；NULL=历史通用次数套餐",
    )


def define_tables(metadata):
    balances = sa.Table(
        "interview_credit_balances",
        metadata,
        sa.Column(
            "id", sa.String(36), primary_key=True, default=new_id, comment="分类权益余额记录 ID（UUID）"
        ),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id"),
            nullable=False,
            comment="权益所属用户 ID，关联 users.id",
        ),
        sa.Column(
            "mode", sa.String(16), nullable=False, comment="面试类型：TEXT=文字、VOICE=语音、VIDEO=视频"
        ),
        sa.Column(
            "remaining",
            sa.Integer,
            nullable=False,
            default=0,
            server_default="0",
            comment="该类型可用次数，单位次；不含已预占次数和历史通用次数",
        ),
        sa.Column("created_at", UTCDateTime(), default=utcnow, nullable=False, comment="创建时间（UTC）"),
        sa.Column(
            "updated_at",
            UTCDateTime(),
            default=utcnow,
            onupdate=utcnow,
            nullable=False,
            comment="最后变更时间（UTC）",
        ),
        sa.UniqueConstraint("user_id", "mode", name="uq_credit_balance_user_mode"),
        sa.CheckConstraint("remaining >= 0", name="ck_credit_balance_nonnegative"),
        comment="按文字、语音、视频分别管理可用面试次数；旧通用次数继续存储于 users.credits",
        **MYSQL_TABLE_OPTIONS,
    )
    allocations = sa.Table(
        "usage_credit_allocations",
        metadata,
        sa.Column(
            "id", sa.String(36), primary_key=True, default=new_id, comment="权益流水分类明细 ID（UUID）"
        ),
        sa.Column(
            "usage_id",
            sa.String(36),
            sa.ForeignKey("usage_ledger.id"),
            nullable=False,
            comment="权益流水 ID，关联 usage_ledger.id",
        ),
        sa.Column(
            "mode",
            sa.String(16),
            nullable=False,
            comment="次数来源：TEXT/VOICE/VIDEO=对应类型；UNIVERSAL=历史通用次数",
        ),
        sa.Column(
            "quantity",
            sa.Integer,
            nullable=False,
            comment="该类型次数变化，单位次；发放为正、预占或退款为负；释放按原来源返还",
        ),
        sa.Column("created_at", UTCDateTime(), default=utcnow, nullable=False, comment="创建时间（UTC）"),
        sa.Column(
            "updated_at",
            UTCDateTime(),
            default=utcnow,
            onupdate=utcnow,
            nullable=False,
            comment="最后变更时间（UTC）",
        ),
        sa.UniqueConstraint("usage_id", "mode", name="uq_usage_credit_mode"),
        comment="权益流水的类型分配明细，保证取消、重试及退款使用原始类型；历史无明细流水视为通用次数",
        **MYSQL_TABLE_OPTIONS,
    )
    return [balances, allocations]
