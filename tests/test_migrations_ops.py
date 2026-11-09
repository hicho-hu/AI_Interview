import io
from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command
from app.core.config import Settings
from app.db.models import Base
from app.db.repository import Database
from scripts.export_schema_sql import render_bootstrap_sql
from tests.conftest import data, login
from tests.test_admin import admin_login


def test_migration_round_trip_and_mysql_sql(tmp_path):
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    url = f"sqlite:///{tmp_path}/migrate.db"
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    db = Database(Settings(_env_file=None, database_url=url))
    assert len(inspect(db.engine).get_table_names()) == len(Base.metadata.tables) + 1
    command.check(config)
    command.downgrade(config, "base")
    assert inspect(db.engine).get_table_names() == ["alembic_version"]
    command.upgrade(config, "head")
    db.engine.dispose()
    output = io.StringIO()
    config.output_buffer = output
    config.attributes["database_url"] = "mysql+pymysql://test:test@localhost/test?charset=utf8mb4"
    command.upgrade(config, "head", sql=True)
    sql = output.getvalue()
    assert "JSONB" not in sql and "JSON NOT NULL" in sql
    assert "DATETIME(6)" in sql and "LONGTEXT" in sql
    assert sql.count("ENGINE=InnoDB") == len(Base.metadata.tables)
    assert sql.count("COLLATE utf8mb4_0900_bin") == len(Base.metadata.tables)
    assert "RETURNING" not in sql
    assert sql.count("ADD CONSTRAINT fk_interview_resume") == 1
    assert sql.count("ADD CONSTRAINT fk_turn_answer") == 1
    assert sql.count("system_avatar_key VARCHAR(16)") == 1
    assert sql.count("alipay_account_encrypted LONGTEXT") == 1
    assert sql.count("settled_at DATETIME(6)") == 1
    exported = (Path(__file__).parents[1] / "docs/sql/ai_interview_mysql.sql").read_text(encoding="utf-8")
    import re

    def normalized(value):
        value = re.sub(r"^\s*--[^\n]*", "", value, flags=re.M)
        return re.sub(r"\s+", " ", value).strip()

    # The bootstrap emits comments inline; Alembic retains historical migrations and adds comments at head.
    assert normalized(exported) == normalized(render_bootstrap_sql())
    assert (
        len(re.findall(r"CREATE TABLE ", exported))
        == len(re.findall(r"CREATE TABLE ", sql))
        == len(Base.metadata.tables) + 1
    )
    output = io.StringIO()
    config.output_buffer = output
    command.downgrade(config, "head:base", sql=True)
    assert output.getvalue().count("DROP TABLE ") == len(Base.metadata.tables) + 1
    assert "DROP INDEX" not in output.getvalue()


def test_growth_columns_belong_to_their_own_migration_revisions(tmp_path):
    url = f"sqlite:///{tmp_path}/growth-revisions.db"
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.attributes["database_url"] = url
    engine = create_engine(url)

    def columns(table):
        return {column["name"] for column in inspect(engine).get_columns(table)}

    try:
        command.upgrade(config, "c83f5a7e0203")
        assert "system_avatar_key" not in columns("user_accounts")
        assert "alipay_account_encrypted" not in columns("promotion_links")
        assert "settled_at" not in columns("promotion_commissions")
        command.upgrade(config, "d4e6a8b10321")
        assert "system_avatar_key" in columns("user_accounts")
        assert "alipay_account_encrypted" not in columns("promotion_links")
        command.upgrade(config, "head")
        assert "alipay_account_encrypted" in columns("promotion_links")
        assert "settled_at" in columns("promotion_commissions")
        command.check(config)
        command.downgrade(config, "c83f5a7e0203")
        assert "system_avatar_key" not in columns("user_accounts")
        assert "alipay_account_encrypted" not in columns("promotion_links")
        assert "settled_at" not in columns("promotion_commissions")
    finally:
        engine.dispose()


def test_admin_feedback_health_and_validation(client):
    headers, uid = login(client)
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200
    assert client.get("/api/v1/admin/stats", headers=headers).status_code == 401
    item = data(
        client.post(
            "/api/v1/feedback", headers=headers, json={"category": "REPORT", "content": "评分建议待核验"}
        )
    )
    admin_headers, _ = admin_login(client)
    data(
        client.patch(
            "/api/v1/admin/feedback/" + item["id"],
            headers=admin_headers,
            json={"status": "RESOLVED", "reply": "已核查并补充说明"},
        )
    )
    assert data(client.get("/api/v1/feedback", headers=headers))["items"][0]["reply"] == "已核查并补充说明"
    for path in (
        "/api/v1/admin/stats",
        "/api/v1/admin/ai-calls",
        "/api/v1/admin/audit-logs",
        "/api/v1/users/me/data",
        "/api/v1/billing/products",
        "/api/v1/billing/orders",
        "/api/v1/resumes",
        "/api/v1/job-profiles",
    ):
        data(client.get(path, headers=admin_headers if "/admin/" in path else headers))
    assert client.post("/api/v1/interviews", headers=headers, json={}).status_code == 422
    assert client.get("/api/v1/interviews?page_size=101", headers=headers).status_code == 422
    response = client.get("/not-found")
    assert response.status_code == 404 and response.json()["request_id"]
