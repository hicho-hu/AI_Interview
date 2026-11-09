"""Growth extensions introduced after c83f5a7e0203, through e913c5ab7d20.

Keep the original registration schema separate so offline and online migrations
add each column at its own revision instead of repeating columns from the base.
"""

import sqlalchemy as sa

from app.db.growth_schema_v1 import define_tables as define_base_tables
from app.db.types import LongText, UTCDateTime

COMMISSION_STATUS_COMMENT = "应计状态：ACCRUED=已计提，REVERSED=退款已冲销，SETTLED=已线下打款"
BASE_COMMISSION_STATUS_COMMENT = "应计状态：ACCRUED=已计提，REVERSED=退款已冲销"


def system_avatar_column():
    return sa.Column(
        "system_avatar_key", sa.String(16), nullable=True, comment="用户选择的系统头像目录标识"
    )


def alipay_account_column():
    return sa.Column(
        "alipay_account_encrypted", LongText, nullable=True, comment="线下打款用支付宝账号（加密存储）"
    )


def settled_at_column():
    return sa.Column("settled_at", UTCDateTime(), nullable=True, comment="线下打款结算时间（UTC）")


def define_tables(metadata):
    tables = define_base_tables(metadata)
    metadata.tables["user_accounts"].append_column(system_avatar_column())
    metadata.tables["promotion_links"].append_column(alipay_account_column())
    metadata.tables["promotion_commissions"].append_column(settled_at_column())
    metadata.tables["promotion_commissions"].c.status.comment = COMMISSION_STATUS_COMMENT
    return tables
