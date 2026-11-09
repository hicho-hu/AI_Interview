"""Verified email accounts, invitation rewards and promotion commissions.
Revision ID: c83f5a7e0203
Revises: b72e4f6d9102
"""

import sqlalchemy as sa

from alembic import op
from app.db.growth_schema_v1 import define_tables

revision = "c83f5a7e0203"
down_revision = "b72e4f6d9102"
branch_labels = None
depends_on = None


def schema():
    metadata = sa.MetaData()
    for name in ("users", "media_assets", "usage_ledger", "admin_users", "orders"):
        sa.Table(name, metadata, sa.Column("id", sa.String(36), primary_key=True))
    names = {table.name for table in define_tables(metadata)}
    return [table for table in metadata.sorted_tables if table.name in names]


def upgrade():
    for table in schema():
        op.create_table(
            table.name,
            *[column._copy() for column in table.columns],
            *[
                sa.UniqueConstraint(*[column.name for column in constraint.columns], name=constraint.name)
                for constraint in list(table.constraints)
                if isinstance(constraint, sa.UniqueConstraint)
            ],
            *[
                sa.ForeignKeyConstraint(
                    [column.name for column in constraint.columns],
                    [element.target_fullname for element in constraint.elements],
                    name=constraint.name,
                )
                for constraint in list(table.constraints)
                if isinstance(constraint, sa.ForeignKeyConstraint)
            ],
            comment=table.comment,
            **dict(table.kwargs),
        )
        for index in table.indexes:
            op.create_index(index.name, table.name, [column.name for column in index.columns])


def downgrade():
    for table in reversed(schema()):
        op.drop_table(table.name)
