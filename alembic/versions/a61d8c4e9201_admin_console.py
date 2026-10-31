"""Independent admin identity, approvals, products, reviews and finance metadata.

Revision ID: a61d8c4e9201
Revises: 3b9e7c2a4106
"""

import sqlalchemy as sa
from sqlalchemy.schema import CreateColumn

from alembic import context, op
from app.db.admin_schema_v1 import define_tables

revision = "a61d8c4e9201"
down_revision = "3b9e7c2a4106"
branch_labels = None
depends_on = None


def schema():
    metadata = sa.MetaData()
    # Foreign key targets already exist; placeholders are never created by this migration.
    for name in ("users", "orders", "interviews"):
        sa.Table(name, metadata, sa.Column("id", sa.String(36), primary_key=True))
    names = define_tables(metadata)
    return [t for t in metadata.sorted_tables if t.name in names]


def upgrade():
    for table in schema():
        op.create_table(
            table.name,
            *[c._copy() for c in table.columns],
            *[
                sa.UniqueConstraint(*[col.name for col in c.columns], name=c.name)
                for c in list(table.constraints)
                if isinstance(c, sa.UniqueConstraint) and c.name
            ],
            *[
                sa.ForeignKeyConstraint(
                    [col.name for col in c.columns],
                    [element.target_fullname for element in c.elements],
                    name=c.name,
                )
                for c in list(table.constraints)
                if isinstance(c, sa.ForeignKeyConstraint)
            ],
            comment=table.comment,
            **dict(table.kwargs),
        )
        for index in table.indexes:
            op.create_index(index.name, table.name, [c.name for c in index.columns], unique=index.unique)
    apply_business_comments()


def apply_business_comments(reverse=False):
    from app.db.schema_docs_v1 import COLUMN_DOCS, baseline_table

    connection = op.get_bind()
    if connection.dialect.name != "mysql":
        return
    changes = {
        ("users", "status"): "账号状态：ACTIVE=正常，FROZEN=冻结，DELETING=删除中，DELETED=已删除",
        (
            "user_consents",
            "consent_type",
        ): "授权类型：PRIVACY/TERMS/AI_PROCESSING/MICROPHONE/CAMERA/SCREEN_CAPTURE；SUPPORT_REVIEW=后台人工审阅授权",
    }
    for (name, key), comment in changes.items():
        table = (
            baseline_table(name)
            if context.is_offline_mode()
            else sa.Table(name, sa.MetaData(), autoload_with=connection, resolve_fks=False)
        )
        column = table.c[key]
        column.comment = COLUMN_DOCS[name][key].comment if reverse else comment
        definition = str(CreateColumn(column).compile(dialect=connection.dialect)).strip()
        quoted = connection.dialect.identifier_preparer.quote_identifier(name)
        op.execute(f"ALTER TABLE {quoted} MODIFY COLUMN {definition}")


def downgrade():
    apply_business_comments(reverse=True)
    for table in reversed(schema()):
        op.drop_table(table.name)
