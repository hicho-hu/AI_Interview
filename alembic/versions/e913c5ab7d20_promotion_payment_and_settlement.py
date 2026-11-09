"""Store promotion payout account and commission settlement state."""

import sqlalchemy as sa

from alembic import context, op
from app.db.growth_schema_v2 import (
    BASE_COMMISSION_STATUS_COMMENT,
    COMMISSION_STATUS_COMMENT,
    alipay_account_column,
    settled_at_column,
)

revision = "e913c5ab7d20"
down_revision = "d4e6a8b10321"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if context.is_offline_mode() or "alipay_account_encrypted" not in {
        column["name"] for column in sa.inspect(bind).get_columns("promotion_links")
    }:
        op.add_column("promotion_links", alipay_account_column())
    if context.is_offline_mode() or "settled_at" not in {
        column["name"] for column in sa.inspect(bind).get_columns("promotion_commissions")
    }:
        op.add_column("promotion_commissions", settled_at_column())
    if bind.dialect.name == "mysql":
        op.alter_column(
            "promotion_commissions",
            "status",
            existing_type=sa.String(16),
            existing_nullable=False,
            comment=COMMISSION_STATUS_COMMENT,
        )


def downgrade():
    bind = op.get_bind()
    if context.is_offline_mode() or "settled_at" in {
        column["name"] for column in sa.inspect(bind).get_columns("promotion_commissions")
    }:
        op.drop_column("promotion_commissions", "settled_at")
    if context.is_offline_mode() or "alipay_account_encrypted" in {
        column["name"] for column in sa.inspect(bind).get_columns("promotion_links")
    }:
        op.drop_column("promotion_links", "alipay_account_encrypted")
    if bind.dialect.name == "mysql":
        op.alter_column(
            "promotion_commissions",
            "status",
            existing_type=sa.String(16),
            existing_nullable=False,
            comment=BASE_COMMISSION_STATUS_COMMENT,
        )
