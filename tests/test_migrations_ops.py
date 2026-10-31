import io
from pathlib import Path

from alembic.config import Config
from sqlalchemy import inspect

from alembic import command
from app.core.config import Settings
from app.db.repository import Database
from tests.conftest import data, login


def test_migration_round_trip_and_postgres_sql(tmp_path):
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    url = f"sqlite:///{tmp_path}/migrate.db"
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    db = Database(Settings(database_url=url))
    assert len(inspect(db.engine).get_table_names()) == 18
    command.check(config)
    command.downgrade(config, "base")
    assert inspect(db.engine).get_table_names() == ["alembic_version"]
    command.upgrade(config, "head")
    db.engine.dispose()
    output = io.StringIO()
    config.output_buffer = output
    config.attributes["database_url"] = "postgresql+psycopg://test:test@localhost/test"
    command.upgrade(config, "head", sql=True)
    sql = output.getvalue()
    assert "JSONB" in sql
    assert sql.count("ADD CONSTRAINT fk_interview_resume") == 1
    assert sql.count("ADD CONSTRAINT fk_turn_answer") == 1


def test_admin_feedback_health_and_validation(client):
    headers, uid = login(client)
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200
    assert client.get("/api/v1/admin/stats", headers=headers).status_code == 403
    item = data(
        client.post(
            "/api/v1/feedback", headers=headers, json={"category": "REPORT", "content": "评分建议待核验"}
        )
    )
    client.app.state.runtime.settings.admin_user_ids = uid
    data(
        client.patch(
            "/api/v1/admin/feedback/" + item["id"],
            headers=headers,
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
        data(client.get(path, headers=headers))
    assert client.post("/api/v1/interviews", headers=headers, json={}).status_code == 422
    assert client.get("/api/v1/interviews?page_size=101", headers=headers).status_code == 422
    response = client.get("/not-found")
    assert response.status_code == 404 and response.json()["request_id"]
