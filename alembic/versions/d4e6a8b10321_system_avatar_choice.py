"""Persist user's selected system avatar."""

import sqlalchemy as sa

from alembic import context, op
from app.db.growth_schema_v2 import system_avatar_column

revision = "d4e6a8b10321"
down_revision = "c83f5a7e0203"
branch_labels = None
depends_on = None


def upgrade():
    if context.is_offline_mode() or "system_avatar_key" not in {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("user_accounts")
    }:
        op.add_column("user_accounts", system_avatar_column())


def downgrade():
    if context.is_offline_mode() or "system_avatar_key" in {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("user_accounts")
    }:
        op.drop_column("user_accounts", "system_avatar_key")
