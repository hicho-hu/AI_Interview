import io
import os
import re
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import inspect, text

from alembic import command
from app.db.models import Base
from app.db.schema_docs_v1 import COLUMN_DOCS, TABLE_COMMENTS, comment_alter_statements
from scripts.export_schema_sql import render_bootstrap_sql, render_comments_sql

ROOT = Path(__file__).parents[1]


def test_every_table_and_field_has_a_chinese_comment():
    assert set(COLUMN_DOCS) <= set(Base.metadata.tables) | {"alembic_version"}
    for table in Base.metadata.tables.values():
        assert re.search("[\u4e00-\u9fff]", table.comment)
        for column in table.c:
            assert re.search("[\u4e00-\u9fff]", column.comment)
    assert sum(len(columns) for columns in COLUMN_DOCS.values()) == 185
    for name, columns in COLUMN_DOCS.items():
        assert re.search("[\u4e00-\u9fff]", TABLE_COMMENTS[name])
        for key, spec in columns.items():
            assert re.search("[\u4e00-\u9fff]", spec.comment), (name, key)
            assert len(spec.comment.encode("utf-8")) <= 1024
        if name in Base.metadata.tables:
            table = Base.metadata.tables[name]
            assert table.comment == TABLE_COMMENTS[name]
            for column in table.c:
                assert column.comment == columns[column.name].comment or (name, column.name) in (
                    ("users", "status"),
                    ("user_consents", "consent_type"),
                )


def test_sql_exports_have_complete_comments_and_no_destructive_updates():
    bootstrap = render_bootstrap_sql()
    updates = render_comments_sql()
    assert (ROOT / "docs/sql/ai_interview_mysql.sql").read_text() == bootstrap
    assert (ROOT / "docs/sql/update_ai_interview_mysql_comments.sql").read_text() == updates
    assert (
        len(re.findall(r"\bCOMMENT\s+\'", bootstrap))
        == sum(len(t.c) for t in Base.metadata.tables.values()) + 1
    )
    executable = re.sub(r"^\s*--[^\n]*", "", updates, flags=re.M)
    assert len(re.findall(r"\bMODIFY COLUMN\b", executable)) == 185
    assert len(re.findall(r"^ALTER TABLE ", updates, re.M)) == 18
    assert len(comment_alter_statements()) == 18
    assert not re.search(r"\b(DROP|TRUNCATE|DELETE|INSERT|CREATE|ADD COLUMN)\b", executable)
    assert re.findall(r"UPDATE\s+(\w+)", executable) == ["alembic_version"]
    assert "FIRST" not in executable and "AFTER" not in executable


def test_incremental_export_matches_alembic_comment_migration():
    output = io.StringIO()
    config = Config(str(ROOT / "alembic.ini"), output_buffer=output)
    config.attributes["database_url"] = "mysql+pymysql://test:test@localhost/test"
    command.upgrade(config, "f4c8a1d2e903:3b9e7c2a4106", sql=True)
    migrated = output.getvalue()
    assert migrated.count("MODIFY COLUMN") == 185
    assert migrated.count("ALTER TABLE ") == 18
    for statement in comment_alter_statements():
        assert statement in migrated


MYSQL = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="requires MySQL test server")


def mysql_config(client):
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["database_url"] = client.app.state.runtime.settings.database_url
    return config


def execute_sql(connection, sql):
    executable = re.sub(r"^\s*--[^\n]*", "", sql, flags=re.M)
    for statement in executable.split(";"):
        if statement.strip():
            connection.execute(text(statement))


def column_metadata(connection):
    fields = (
        "TABLE_NAME",
        "COLUMN_NAME",
        "COLUMN_TYPE",
        "IS_NULLABLE",
        "COLUMN_DEFAULT",
        "EXTRA",
        "CHARACTER_SET_NAME",
        "COLLATION_NAME",
        "COLUMN_KEY",
        "ORDINAL_POSITION",
        "GENERATION_EXPRESSION",
    )
    rows = connection.execute(
        text(
            "SELECT "
            + ", ".join(fields)
            + " FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() ORDER BY TABLE_NAME, ORDINAL_POSITION"
        )
    )
    return [tuple(row) for row in rows if row[0] in COLUMN_DOCS]


def assert_comments(connection):
    inspector = inspect(connection)
    for name, columns in COLUMN_DOCS.items():
        assert inspector.get_table_comment(name)["text"] == TABLE_COMMENTS[name]
        for column in inspector.get_columns(name):
            assert column["comment"] == columns[column["name"]].comment or (name, column["name"]) in (
                ("users", "status"),
                ("user_consents", "consent_type"),
            )


@MYSQL
def test_mysql_existing_rows_and_column_properties_survive_comments_update(client):
    config = mysql_config(client)
    rt = client.app.state.runtime
    command.downgrade(config, "f4c8a1d2e903")
    with rt.db.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, openid_hash, nickname, status, credits, created_at, updated_at) "
                "VALUES ('comment-check', 'comment-check-hash', '中文🙂用户', 'ACTIVE', 7, "
                "'2026-11-01 08:01:02.345678', '2026-11-01 08:01:02.345678')"
            )
        )
        before = column_metadata(connection)
        row_before = tuple(connection.execute(text("SELECT * FROM users WHERE id='comment-check'")).one())
        before_indexes = {name: inspect(connection).get_indexes(name) for name in COLUMN_DOCS}
        before_fks = {name: inspect(connection).get_foreign_keys(name) for name in COLUMN_DOCS}
    with rt.db.engine.begin() as connection:
        execute_sql(connection, render_comments_sql())
    with rt.db.engine.connect() as connection:
        assert column_metadata(connection) == before
        assert (
            tuple(connection.execute(text("SELECT * FROM users WHERE id='comment-check'")).one())
            == row_before
        )
        assert_comments(connection)
        assert {name: inspect(connection).get_indexes(name) for name in COLUMN_DOCS} == before_indexes
        assert {name: inspect(connection).get_foreign_keys(name) for name in COLUMN_DOCS} == before_fks
    # At this point only the historical COMMENT revision is applied.
    # The standalone SQL is repeatable and remains compatible with subsequent Alembic invocations.
    with rt.db.engine.begin() as connection:
        execute_sql(connection, render_comments_sql())
    command.upgrade(config, "head")


@MYSQL
def test_mysql_online_comment_migration_preserves_custom_defaults_and_collation(client):
    config = mysql_config(client)
    rt = client.app.state.runtime
    command.downgrade(config, "f4c8a1d2e903")
    with rt.db.engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE users MODIFY COLUMN nickname VARCHAR(80) "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL DEFAULT '自定义昵称'"
            )
        )
        before = column_metadata(connection)
    command.upgrade(config, "head")
    with rt.db.engine.connect() as connection:
        assert column_metadata(connection) == before
        assert_comments(connection)
