"""Add Chinese table and column comments without changing business data.

Revision ID: 3b9e7c2a4106
Revises: f4c8a1d2e903
"""

from alembic import context, op
from app.db.schema_docs_v1 import comment_alter_statements

revision = "3b9e7c2a4106"
down_revision = "f4c8a1d2e903"
branch_labels = None
depends_on = None


def apply_comments(clear=False):
    if op.get_bind().dialect.name == "mysql":
        connection = None if context.is_offline_mode() else op.get_bind()
        for statement in comment_alter_statements(connection, clear=clear):
            op.execute(statement)


def upgrade():
    apply_comments()


def downgrade():
    apply_comments(clear=True)
