"""Separate TEXT/VOICE/VIDEO entitlements while preserving historical universal credits.

Revision ID: b72e4f6d9102
Revises: a61d8c4e9201
"""

import sqlalchemy as sa

from alembic import op
from app.db.entitlement_schema_v1 import define_tables, product_credits_column

revision = "b72e4f6d9102"
down_revision = "a61d8c4e9201"
branch_labels = None
depends_on = None


def upgrade():
    metadata = sa.MetaData()
    for name in ("users", "usage_ledger"):
        sa.Table(name, metadata, sa.Column("id", sa.String(36), primary_key=True))
    for table in define_tables(metadata):
        op.create_table(
            table.name,
            *[column._copy() for column in table.columns],
            *[
                sa.UniqueConstraint(*[column.name for column in constraint.columns], name=constraint.name)
                for constraint in list(table.constraints)
                if isinstance(constraint, sa.UniqueConstraint)
            ],
            *[
                sa.CheckConstraint(str(constraint.sqltext), name=constraint.name)
                for constraint in list(table.constraints)
                if isinstance(constraint, sa.CheckConstraint)
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
    op.add_column("product_skus", product_credits_column())


def downgrade():
    op.drop_column("product_skus", "interview_credits")
    op.drop_table("usage_credit_allocations")
    op.drop_table("interview_credit_balances")
